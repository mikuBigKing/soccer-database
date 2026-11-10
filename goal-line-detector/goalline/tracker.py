"""Minimal constant-velocity Kalman tracker for a single ball.

The detector runs at a few tens of FPS on a fast-moving small object, so short
detection gaps (motion blur, occlusion by the goalkeeper or a post) are normal.
The tracker bridges those gaps and keeps a sub-frame accurate trajectory, which
the judge later uses to interpolate the exact crossing moment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import TrackingConfig
from .detector import BallDetection


@dataclass
class TrackState:
    frame_idx: int
    time_s: float
    x: float
    y: float
    radius: float
    score: float
    detected: bool
    valid: bool


class BallTracker:
    def __init__(self, fps: float, cfg: TrackingConfig | None = None) -> None:
        self.cfg = cfg or TrackingConfig()
        self.dt = 1.0 / max(fps, 1e-3)
        self._x: np.ndarray | None = None
        self._p: np.ndarray | None = None
        self._radius = 0.0
        self._missed = 0
        self._last_score = 0.0

    def reset(self) -> None:
        self._x = None
        self._p = None
        self._radius = 0.0
        self._missed = 0
        self._last_score = 0.0

    def _init(self, det: BallDetection) -> None:
        self._x = np.array([det.cx, det.cy, 0.0, 0.0], dtype=float)
        self._p = np.diag([25.0, 25.0, 2500.0, 2500.0])
        self._radius = det.radius

    def _predict(self) -> np.ndarray:
        dt = self.dt
        f = np.array(
            [[1.0, 0.0, dt, 0.0], [0.0, 1.0, 0.0, dt], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
        )
        q = self.cfg.process_noise**2
        q_mat = q * np.array(
            [
                [dt**4 / 4, 0.0, dt**3 / 2, 0.0],
                [0.0, dt**4 / 4, 0.0, dt**3 / 2],
                [dt**3 / 2, 0.0, dt**2, 0.0],
                [0.0, dt**3 / 2, 0.0, dt**2],
            ]
        )
        self._x = f @ self._x
        self._p = f @ self._p @ f.T + q_mat
        return self._x

    def _update(self, det: BallDetection) -> None:
        h = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
        r = np.eye(2) * self.cfg.measurement_noise**2
        z = np.array([det.cx, det.cy])
        y = z - h @ self._x
        s = h @ self._p @ h.T + r
        k = self._p @ h.T @ np.linalg.inv(s)
        self._x = self._x + k @ y
        self._p = (np.eye(4) - k @ h) @ self._p
        # Radius is a slowly varying nuisance parameter; EMA is enough.
        self._radius = 0.7 * self._radius + 0.3 * det.radius

    def update(self, frame_idx: int, time_s: float, det: BallDetection | None) -> TrackState:
        if self._x is None:
            if det is None:
                return TrackState(frame_idx, time_s, 0.0, 0.0, 0.0, 0.0, False, False)
            self._init(det)
            self._last_score = det.score
            return TrackState(frame_idx, time_s, det.cx, det.cy, det.radius, det.score, True, True)

        pred = self._predict()

        if det is not None:
            dist = float(np.hypot(det.cx - pred[0], det.cy - pred[1]))
            if dist > self.cfg.gate_px:
                # Too far from the prediction: most likely a different object or a
                # re-appearing ball after a long gap. Restart the filter.
                self._init(det)
                self._last_score = det.score
                return TrackState(frame_idx, time_s, det.cx, det.cy, det.radius, det.score, True, True)
            self._update(det)
            self._missed = 0
            self._last_score = det.score
            return TrackState(
                frame_idx, time_s, float(self._x[0]), float(self._x[1]), self._radius, det.score, True, True
            )

        self._missed += 1
        if self._missed > self.cfg.max_missed:
            self.reset()
            return TrackState(frame_idx, time_s, 0.0, 0.0, 0.0, 0.0, False, False)

        return TrackState(
            frame_idx,
            time_s,
            float(self._x[0]),
            float(self._x[1]),
            self._radius,
            self._last_score,
            False,
            True,
        )

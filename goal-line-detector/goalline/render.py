"""Overlay rendering for the verification video."""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from .config import GoalLineConfig
from .detector import BallDetection
from .judge import FrameVerdict
from .tracker import TrackState

_GREEN = (0, 200, 0)
_YELLOW = (0, 220, 255)
_RED = (0, 0, 255)
_WHITE = (255, 255, 255)
_GREY = (160, 160, 160)


class Renderer:
    def __init__(self, line, cfg: GoalLineConfig, trail_length: int = 40) -> None:
        self.line = line
        self.cfg = cfg
        self.trail: deque[tuple[float, float]] = deque(maxlen=trail_length)
        self._goal_flash_frames = 0
        self._shade_cache: tuple[int, int, np.ndarray | None] = (-1, -1, None)

    def draw(
        self,
        frame: np.ndarray,
        track: TrackState | None,
        det: BallDetection | None,
        verdict: FrameVerdict,
    ) -> np.ndarray:
        h, w = frame.shape[:2]
        out = frame.copy()

        quad = self._shade(h, w)
        if quad is not None:
            overlay = out.copy()
            cv2.fillPoly(overlay, [quad], (40, 180, 40))
            cv2.addWeighted(overlay, 0.22, out, 0.78, 0, out)

        pts = self.line.points.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], False, _YELLOW, 3, cv2.LINE_AA)
        for p in self.line.points:
            cv2.circle(out, (int(p[0]), int(p[1])), 4, _YELLOW, -1, cv2.LINE_AA)
        cv2.circle(out, (int(self.line.inside_point[0]), int(self.line.inside_point[1])), 5, (255, 0, 255), -1)
        cv2.putText(out, "goal side", (int(self.line.inside_point[0]) + 8, int(self.line.inside_point[1]) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1, cv2.LINE_AA)

        if track is not None and track.valid:
            self.trail.append((track.x, track.y))
        for i in range(1, len(self.trail)):
            a = (int(self.trail[i - 1][0]), int(self.trail[i - 1][1]))
            b = (int(self.trail[i][0]), int(self.trail[i][1]))
            alpha = i / len(self.trail)
            color = (int(60 * alpha), int(140 * alpha), int(255 * alpha))
            cv2.line(out, a, b, color, 2, cv2.LINE_AA)

        if track is not None and track.valid:
            color = _GREEN if track.detected else _GREY
            center = (int(track.x), int(track.y))
            cv2.circle(out, center, max(int(track.radius), 3), color, 2, cv2.LINE_AA)
            cv2.drawMarker(out, center, color, cv2.MARKER_CROSS, 10, 1)
            if det is not None:
                x1, y1, x2, y2 = (int(v) for v in det.box)
                cv2.rectangle(out, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)

        status = "GOAL CONFIRMED" if verdict.goal_confirmed else verdict.state.upper()
        if verdict.goal_confirmed:
            self._goal_flash_frames = 20
        status_color = _RED if verdict.state == "goal" else _WHITE
        if self._goal_flash_frames > 0:
            self._goal_flash_frames -= 1
            cv2.rectangle(out, (2, 2), (w - 3, h - 3), _RED, 6)

        lines = [
            f"frame {verdict.frame_idx}  t={verdict.time_s:.2f}s",
            f"depth {verdict.inside_dist:+.1f}px / need {verdict.required_depth:.1f}px",
            f"t_on_line {verdict.t_on_line:.3f}   state: {status}",
        ]
        if track is not None and track.valid:
            lines.append(f"conf {track.score:.2f}  r={track.radius:.1f}px")

        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thickness = 0.55, 1
        block_w = max(cv2.getTextSize(text, font, scale, thickness)[0][0] for text in lines) + 16
        block_h = 22 * len(lines) + 10
        panel = out.copy()
        cv2.rectangle(panel, (6, 6), (6 + block_w, 6 + block_h), (15, 15, 15), -1)
        cv2.addWeighted(panel, 0.7, out, 0.3, 0, out)

        y = 30
        for text in lines:
            cv2.putText(out, text, (14, y), font, scale, status_color, 1, cv2.LINE_AA)
            y += 22

        if self._goal_flash_frames > 0:
            banner = "GOAL"
            size = cv2.getTextSize(banner, cv2.FONT_HERSHEY_DUPLEX, 1.8, 5)[0]
            pos = (int(w / 2 - size[0] / 2), h - 22)
            cv2.putText(out, banner, pos, cv2.FONT_HERSHEY_DUPLEX, 1.8, _RED, 7, cv2.LINE_AA)
            cv2.putText(out, banner, pos, cv2.FONT_HERSHEY_DUPLEX, 1.8, _WHITE, 2, cv2.LINE_AA)

        return out

    def _shade(self, h: int, w: int) -> np.ndarray | None:
        if self._shade_cache[:2] != (h, w):
            self._shade_cache = (h, w, self.line.shaded_polygon(w, h, depth=50.0))
        return self._shade_cache[2]

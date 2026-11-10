"""Goal decision: geometry + multi-frame confirmation + crossing-time backtracking.

A goal is only confirmed after the ball has been "over the line" for N
consecutive tracked frames (predicted frames included, but at least
``min_visible_in_confirm`` real detections must be part of the window).  Once
confirmed, the system backtracks the last field-side sample and interpolates the
moment the ball centre crossed the line, which is more accurate than reporting
the confirmation frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import JudgeConfig
from .geometry import GoalLine
from .tracker import TrackState


@dataclass
class FrameVerdict:
    frame_idx: int
    time_s: float
    inside_dist: float
    t_on_line: float
    required_depth: float
    over_line: bool
    state: str
    goal_confirmed: bool = False
    event_id: int | None = None


@dataclass
class _Sample:
    frame_idx: int
    time_s: float
    x: float
    y: float
    inside_dist: float
    t_on_line: float
    valid: bool
    detected: bool
    over_line: bool


class GoalJudge:
    def __init__(self, line: GoalLine, cfg: JudgeConfig, fps: float) -> None:
        self.line = line
        self.cfg = cfg
        self.fps = fps
        self.state = "armed"
        self.events: list[dict] = []
        self._samples: list[_Sample] = []
        self._streak = 0
        self._visible_in_streak = 0
        self._out_streak = 0
        self._last_goal_time = -1e9
        self._event_seq = 0

    def step(self, track: TrackState | None) -> FrameVerdict:
        if track is None or not track.valid:
            sample = _Sample(
                frame_idx=-1 if track is None else track.frame_idx,
                time_s=0.0 if track is None else track.time_s,
                x=0.0,
                y=0.0,
                inside_dist=float("-inf"),
                t_on_line=-1.0,
                valid=False,
                detected=False,
                over_line=False,
            )
        else:
            inside_dist, t_on_line, _, _ = self.line.locate((track.x, track.y))
            required = self.line.required_depth(track.radius, self.cfg)
            in_span = (-self.cfg.t_margin) <= t_on_line <= (1.0 + self.cfg.t_margin)
            over = in_span and inside_dist >= required
            sample = _Sample(
                track.frame_idx,
                track.time_s,
                track.x,
                track.y,
                inside_dist,
                t_on_line,
                True,
                track.detected,
                over,
            )

        self._samples.append(sample)
        if len(self._samples) > 240:
            self._samples = self._samples[-240:]

        confirmed = False
        event_id: int | None = None

        if self.state == "armed":
            if sample.over_line:
                self._streak += 1
                if sample.detected:
                    self._visible_in_streak += 1
                if (
                    self._streak >= self.cfg.confirm_frames
                    and self._visible_in_streak >= self.cfg.min_visible_in_confirm
                ):
                    event = self._emit_goal(sample)
                    confirmed = True
                    event_id = event["event_id"]
            else:
                self._streak = 0
                self._visible_in_streak = 0
        else:  # state == "goal": wait until the ball is clearly back out of the zone
            if sample.over_line:
                self._out_streak = 0
            else:
                self._out_streak += 1
            if (
                self._out_streak >= self.cfg.release_frames
                and sample.time_s - self._last_goal_time >= self.cfg.cooldown_s
            ):
                self.state = "armed"
                self._streak = 0
                self._visible_in_streak = 0

        required_depth = (
            self.line.required_depth(track.radius, self.cfg) if sample.valid else 0.0
        )
        return FrameVerdict(
            frame_idx=sample.frame_idx,
            time_s=sample.time_s,
            inside_dist=sample.inside_dist if sample.valid else float("nan"),
            t_on_line=sample.t_on_line,
            required_depth=required_depth,
            over_line=sample.over_line,
            state=self.state,
            goal_confirmed=confirmed,
            event_id=event_id,
        )

    def _emit_goal(self, sample: _Sample) -> dict:
        self._event_seq += 1
        first_over = None
        for s in reversed(self._samples):
            if s.over_line:
                first_over = s
            else:
                break
        first_over = first_over or sample

        cross_time, cross_frame, cross_xy = self._interpolate_crossing(first_over)
        depth = max(s.inside_dist for s in self._samples[-max(self._streak, 1):] if s.valid)

        event = {
            "event_id": self._event_seq,
            "type": "goal",
            "confirmed": True,
            "cross_frame": round(cross_frame, 2),
            "cross_time_s": round(cross_time, 3),
            "confirm_frame": sample.frame_idx,
            "confirm_time_s": round(sample.time_s, 3),
            "max_depth_px": round(float(depth), 2),
            "t_on_line": round(first_over.t_on_line, 4),
            "ball_xy": [round(cross_xy[0], 1), round(cross_xy[1], 1)],
        }
        self.events.append(event)
        self.state = "goal"
        self._out_streak = 0
        self._streak = 0
        self._visible_in_streak = 0
        self._last_goal_time = sample.time_s
        return event

    def _interpolate_crossing(self, first_over: _Sample) -> tuple[float, float, tuple[float, float]]:
        """Interpolate the moment the ball centre crossed the line.

        Walks back from the first over-line sample to the last valid sample that
        was still on the field side, then interpolates between the two.
        """
        idx = next(
            (i for i, s in enumerate(self._samples) if s is first_over),
            len(self._samples) - 1,
        )
        prev_idx = None
        for i in range(idx, -1, -1):
            s = self._samples[i]
            if s.valid and s.inside_dist < 0.0:
                prev_idx = i
                break
        if prev_idx is None:
            return first_over.time_s, float(first_over.frame_idx), (first_over.x, first_over.y)

        before = self._samples[prev_idx]
        after = next(
            (self._samples[k] for k in range(prev_idx + 1, idx + 1) if self._samples[k].valid),
            first_over,
        )
        span = after.inside_dist - before.inside_dist
        frac = 0.0 if span <= 1e-9 else -before.inside_dist / span
        frac = min(max(frac, 0.0), 1.0)
        t = before.time_s + frac * (after.time_s - before.time_s)
        f = before.frame_idx + frac * (after.frame_idx - before.frame_idx)
        x = before.x + frac * (after.x - before.x)
        y = before.y + frac * (after.y - before.y)
        return t, f, (x, y)

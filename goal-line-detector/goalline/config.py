"""Configuration objects for the goal-line detection pipeline.

Everything is JSON so a hand-drawn line config can also be edited by hand.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DetectionConfig:
    """Ball detector settings."""

    threshold: float = 0.35
    min_radius_px: float = 4.0
    max_radius_px: float = 120.0
    # Class names of COCO / fine-tuned models that should be treated as "the ball".
    # Order matters: the first name found in the model label map wins.
    class_names: list[str] = field(
        default_factory=lambda: ["sports ball", "soccer ball", "football", "ball"]
    )


@dataclass
class TrackingConfig:
    """Single-object constant-velocity tracker settings."""

    gate_px: float = 140.0
    max_missed: int = 10
    # Acceleration noise (px / s^2) and measurement noise (px).
    process_noise: float = 60.0
    measurement_noise: float = 8.0


@dataclass
class JudgeConfig:
    """Goal decision settings."""

    # Half thickness of the painted goal line, in pixels of the *input video*.
    line_half_width_px: float = 6.0
    # True  -> the whole ball must cross ("ball fully over the line", FIFA rule).
    # False -> the ball centre crossing is enough.
    require_whole_ball: bool = True
    # Consecutive over-line frames required before a goal is confirmed.
    confirm_frames: int = 3
    # Consecutive out-of-zone frames required before the judge re-arms.
    release_frames: int = 5
    cooldown_s: float = 2.0
    # Allow the projected crossing point to fall slightly outside the drawn segment.
    t_margin: float = 0.01
    # At least this many real (non-predicted) detections must be part of the
    # confirming window, otherwise we are only following a track through occlusion.
    min_visible_in_confirm: int = 1


@dataclass
class GoalLineConfig:
    """Line geometry plus all sub-configs."""

    goal_line: list[list[float]]
    inside_point: list[float]
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    # Written by the labelling tool for reference; not used by the pipeline.
    video_width: int | None = None
    video_height: int | None = None
    source_video: str | None = None

    def validate(self) -> None:
        if len(self.goal_line) < 2:
            raise ValueError("goal_line needs at least two points")
        if len(self.inside_point) != 2:
            raise ValueError("inside_point must be [x, y]")
        for p in self.goal_line:
            if len(p) != 2:
                raise ValueError(f"goal_line point {p!r} must be [x, y]")

    @classmethod
    def from_json(cls, path: str | Path) -> "GoalLineConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = cls(
            goal_line=raw["goal_line"],
            inside_point=raw["inside_point"],
            detection=DetectionConfig(**raw.get("detection", {})),
            tracking=TrackingConfig(**raw.get("tracking", {})),
            judge=JudgeConfig(**raw.get("judge", {})),
            video_width=raw.get("video_width"),
            video_height=raw.get("video_height"),
            source_video=raw.get("source_video"),
        )
        cfg.validate()
        return cfg

    def to_json(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def apply_overrides(cfg: GoalLineConfig, **overrides: Any) -> GoalLineConfig:
    """Apply flat CLI overrides such as ``threshold``/``confirm_frames``."""
    mapping = {
        "threshold": (cfg.detection, "threshold"),
        "min_radius_px": (cfg.detection, "min_radius_px"),
        "max_radius_px": (cfg.detection, "max_radius_px"),
        "line_half_width_px": (cfg.judge, "line_half_width_px"),
        "confirm_frames": (cfg.judge, "confirm_frames"),
        "release_frames": (cfg.judge, "release_frames"),
        "cooldown_s": (cfg.judge, "cooldown_s"),
        "require_whole_ball": (cfg.judge, "require_whole_ball"),
        "gate_px": (cfg.tracking, "gate_px"),
        "max_missed": (cfg.tracking, "max_missed"),
    }
    for key, value in overrides.items():
        if value is None:
            continue
        if key not in mapping:
            raise KeyError(f"unknown override: {key}")
        target, attr = mapping[key]
        setattr(target, attr, value)
    return cfg

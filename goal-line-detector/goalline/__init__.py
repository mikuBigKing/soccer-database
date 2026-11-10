"""Goal-line detection package (RF-DETR + multi-frame goal judgement)."""

from .config import GoalLineConfig
from .judge import GoalJudge
from .pipeline import run_video

__all__ = ["GoalLineConfig", "GoalJudge", "run_video"]

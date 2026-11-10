"""Unit tests for the geometric and multi-frame goal judgement (no model needed)."""

from __future__ import annotations

import unittest

from goalline.config import JudgeConfig
from goalline.geometry import GoalLine
from goalline.judge import GoalJudge
from goalline.tracker import TrackState

FPS = 25.0


def _line() -> GoalLine:
    # Horizontal goal line, goal interior above the line (smaller y).
    return GoalLine([[300, 500], [1300, 500]], [800, 400])


def _run(y_values: list[float], x: float = 800.0, radius: float = 8.0, cfg: JudgeConfig | None = None):
    judge = GoalJudge(_line(), cfg or JudgeConfig(confirm_frames=3, release_frames=5), FPS)
    verdicts = []
    for i, y in enumerate(y_values):
        track = TrackState(i, i / FPS, x, y, radius, 0.9, True, True)
        verdicts.append(judge.step(track))
    return judge, verdicts


class GeometryTest(unittest.TestCase):
    def test_signed_distance_sign(self):
        line = _line()
        self.assertGreater(line.inside_distance((800, 400)), 0)  # goal side
        self.assertLess(line.inside_distance((800, 600)), 0)  # field side
        self.assertAlmostEqual(line.inside_distance((800, 500)), 0.0, places=6)

    def test_t_parameter(self):
        line = _line()
        _, t, _, _ = line.locate((800, 450))
        self.assertAlmostEqual(t, 0.5, places=3)
        _, t_out, _, _ = line.locate((200, 450))
        self.assertLess(t_out, 0.05)


class JudgeTest(unittest.TestCase):
    def test_goal_confirmed_once_with_backtracked_time(self):
        ys = [560 - 20 * i for i in range(12)]
        judge, verdicts = _run(ys)
        self.assertEqual(len(judge.events), 1)
        event = judge.events[0]
        # Centre crosses y=500 after 3 frames; whole-ball depth needs 12 px.
        self.assertAlmostEqual(event["cross_frame"], 3.0, delta=0.5)
        self.assertEqual(event["confirm_frame"], 6)
        self.assertEqual(sum(v.goal_confirmed for v in verdicts), 1)

    def test_no_goal_when_ball_stays_on_field_side(self):
        ys = [560 - 2 * i for i in range(25)]  # ends at 512, never reaches the line
        judge, _ = _run(ys)
        self.assertEqual(judge.events, [])

    def test_no_goal_outside_goal_mouth(self):
        ys = [560 - 20 * i for i in range(12)]
        judge, _ = _run(ys, x=100.0)  # far left of the drawn segment
        self.assertEqual(judge.events, [])

    def test_predicted_frames_alone_do_not_confirm(self):
        judge = GoalJudge(_line(), JudgeConfig(confirm_frames=3, min_visible_in_confirm=1), FPS)
        for i in range(8):
            y = 470.0
            judge.step(TrackState(i, i / FPS, 800, y, 8.0, 0.0, False, True))
        self.assertEqual(judge.events, [])

    def test_rearm_after_leaving_zone(self):
        ys = [560 - 20 * i for i in range(10)]
        ys += [480 + 20 * i for i in range(1, 10)]  # ball leaves the goal
        ys += [560 - 20 * i for i in range(10)]  # second approach
        cfg = JudgeConfig(confirm_frames=3, release_frames=5, cooldown_s=0.0)
        judge, _ = _run(ys, cfg=cfg)
        self.assertEqual(len(judge.events), 2)


if __name__ == "__main__":
    unittest.main()

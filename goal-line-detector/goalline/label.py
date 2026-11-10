"""Interactive goal-line labelling on a single video frame (requires a display)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import GoalLineConfig


def label_line(video_path: str | Path, out_json: str | Path, frame_index: int = 0) -> GoalLineConfig:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    if frame_index > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = cap.read()
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read frame {frame_index} from {video_path}")

    h, w = frame.shape[:2]
    points: list[tuple[int, int]] = []
    inside: tuple[int, int] | None = None
    stage = "line"

    def on_mouse(event, x, y, flags, _param):
        nonlocal inside
        if event == cv2.EVENT_LBUTTONDOWN:
            if stage == "line":
                points.append((x, y))
            elif stage == "inside":
                inside = (x, y)
        elif event == cv2.EVENT_RBUTTONDOWN and stage == "line" and points:
            points.pop()

    window = "goal-line labeler: L-click add, R-click undo, ENTER next, ESC cancel"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)

    print("Step 1: left-click 2+ points along the goal line (goal mouth), right-click undoes.")
    print("Step 2: press ENTER, then left-click one point inside the goal.")
    print("Step 3: press ENTER to save, ESC to abort.")

    while True:
        canvas = frame.copy()
        if len(points) >= 2:
            cv2.polylines(canvas, [np.array(points, np.int32)], False, (0, 220, 255), 2, cv2.LINE_AA)
        for p in points:
            cv2.circle(canvas, p, 4, (0, 0, 255), -1, cv2.LINE_AA)
        if inside is not None:
            cv2.circle(canvas, inside, 5, (255, 0, 255), -1, cv2.LINE_AA)
            cv2.putText(canvas, "inside", (inside[0] + 8, inside[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1, cv2.LINE_AA)
        hint = f"stage: {stage}  points: {len(points)}  (ENTER=next/confirm, ESC=cancel)"
        cv2.putText(canvas, hint, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, hint, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(window, canvas)
        key = cv2.waitKey(20) & 0xFF

        if key == 27:
            cv2.destroyAllWindows()
            raise SystemExit("aborted")
        if key in (13, 10, 32):
            if stage == "line":
                if len(points) < 2:
                    print("need at least two line points")
                    continue
                stage = "inside"
            else:
                if inside is None:
                    print("click one point inside the goal first")
                    continue
                break

    cv2.destroyAllWindows()

    out_path = Path(out_json)
    if out_path.exists():
        cfg = GoalLineConfig.from_json(out_path)
    else:
        cfg = GoalLineConfig(goal_line=[], inside_point=[])
    cfg.goal_line = [[float(x), float(y)] for x, y in points]
    cfg.inside_point = [float(inside[0]), float(inside[1])]
    cfg.video_width = w
    cfg.video_height = h
    cfg.source_video = str(video_path)
    cfg.validate()
    cfg.to_json(out_path)
    print(f"saved {out_path}  fps={fps:.2f}  frame={w}x{h}")
    return cfg

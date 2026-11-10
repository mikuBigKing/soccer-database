"""Offline verification pipeline: video -> detections -> track -> verdict -> artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np

from .config import GoalLineConfig
from .detector import BallDetector
from .geometry import GoalLine
from .judge import GoalJudge
from .render import Renderer
from .tracker import BallTracker


def run_video(
    video_path: str | Path,
    cfg: GoalLineConfig,
    out_dir: str | Path,
    detector: BallDetector,
    batch_size: int = 8,
    start_s: float = 0.0,
    limit_frames: int | None = None,
    show: bool = False,
    progress_every: int = 60,
) -> dict:
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if start_s > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000.0)
    frame_idx = int(round(start_s * fps))

    line = GoalLine(cfg.goal_line, cfg.inside_point)
    tracker = BallTracker(fps, cfg.tracking)
    judge = GoalJudge(line, cfg.judge, fps)
    renderer = Renderer(line, cfg)

    writer = cv2.VideoWriter(
        str(out_dir / "annotated.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("cannot create video writer (codec mp4v)")

    csv_file = (out_dir / "frames.csv").open("w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(
        [
            "frame", "time_s", "ball_x", "ball_y", "radius_px", "score", "detected",
            "inside_dist_px", "required_depth_px", "t_on_line", "over_line", "state", "event_id",
        ]
    )

    processed = 0
    try:
        while True:
            if limit_frames is not None and processed >= limit_frames:
                break
            frames_bgr: list[np.ndarray] = []
            for _ in range(batch_size):
                ok, frame = cap.read()
                if not ok:
                    break
                frames_bgr.append(frame)
            if not frames_bgr:
                break

            rgb_batch = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames_bgr]
            detections = detector.predict(rgb_batch)

            for frame_bgr, det in zip(frames_bgr, detections):
                t = frame_idx / fps
                track = tracker.update(frame_idx, t, det)
                verdict = judge.step(track)
                rendered = renderer.draw(frame_bgr, track, det, verdict)
                writer.write(rendered)

                csv_writer.writerow(
                    [
                        frame_idx, round(t, 4),
                        round(track.x, 1) if track.valid else "",
                        round(track.y, 1) if track.valid else "",
                        round(track.radius, 1) if track.valid else "",
                        round(track.score, 4) if track.valid else "",
                        int(track.detected) if track.valid else 0,
                        round(verdict.inside_dist, 1) if track.valid else "",
                        round(verdict.required_depth, 1),
                        round(verdict.t_on_line, 4) if track.valid else "",
                        int(verdict.over_line),
                        verdict.state,
                        verdict.event_id if verdict.event_id is not None else "",
                    ]
                )

                if show:
                    cv2.imshow("goal-line", rendered)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        limit_frames = processed
                        break

                processed += 1
                frame_idx += 1
                if progress_every and processed % progress_every == 0:
                    print(
                        f"  {processed}/{total if total > 0 else '?'} frames "
                        f"({t:.1f}s) goals={len(judge.events)}",
                        flush=True,
                    )
    finally:
        cap.release()
        writer.release()
        csv_file.close()
        if show:
            cv2.destroyAllWindows()

    summary = {
        "video": str(video_path),
        "fps": fps,
        "width": width,
        "height": height,
        "processed_frames": processed,
        "processed_seconds": round(processed / fps, 2) if fps > 0 else None,
        "goal_count": len(judge.events),
        "config": {
            "confirm_frames": cfg.judge.confirm_frames,
            "release_frames": cfg.judge.release_frames,
            "cooldown_s": cfg.judge.cooldown_s,
            "require_whole_ball": cfg.judge.require_whole_ball,
            "line_half_width_px": cfg.judge.line_half_width_px,
            "threshold": cfg.detection.threshold,
        },
    }
    (out_dir / "events.json").write_text(
        json.dumps({"summary": summary, "events": judge.events}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary

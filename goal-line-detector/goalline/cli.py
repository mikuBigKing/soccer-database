"""Command line entry point: ``python -m goalline <label|run>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import GoalLineConfig, apply_overrides
from .detector import BallDetector
from .pipeline import run_video


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goalline", description="Goal-line detection from a top-down goal camera")
    sub = parser.add_subparsers(dest="command", required=True)

    label = sub.add_parser("label", help="interactively draw the goal line on one frame")
    label.add_argument("--video", required=True)
    label.add_argument("--out", required=True, help="output JSON config path")
    label.add_argument("--frame", type=int, default=0)

    run = sub.add_parser("run", help="run the offline verification pipeline")
    run.add_argument("--video", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--out", default="runs/latest")
    run.add_argument("--model", default="small", choices=["nano", "small", "medium", "large"],
                     help="RF-DETR variant used when --checkpoint is not given")
    run.add_argument("--weights", default=None, help="pretrained COCO weights path (default: auto-download)")
    run.add_argument("--checkpoint", default=None, help="fine-tuned ball checkpoint (.pth)")
    run.add_argument("--device", default=None, help="e.g. cuda, cuda:0, cpu")
    run.add_argument("--batch-size", type=int, default=8)
    run.add_argument("--start", type=float, default=0.0, help="start offset in seconds")
    run.add_argument("--limit", type=int, default=None, help="max frames to process")
    run.add_argument("--threshold", type=float, default=None)
    run.add_argument("--confirm-frames", type=int, default=None)
    run.add_argument("--release-frames", type=int, default=None)
    run.add_argument("--cooldown", type=float, default=None)
    run.add_argument("--line-half-width", type=float, default=None)
    run.add_argument("--show", action="store_true", help="preview frames while processing")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "label":
        from .label import label_line

        label_line(args.video, args.out, args.frame)
        return 0

    cfg = GoalLineConfig.from_json(args.config)
    apply_overrides(
        cfg,
        threshold=args.threshold,
        confirm_frames=args.confirm_frames,
        release_frames=args.release_frames,
        cooldown_s=args.cooldown,
        line_half_width_px=args.line_half_width,
    )

    if not Path(args.video).exists():
        print(f"video not found: {args.video}", file=sys.stderr)
        return 2

    detector = BallDetector(
        variant=args.model,
        weights=args.weights,
        checkpoint=args.checkpoint,
        device=args.device,
        cfg=cfg.detection,
    )
    print(f"loaded RF-DETR ({'checkpoint ' + args.checkpoint if args.checkpoint else args.model}), "
          f"ball classes={detector.ball_class_ids}")

    summary = run_video(
        video_path=args.video,
        cfg=cfg,
        out_dir=args.out,
        detector=detector,
        batch_size=args.batch_size,
        start_s=args.start,
        limit_frames=args.limit,
        show=args.show,
    )
    print(
        f"done: {summary['processed_frames']} frames, {summary['goal_count']} goal(s)\n"
        f"artifacts in {Path(args.out).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

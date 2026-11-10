"""Fine-tune RF-DETR on a single-class ball dataset (COCO format).

Dataset layout expected by RF-DETR::

    data/goal_ball/
      train/
        _annotations.coco.json
        *.jpg
      valid/
        _annotations.coco.json
        *.jpg

Usage (8 GB laptop GPU: start with nano/small, batch 2-4):
    python train/finetune.py --dataset data/goal_ball --variant small --epochs 40 --batch-size 4
"""

from __future__ import annotations

import argparse
from pathlib import Path

import rfdetr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="dataset root with train/ and valid/")
    parser.add_argument("--variant", default="small", choices=["nano", "small", "medium", "large"])
    parser.add_argument("--weights", default=None, help="COCO pretrained weights (default: auto-download)")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=1)
    parser.add_argument("--resolution", type=int, default=None, help="must be divisible by 32 (small: 512)")
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--class-name", default="ball")
    parser.add_argument("--output-dir", default="runs/finetune")
    parser.add_argument("--resume", default=None)
    args = parser.parse_args()

    dataset = Path(args.dataset)
    if not (dataset / "train" / "_annotations.coco.json").exists():
        raise SystemExit(f"{dataset} is not a COCO dataset (missing train/_annotations.coco.json)")

    model_cls = {
        "nano": rfdetr.RFDETRNano,
        "small": rfdetr.RFDETRSmall,
        "medium": rfdetr.RFDETRMedium,
        "large": rfdetr.RFDETRLarge,
    }[args.variant]
    model = model_cls(**({"pretrain_weights": args.weights} if args.weights else {}))

    kwargs = dict(
        dataset_dir=str(dataset),
        epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accum_steps=args.grad_accum,
        lr=args.lr,
        output_dir=args.output_dir,
        class_names=[args.class_name],
        # Checkpoints that carry no notes are still usable; this is provenance only.
        notes={"task": "goal-line ball detection", "class": args.class_name},
    )
    if args.resolution:
        kwargs["resolution"] = args.resolution
    if args.resume:
        kwargs["resume"] = args.resume

    model.train(**kwargs)
    print(f"fine-tuning finished, checkpoints in {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

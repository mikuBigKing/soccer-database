"""RF-DETR ball detector wrapper.

Zero-shot usage relies on the COCO ``sports ball`` class of the public
checkpoints.  A fine-tuned checkpoint (single ``ball`` class) can be passed with
``checkpoint=...`` instead and is loaded through ``RFDETR.from_checkpoint``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import DetectionConfig

VARIANT_CLASSES = {
    "nano": "RFDETRNano",
    "small": "RFDETRSmall",
    "medium": "RFDETRMedium",
    "large": "RFDETRLarge",
}


@dataclass
class BallDetection:
    score: float
    cx: float
    cy: float
    radius: float
    box: tuple[float, float, float, float]


class BallDetector:
    def __init__(
        self,
        variant: str = "small",
        weights: str | None = None,
        checkpoint: str | None = None,
        device: str | None = None,
        cfg: DetectionConfig | None = None,
    ) -> None:
        self.cfg = cfg or DetectionConfig()
        if checkpoint:
            # Fine-tuned checkpoint: class names and resolution come from the file.
            from rfdetr import RFDETR

            self.model = RFDETR.from_checkpoint(checkpoint)
        else:
            import rfdetr

            if variant not in VARIANT_CLASSES:
                raise ValueError(f"unknown variant {variant!r}, expected one of {sorted(VARIANT_CLASSES)}")
            model_cls = getattr(rfdetr, VARIANT_CLASSES[variant])
            kwargs = {"pretrain_weights": weights} if weights else {}
            self.model = model_cls(**kwargs)

        if device:
            import torch

            self.model.model.to(torch.device(device))

        self.ball_class_ids = self._resolve_ball_classes()

    def _resolve_ball_classes(self) -> list[int]:
        names = list(getattr(self.model, "class_names", None) or [])
        if not names:
            # Single-class fine-tuned checkpoints sometimes expose no labels.
            return [0]
        lowered = [n.lower() for n in names]
        for wanted in self.cfg.class_names:
            wanted = wanted.lower()
            if wanted in lowered:
                return [lowered.index(wanted)]
        if len(names) == 1:
            return [0]
        raise RuntimeError(
            "none of the configured ball class names "
            f"{self.cfg.class_names} exist in the model label map {names}. "
            "Pass --checkpoint for a fine-tuned ball model or adjust detection.class_names."
        )

    def predict(self, frames_rgb: list[np.ndarray]) -> list[BallDetection | None]:
        """Run a batch of RGB frames and return the best ball per frame."""
        if not frames_rgb:
            return []
        results = self.model.predict(
            list(frames_rgb),
            threshold=self.cfg.threshold,
            include_source_image=False,
        )
        return [self._pick(det) for det in results]

    def _pick(self, detections) -> BallDetection | None:
        best: BallDetection | None = None
        if detections is None or len(detections) == 0:
            return None
        class_ids = np.asarray(detections.class_id).reshape(-1)
        for i, class_id in enumerate(class_ids):
            if int(class_id) not in self.ball_class_ids:
                continue
            x1, y1, x2, y2 = (float(v) for v in detections.xyxy[i])
            score = float(detections.confidence[i])
            radius = max(x2 - x1, y2 - y1) / 2.0
            if not (self.cfg.min_radius_px <= radius <= self.cfg.max_radius_px):
                continue
            if best is None or score > best.score:
                best = BallDetection(
                    score=score,
                    cx=(x1 + x2) / 2.0,
                    cy=(y1 + y2) / 2.0,
                    radius=radius,
                    box=(x1, y1, x2, y2),
                )
        return best

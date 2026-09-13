"""Synthetic detector backed by simulator ground truth.

This exists so the full pipeline - tracking, rules, scoring, evidence, sync - can
be exercised and tested without a model file present, and so the evaluation
harness has a known-truth reference. It is a **simulation**, labelled as such
everywhere it surfaces, and `describe()["simulated"]` is True so the dashboard
can badge it. It is never selected when a real model is available.

It deliberately does **not** return perfect detections. A synthetic detector that
never misses and never hallucinates would make every downstream claim in this
project meaningless: the false-alarm suppression would have nothing to suppress,
the capability profiling would have no consequence, and the operator-feedback
curve would be theatre. So detections here degrade the way real ones do:

  * detection probability falls off with pixels-on-target, and falls off faster
    on a soft, noisy or heavily compressed camera;
  * boxes jitter in proportion to how poor the optics are;
  * cattle are confused with people at range - which is the single most common
    real false alarm at an Indian border installation;
  * sensor noise produces occasional phantom detections in empty scene.

All of it is seeded, so a demo replays identically.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from prahari.common.models import BBox, Capability, Detection, ObjectClass
from prahari.edge.detect.base import Detector, class_allowed


class SyntheticDetector(Detector):
    name = "synthetic-gt (SIMULATED)"

    def __init__(self, seed: int = 20260913, confidence_floor: float = 0.35) -> None:
        self.device = "cpu"
        self._rng = np.random.default_rng(seed)
        self.confidence_floor = confidence_floor

    # -- degradation models ---------------------------------------------
    @staticmethod
    def _detect_probability(px_height: float, quality: float) -> float:
        """Logistic fall-off in apparent size, shifted by image quality.

        `quality` in 0..1 (1 = pristine). A good camera holds high recall down to
        roughly 22 px of target height; a degraded one needs nearly twice that.
        """
        midpoint = 18.0 + (1.0 - quality) * 26.0
        steepness = 0.22 + quality * 0.16
        return 1.0 / (1.0 + math.exp(-steepness * (px_height - midpoint)))

    @staticmethod
    def _quality_from_context(ctx: dict[str, Any]) -> float:
        eff = float(ctx.get("effective_resolution_factor", 0.10))
        noise = float(ctx.get("noise_sigma", 4.0))
        block = float(ctx.get("compression_artifact_score", 0.2))
        night = bool(ctx.get("is_low_light", False))
        q = 1.0
        q *= float(np.clip(eff / 0.12, 0.25, 1.0))
        q *= float(np.clip(1.0 - (noise - 2.0) / 18.0, 0.25, 1.0))
        q *= float(np.clip(1.0 - block, 0.30, 1.0))
        if night:
            q *= 0.78
        return float(np.clip(q, 0.12, 1.0))

    # -- inference -------------------------------------------------------
    def infer(
        self,
        image: np.ndarray,
        *,
        allowed: set[Capability] | None = None,
        frame_index: int = 0,
        context: dict[str, Any] | None = None,
    ) -> list[Detection]:
        ctx = context or {}
        truth: list[dict[str, Any]] = ctx.get("ground_truth") or []
        quality = self._quality_from_context(ctx)
        h, w = image.shape[:2]
        out: list[Detection] = []

        for gt in truth:
            px_h = float(gt.get("px_height", 0.0))
            cls_value = str(gt.get("object_class", "unknown"))

            p = self._detect_probability(px_h, quality)
            if self._rng.random() > p:
                continue                      # a miss, exactly as happens at range

            # Class confusion at range. Cattle read as people on a poor camera,
            # which is why an unfiltered perimeter deployment alarms all night.
            if cls_value == ObjectClass.CATTLE.value:
                confuse = float(np.clip(0.45 * (1.0 - quality) + 12.0 / max(px_h, 6.0), 0, 0.6))
                if self._rng.random() < confuse:
                    cls_value = ObjectClass.PERSON.value
            elif cls_value == ObjectClass.PERSON.value and px_h < 26:
                if self._rng.random() < 0.10 * (1.0 - quality):
                    cls_value = ObjectClass.CATTLE.value

            if not class_allowed(cls_value, allowed):
                continue

            x1, y1, x2, y2 = (float(v) for v in gt["bbox"])

            # Jitter the box as a centre shift plus a symmetric size scaling,
            # rather than perturbing each corner independently. Independent
            # corners followed by min/max is *biased*: the expected width of
            # max(x)-min(x) over two noisy endpoints is larger than the true
            # width, so every box comes out inflated, and the inflation grows
            # with the noise. That silently enlarged boxes on exactly the
            # low-quality cameras, and the ground-plane fit - which reads box
            # height as a distance measurement - then under-estimated their
            # mounting height by 30-45%. Detector noise is real; a systematic
            # size bias masquerading as noise is not.
            jitter = (1.0 - quality) * max(2.0, px_h * 0.09)
            shift = self._rng.normal(0, jitter, 2)
            scale = 1.0 + self._rng.normal(0, 0.04 * (1.0 - quality) + 0.01)
            cx_box, cy_box = (x1 + x2) / 2 + shift[0], (y1 + y2) / 2 + shift[1]
            bw, bh = (x2 - x1) * scale, (y2 - y1) * scale
            x1, x2 = cx_box - bw / 2, cx_box + bw / 2
            y1, y2 = cy_box - bh / 2, cy_box + bh / 2
            x1, x2 = float(np.clip(x1, 0, w)), float(np.clip(x2, 0, w))
            y1, y2 = float(np.clip(y1, 0, h)), float(np.clip(y2, 0, h))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue

            conf = float(np.clip(
                0.52 + 0.42 * p * quality + self._rng.normal(0, 0.05), 0.05, 0.99))
            if conf < self.confidence_floor:
                continue

            try:
                oc = ObjectClass(cls_value)
            except ValueError:
                oc = ObjectClass.UNKNOWN

            out.append(Detection(
                object_class=oc,
                confidence=round(conf, 3),
                bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
                frame_index=frame_index,
            ))

        out.extend(self._phantoms(w, h, quality, allowed, frame_index))
        return out

    def _phantoms(
        self, w: int, h: int, quality: float,
        allowed: set[Capability] | None, frame_index: int,
    ) -> list[Detection]:
        """Noise-driven false positives.

        Rate scales with how bad the image is. These are the detections the rule
        engine, the normalcy model and the operator feedback loop have to earn
        their keep against.
        """
        rate = 0.045 * (1.0 - quality) ** 1.5
        if self._rng.random() > rate:
            return []
        if not class_allowed(ObjectClass.PERSON.value, allowed):
            return []
        # Phantoms cluster near the horizon, where noise looks most like a distant
        # figure - which is exactly where real ones cluster too.
        cy = h * float(self._rng.uniform(0.42, 0.60))
        cx = w * float(self._rng.uniform(0.05, 0.95))
        ph = float(self._rng.uniform(10, 24))
        pw = ph * 0.38
        return [Detection(
            object_class=ObjectClass.PERSON,
            confidence=round(float(np.clip(self._rng.normal(0.46, 0.07), 0.35, 0.72)), 3),
            bbox=BBox(x1=cx - pw / 2, y1=cy - ph, x2=cx + pw / 2, y2=cy),
            frame_index=frame_index,
        )]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device,
            "simulated": True,
            "note": (
                "Ground-truth-driven simulation with modelled miss rate, class "
                "confusion and noise-driven false positives. Not a trained model; "
                "reported confidences are synthetic and carry no accuracy claim."
            ),
        }

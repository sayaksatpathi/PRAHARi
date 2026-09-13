"""SAM 2 segmentation via ONNX Runtime — the intended production backend.

SAM 2 is used through an ONNX export rather than its official PyTorch
implementation, for the same reason the object detector is: adding torch and
torchvision would put ~2.5 GB of framework on an edge node whose entire premise
is that it stays small enough to update over a VSAT link. Pre-exported SAM 2.1
encoder+decoder ONNX models exist (e.g. vietanhdev/segment-anything-2.1-onnx-
models), so segmentation becomes one more onnxruntime session — no torch, and the
same CPU/CUDA execution-provider selection as everything else.

A necessary, prominent caveat. This backend is written against the documented
SAM 2 ONNX export interface, but it has NOT been run against real SAM weights on
the build machine, because the weights (and onnxruntime-gpu) could not be
downloaded on a ~0.1 MB/s connection. The pre/post-processing here follows the
published SAM 2 convention and is defensive about output layout, but it must be
validated the first time it runs against real weights. Until then the verified,
tested segmentation path is GrabCut; this is the upgrade that slots in behind the
same interface when the weights and a GPU are present. That honesty is the point:
shipping elaborate, untested model glue as if it were verified is exactly the
failure this project avoids elsewhere.

EdgeTAM (Apache-2.0, an on-device SAM 2 variant) fits the same interface; its
documented export path is CoreML rather than ONNX today, so it is noted as a
future backend rather than implemented here.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from prahari.common.models import BBox
from prahari.edge.segment.base import SegmentResult, Segmenter, finalise_mask

log = logging.getLogger("prahari.segment.sam")

SAM_INPUT_SIZE = 1024


class SamOnnxSegmenter(Segmenter):
    name = "sam2-onnx"
    approximate = False

    def __init__(self, encoder_path: Path | str, decoder_path: Path | str,
                 device: str = "auto") -> None:
        import onnxruntime as ort

        self.encoder_path = Path(encoder_path)
        self.decoder_path = Path(decoder_path)
        if not self.encoder_path.exists() or not self.decoder_path.exists():
            raise FileNotFoundError(
                "SAM 2 ONNX weights not found. Expected an encoder and decoder "
                "at the configured paths - see docs/segmentation.md for where to "
                "obtain them.")

        providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                     if device in ("auto", "cuda") else ["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(str(self.encoder_path), providers=providers)
        self.decoder = ort.InferenceSession(str(self.decoder_path), providers=providers)
        self.device = ("cuda" if "CUDAExecutionProvider" in self.encoder.get_providers()
                       else "cpu")
        self._enc_input = self.encoder.get_inputs()[0].name
        self._enc_outputs = [o.name for o in self.encoder.get_outputs()]
        self._dec_inputs = {i.name: i for i in self.decoder.get_inputs()}
        log.info("SAM 2 ONNX loaded on %s (encoder %s, decoder %s)",
                 self.device, self.encoder_path.name, self.decoder_path.name)

    # -- preprocessing ---------------------------------------------------
    @staticmethod
    def _preprocess(image: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        import cv2

        h, w = image.shape[:2]
        scale = SAM_INPUT_SIZE / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((SAM_INPUT_SIZE, SAM_INPUT_SIZE, 3), np.float32)
        canvas[:nh, :nw] = resized[:, :, ::-1]     # BGR->RGB
        # ImageNet normalisation, per the SAM convention.
        mean = np.array([0.485, 0.456, 0.406], np.float32) * 255.0
        std = np.array([0.229, 0.224, 0.225], np.float32) * 255.0
        canvas = (canvas - mean) / std
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
        return blob, scale, (h, w)

    # -- inference -------------------------------------------------------
    def segment(self, image: np.ndarray, box: BBox) -> SegmentResult | None:
        import cv2

        t0 = time.perf_counter()
        blob, scale, (h, w) = self._preprocess(image)

        try:
            enc_out = self.encoder.run(None, {self._enc_input: blob})
        except Exception:
            log.exception("SAM encoder failed")
            return None
        image_embed = enc_out[0]

        # Box prompt: SAM encodes a box as two labelled points - top-left (2) and
        # bottom-right (3) - in the model's input coordinate frame.
        pts = np.array([[[box.x1 * scale, box.y1 * scale],
                         [box.x2 * scale, box.y2 * scale]]], dtype=np.float32)
        labels = np.array([[2, 3]], dtype=np.float32)

        feed: dict[str, Any] = {}
        for name in self._dec_inputs:
            low = name.lower()
            if "image_embed" in low or low in ("image_embeddings", "embeddings"):
                feed[name] = image_embed
            elif "point_coord" in low or low == "coords":
                feed[name] = pts
            elif "point_label" in low or low == "labels":
                feed[name] = labels
            elif "mask_input" in low:
                feed[name] = np.zeros((1, 1, 256, 256), np.float32)
            elif "has_mask" in low:
                feed[name] = np.zeros((1,), np.float32)
            elif "orig_im_size" in low or "orig_size" in low:
                feed[name] = np.array([h, w], dtype=np.float32)
            elif low.startswith("high_res") and len(enc_out) > 1:
                # Some exports thread the encoder's high-res features to the
                # decoder; pass them positionally by index in the encoder outputs.
                idx = 1 if "0" in low else 2
                if idx < len(enc_out):
                    feed[name] = enc_out[idx]

        try:
            dec_out = self.decoder.run(None, feed)
        except Exception:
            log.exception("SAM decoder failed (input layout may differ from the "
                          "assumed export; see docs/segmentation.md)")
            return None

        masks = dec_out[0]
        scores = dec_out[1] if len(dec_out) > 1 else None
        # masks: (1, N, Hm, Wm). Pick the highest-scoring proposal.
        masks = np.asarray(masks)
        if masks.ndim == 4:
            best = int(np.argmax(scores[0])) if scores is not None else 0
            mask = masks[0, best]
            score = float(scores[0][best]) if scores is not None else 0.0
        else:
            mask = masks.reshape(masks.shape[-2], masks.shape[-1])
            score = 0.0

        mask = (mask > 0).astype(np.uint8)
        if mask.shape[:2] != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        # Crop to the object's region to keep the result bbox-local.
        x1 = int(max(0, box.x1 - box.width * 0.15))
        y1 = int(max(0, box.y1 - box.height * 0.15))
        x2 = int(min(w, box.x2 + box.width * 0.15))
        y2 = int(min(h, box.y2 + box.height * 0.15))
        crop = mask[y1:y2, x1:x2]
        latency = (time.perf_counter() - t0) * 1000.0

        return finalise_mask(
            crop, offset=(x1, y1), backend=self.name, approximate=False,
            latency_ms=latency, score=score,
            note="SAM 2 ONNX segmentation. NOTE: this backend has not been "
                 "validated against real weights on the build machine; verify on "
                 "first real run.",
        )

    def warmup(self) -> None:
        dummy = np.full((64, 64, 3), 127, np.uint8)
        try:
            self.segment(dummy, BBox(x1=8, y1=8, x2=56, y2=56))
        except Exception:
            log.debug("SAM warmup failed", exc_info=True)

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update({
            "validated": False,
            "note": "SAM 2 via ONNX Runtime. Unvalidated against real weights on "
                    "this machine - see docs/segmentation.md.",
        })
        return d

"""Learned person re-identification embeddings, as an optional upgrade.

The cross-camera coordinator matches appearance with an HSV colour histogram
(`appearance.py`). That is deliberately cheap and needs no download, and it is
also the documented weak link: two people in dark clothing look alike to it, and
the same person on a night-IR camera and a daylight camera does not.

This module is the better cue when a trained model is present, behind the same
interface and following the same pattern as the segmentation backends: the
learned path is used when its weights exist, and the classical path remains when
they do not. A node without the model keeps working exactly as before.

Design constraints, all inherited from the rest of the edge node:

*   **ONNX, not torch.** Training happens offline; the node loads one more
    `onnxruntime` session. The ~2.5 GB of framework that PyTorch would add is the
    difference between a node that can be updated over a VSAT link and one that
    cannot.
*   **Cosine distance on L2-normalised embeddings.** Scale-free, so a crop's
    brightness does not move the match, and it composes with the coordinator's
    existing 0..1 similarity contract without rescaling.
*   **Fails to the fallback, never to a wrong answer.** A missing model, a
    corrupt model or an unreadable crop returns None, and the caller uses the
    histogram. Silently returning a meaningless embedding would be worse than
    having no model at all, because the coordinator trusts this cue more.

**What a MOT17-trained model can and cannot do.** Trained on daylight street
pedestrians, it improves matching between cameras of *similar* modality. It does
not solve visible-to-thermal matching, because no thermal imagery exists in that
dataset to learn from. That is the harder half of the border problem and it needs
a visible-infrared dataset (SYSU-MM01, RegDB) that is not in hand. Stating the
boundary is the point: this closes part of a documented gap, not all of it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("prahari.crosscam.reid")

# Standard person re-ID crop geometry, and what the training pipeline produces.
CROP_W, CROP_H = 64, 128

# Below this a crop is a smear of pixels. The embedding would be noise, and noise
# that the coordinator weights highly is worse than an honest abstention.
MIN_CROP_W, MIN_CROP_H = 24, 48


class ReidEmbedder:
    """Wraps a trained embedding model as an ONNX Runtime session."""

    def __init__(self, model_path: Path | str, device: str = "auto",
                 cuda_dll_dir=None) -> None:
        import onnxruntime as ort

        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"re-ID model not found at {self.model_path}. Train one with "
                f"scripts/train_reid.py, or leave it absent to use the HSV "
                f"histogram fallback.")

        from prahari.common import cuda as cuda_support

        providers = cuda_support.providers_for(device, cuda_dll_dir)
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.device = ("cuda" if "CUDAExecutionProvider" in self.session.get_providers()
                       else "cpu")
        self._input = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        # (N, 3, H, W) - read the geometry from the model rather than assuming it.
        self.height = int(shape[2]) if isinstance(shape[2], int) else CROP_H
        self.width = int(shape[3]) if isinstance(shape[3], int) else CROP_W
        self.dim = int(self.session.get_outputs()[0].shape[-1])
        log.info("re-ID embedder loaded on %s (%dx%d crops, %d-d embeddings)",
                 self.device, self.width, self.height, self.dim)

    # -- inference -------------------------------------------------------
    def embed(self, image: np.ndarray, box) -> np.ndarray | None:
        """L2-normalised embedding for one person crop, or None if unusable."""
        crops = self.embed_batch(image, [box])
        return None if crops is None else crops[0]

    def embed_batch(self, image: np.ndarray, boxes) -> np.ndarray | None:
        """Embeddings for several boxes in one frame, in a single session run.

        Batched because the coordinator asks about every confirmed track on a
        frame, and one session call for eight crops costs far less than eight.
        """
        import cv2

        h, w = image.shape[:2]
        batch = []
        for box in boxes:
            x1 = max(0, int(round(box.x1)))
            y1 = max(0, int(round(box.y1)))
            x2 = min(w, int(round(box.x2)))
            y2 = min(h, int(round(box.y2)))
            if (x2 - x1) < MIN_CROP_W or (y2 - y1) < MIN_CROP_H:
                return None
            crop = cv2.resize(image[y1:y2, x1:x2], (self.width, self.height),
                              interpolation=cv2.INTER_LINEAR)
            batch.append(crop[:, :, ::-1])            # BGR -> RGB

        if not batch:
            return None

        blob = np.stack(batch).astype(np.float32) / 255.0
        # ImageNet normalisation - matched to the training transform. A mismatch
        # here does not raise; it quietly degrades every embedding, which is the
        # same class of silent failure the detector's input convention had.
        mean = np.array([0.485, 0.456, 0.406], np.float32)
        std = np.array([0.229, 0.224, 0.225], np.float32)
        blob = (blob - mean) / std
        blob = np.ascontiguousarray(blob.transpose(0, 3, 1, 2))

        try:
            out = np.asarray(self.session.run(None, {self._input: blob})[0])
        except Exception:
            log.exception("re-ID inference failed; falling back to the histogram")
            return None

        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    def describe(self) -> dict:
        return {
            "name": "reid-onnx",
            "device": self.device,
            "model_file": self.model_path.name,
            "embedding_dim": self.dim,
            "crop": [self.width, self.height],
            "note": ("Trained on daylight pedestrian imagery. Improves matching "
                     "between cameras of similar modality; does not solve "
                     "visible-to-thermal re-identification."),
        }


def similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Cosine similarity mapped to 0..1, matching the histogram cue's contract.

    Cosine runs -1..1, but a negative similarity between two person embeddings
    carries no useful meaning here - it is simply "not alike". Mapping to 0..1
    rather than clamping keeps the scale linear where it matters, and keeps this
    interchangeable with `appearance.similarity` so the coordinator's thresholds
    mean the same thing whichever cue is in use.
    """
    if a is None or b is None:
        return 0.0
    return float(max(0.0, min(1.0, (float(np.dot(a, b)) + 1.0) / 2.0)))


def build_embedder(settings) -> "ReidEmbedder | None":
    """Construct the embedder if its weights are present, else None.

    Returning None is a normal outcome, not an error: it means the node runs the
    histogram cue, which is tested and works.
    """
    path = Path(getattr(settings, "reid_model_path", "models/reid.onnx"))
    if not path.exists():
        log.info("cross-camera appearance: no re-ID model at %s - using the HSV "
                 "histogram cue", path)
        return None
    try:
        return ReidEmbedder(path, device=getattr(settings, "device", "auto"),
                            cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))
    except Exception as exc:
        log.warning("cross-camera appearance: re-ID model failed to load (%s); "
                    "using the HSV histogram cue", exc)
        return None

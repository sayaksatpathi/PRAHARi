"""SAM 2 segmentation via ONNX Runtime — the intended production backend.

SAM 2 is used through an ONNX export rather than its official PyTorch
implementation, for the same reason the object detector is: adding torch and
torchvision would put ~2.5 GB of framework on an edge node whose entire premise
is that it stays small enough to update over a VSAT link. Pre-exported SAM 2.1
encoder+decoder ONNX models exist (e.g. vietanhdev/segment-anything-2.1-onnx-
models), so segmentation becomes one more onnxruntime session — no torch, and the
same CPU/CUDA execution-provider selection as everything else.

**Validated against real weights** (v0.8): sam2_hiera_tiny encoder + decoder,
CUDA execution provider, RTX 4050 6 GB. Before that it was written against the
*documented* export interface and had never been run, which the module said
plainly - and it was wrong in two ways that only real weights could reveal:

  * `"mask_input" in name` also matches `has_mask_input`, so the boolean flag was
    fed a (1, 1, 256, 256) tensor and the decoder rejected the rank;
  * the encoder's outputs are (high_res_feats_0, high_res_feats_1, image_embed),
    so taking `enc_out[0]` as the image embedding passed a 32x256x256 feature map
    where a 256x64x64 embedding belonged.

Both were silent-looking mistakes in glue that *reads* correct. That is the whole
argument for the caveat this module used to carry, and for not shipping untested
model bindings as though they were verified. Inputs and outputs are now bound by
their exact names, taken from the loaded graph.

GrabCut remains the fallback: no download, no GPU, and the tested path when SAM
weights are absent.

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
                 device: str = "auto", cuda_dll_dir=None) -> None:
        import onnxruntime as ort

        self.encoder_path = Path(encoder_path)
        self.decoder_path = Path(decoder_path)
        if not self.encoder_path.exists() or not self.decoder_path.exists():
            raise FileNotFoundError(
                "SAM 2 ONNX weights not found. Expected an encoder and decoder "
                "at the configured paths - see docs/segmentation.md for where to "
                "obtain them.")

        from prahari.common import cuda as cuda_support

        # Resolves the CUDA runtime before asking for the provider, so the
        # session does not advertise the GPU and then quietly run on the CPU.
        providers = cuda_support.providers_for(device, cuda_dll_dir)
        self.encoder = ort.InferenceSession(str(self.encoder_path), providers=providers)
        self.decoder = ort.InferenceSession(str(self.decoder_path), providers=providers)
        self.device = ("cuda" if "CUDAExecutionProvider" in self.encoder.get_providers()
                       else "cpu")
        self._enc_input = self.encoder.get_inputs()[0].name
        self._enc_outputs = [o.name for o in self.encoder.get_outputs()]
        self._dec_inputs = [i.name for i in self.decoder.get_inputs()]
        # The decoder's feature inputs are matched to the encoder's outputs by
        # name rather than by position. Positional matching is what produced the
        # 32x256x256-for-256x64x64 bug: the orders differ between exports, and
        # an index that happens to work for one export silently corrupts another.
        missing = [n for n in ("image_embed", "point_coords", "point_labels")
                   if n not in self._dec_inputs]
        if missing:
            raise RuntimeError(
                f"this SAM decoder export does not have the expected inputs "
                f"{missing}; it has {self._dec_inputs}. The binding in this "
                f"module is written for the standard SAM 2 ONNX export.")
        log.info("SAM 2 ONNX loaded on %s (encoder %s, decoder %s); decoder "
                 "inputs %s", self.device, self.encoder_path.name,
                 self.decoder_path.name, self._dec_inputs)

    # -- preprocessing ---------------------------------------------------
    @staticmethod
    def _preprocess(image: np.ndarray):
        """Letterbox into the model's square input.

        Returns the blob, the scale factor, the original size, and the extent
        the real image occupies inside the square canvas. That last value is not
        optional bookkeeping: the decoder returns a mask over the whole padded
        canvas, and resizing the padded mask straight back to the frame size
        stretches the object by the padding ratio - on a 16:9 frame that is a
        44% vertical error, which looks like a plausible mask and is wrong.
        """
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
        return blob, scale, (h, w), (nh, nw)

    # -- inference -------------------------------------------------------
    def segment(self, image: np.ndarray, box: BBox) -> SegmentResult | None:
        import cv2

        t0 = time.perf_counter()
        blob, scale, (h, w), (nh, nw) = self._preprocess(image)

        try:
            enc_out = self.encoder.run(None, {self._enc_input: blob})
        except Exception:
            log.exception("SAM encoder failed")
            return None
        features = dict(zip(self._enc_outputs, enc_out))

        # Box prompt: SAM encodes a box as two labelled points - top-left (2) and
        # bottom-right (3) - in the model's input coordinate frame.
        pts = np.array([[[box.x1 * scale, box.y1 * scale],
                         [box.x2 * scale, box.y2 * scale]]], dtype=np.float32)
        labels = np.array([[2, 3]], dtype=np.float32)

        feed: dict[str, Any] = {}
        for name in self._dec_inputs:
            if name in features:                       # image_embed, high_res_feats_*
                feed[name] = features[name]
            elif name == "point_coords":
                feed[name] = pts
            elif name == "point_labels":
                feed[name] = labels
            elif name == "has_mask_input":
                # Tested before `mask_input`: the latter is a substring of the
                # former, and checking in the other order feeds a 4-D tensor to a
                # 1-D flag. That was the bug.
                feed[name] = np.zeros((1,), np.float32)
            elif name == "mask_input":
                feed[name] = np.zeros((1, 1, 256, 256), np.float32)
            elif name in ("orig_im_size", "orig_size"):
                feed[name] = np.array([h, w], dtype=np.float32)
            else:
                log.warning("SAM decoder input %r is not one this binding "
                            "knows; leaving it unset", name)

        try:
            dec_out = self.decoder.run(None, feed)
        except Exception:
            log.exception("SAM decoder failed (input layout may differ from the "
                          "assumed export; see docs/segmentation.md)")
            return None

        masks = np.asarray(dec_out[0])
        scores = np.asarray(dec_out[1]) if len(dec_out) > 1 else None
        # masks: (num_labels, num_proposals, Hm, Wm). SAM returns several
        # candidate masks per prompt; take the one it scores highest.
        if masks.ndim == 4:
            best = int(np.argmax(scores[0])) if scores is not None else 0
            mask = masks[0, best]
            score = float(scores[0][best]) if scores is not None else 0.0
        else:
            mask = masks.reshape(masks.shape[-2], masks.shape[-1])
            score = 0.0

        mask = (mask > 0).astype(np.uint8)
        # Undo the letterbox before undoing the resize. The mask covers the
        # padded square, so the padding is cropped off first and only the region
        # the real image occupied is scaled back to frame size.
        mh, mw = mask.shape[:2]
        if (mh, mw) != (h, w):
            ys = max(1, int(round(nh * mh / SAM_INPUT_SIZE)))
            xs = max(1, int(round(nw * mw / SAM_INPUT_SIZE)))
            mask = mask[:ys, :xs]
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
            note=f"SAM 2 ONNX segmentation on {self.device}; the mask is the "
                 f"highest-scoring of the decoder's proposals "
                 f"(predicted IoU {score:.2f}).",
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

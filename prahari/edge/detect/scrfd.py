"""SCRFD ONNX face-detector backend.

A modern alternative to the OpenCV Haar cascade in `face.py`, exposing the same
`detect()` / `describe()` interface so the rest of Prahari does not change. The
Haar implementation is left untouched; this is an additive backend.

Model: SCRFD-500M (InsightFace), ONNX, fixed 640x640 input, strides 8/16/32,
2 anchors per cell, bounding-box regression only (no keypoints). Runs on ONNX
Runtime (CUDA if available, else CPU). See docs/model-provenance.md for source,
checksum and license.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("prahari.face.scrfd")

DEFAULT_MODEL = Path("models/scrfd_500m.onnx")


def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][ovr <= iou_thresh]
    return keep


class ScrfdFaceDetector:
    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL,
        provider: str = "auto",
        conf_thresh: float = 0.3,
        nms_thresh: float = 0.4,
    ) -> None:
        import onnxruntime as ort

        self.model_path = Path(model_path)
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.input_size = (640, 640)  # (w, h) — fixed by the exported graph
        self._strides = [8, 16, 32]
        self._num_anchors = 2
        self._center_cache: dict[tuple, np.ndarray] = {}

        if provider == "auto":
            avail = ort.get_available_providers()
            providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                         if "CUDAExecutionProvider" in avail else ["CPUExecutionProvider"])
        elif provider in ("cuda", "gpu"):
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        if not self.model_path.exists():
            log.warning("SCRFD model not found at %s. Detector disabled.", self.model_path)
            self.session = None
            self.provider = None
            return
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.provider = self.session.get_providers()[0]
        self._input_name = self.session.get_inputs()[0].name
        self._output_names = [o.name for o in self.session.get_outputs()]
        log.info("SCRFD initialized (%s) on %s", self.model_path.name, self.provider)

    # ---- interface parity with FaceDetector -------------------------------
    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Return list of (x, y, w, h) bounding boxes — same shape as Haar."""
        return [(x, y, w, h) for (x, y, w, h, _s) in self.detect_scored(image)]

    def describe(self) -> dict:
        return {
            "name": "InsightFace SCRFD-500M (ONNX)",
            "enabled": self.session is not None,
            "type": "face_detector",
            "provider": self.provider,
            "input_size": self.input_size,
        }

    # ---- scored detection, used by the evaluation harness -----------------
    def detect_scored(self, image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        if self.session is None:
            return []
        blob, det_scale = self._preprocess(image)
        outs = self.session.run(self._output_names, {self._input_name: blob})
        # outs order: score_8, score_16, score_32, bbox_8, bbox_16, bbox_32
        scores_list, boxes_list = [], []
        for idx, stride in enumerate(self._strides):
            scores = outs[idx].reshape(-1)
            bbox_preds = outs[idx + len(self._strides)].reshape(-1, 4) * stride
            centers = self._anchor_centers(stride)
            pos = np.where(scores >= self.conf_thresh)[0]
            if pos.size == 0:
                continue
            boxes = _distance2bbox(centers[pos], bbox_preds[pos])
            scores_list.append(scores[pos])
            boxes_list.append(boxes)
        if not boxes_list:
            return []
        boxes = np.vstack(boxes_list) / det_scale
        scores = np.concatenate(scores_list)
        keep = _nms(boxes, scores, self.nms_thresh)
        out = []
        for i in keep:
            x1, y1, x2, y2 = boxes[i]
            out.append((int(round(x1)), int(round(y1)),
                        int(round(x2 - x1)), int(round(y2 - y1)), float(scores[i])))
        return out

    # ---- helpers ----------------------------------------------------------
    def _preprocess(self, image: np.ndarray):
        iw, ih = self.input_size
        h, w = image.shape[:2]
        im_ratio = h / w
        model_ratio = ih / iw
        if im_ratio > model_ratio:
            new_h = ih
            new_w = int(new_h / im_ratio)
        else:
            new_w = iw
            new_h = int(new_w * im_ratio)
        det_scale = new_h / h
        resized = cv2.resize(image, (new_w, new_h))
        canvas = np.zeros((ih, iw, 3), dtype=np.uint8)
        canvas[:new_h, :new_w, :] = resized
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128, (iw, ih), (127.5, 127.5, 127.5), swapRB=True)
        return blob, det_scale

    def _anchor_centers(self, stride: int) -> np.ndarray:
        key = (stride, self.input_size)
        if key in self._center_cache:
            return self._center_cache[key]
        iw, ih = self.input_size
        gh, gw = ih // stride, iw // stride
        ys, xs = np.mgrid[0:gh, 0:gw]
        centers = np.stack([xs, ys], axis=-1).astype(np.float32) * stride
        centers = centers.reshape(-1, 2)
        if self._num_anchors > 1:
            centers = np.repeat(centers, self._num_anchors, axis=0)
        self._center_cache[key] = centers
        return centers

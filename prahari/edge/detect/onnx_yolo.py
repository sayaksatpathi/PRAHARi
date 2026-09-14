"""ONNX Runtime YOLO detector.

ONNX Runtime is used rather than PyTorch + Ultralytics for three reasons, all of
which matter for a government deployment rather than for a benchmark:

  * Footprint. ~50 MB against ~2.5 GB, which is the difference between an edge
    node that can be updated over a VSAT link and one that cannot.
  * One code path for CPU and CUDA. The same file runs on a workstation with an
    RTX card and on a fanless box at a border outpost, selecting the execution
    provider at load time.
  * Licensing. Ultralytics YOLO is AGPL-3.0. An ONNX graph executed by ONNX
    Runtime (MIT) removes a licence question from the procurement path. Whichever
    model is exported, its own licence still applies and must be recorded - see
    `models/README.md`.

The exporter is intentionally not pinned: any detector that exports to ONNX with
a YOLO-style output head works here, including license-clean alternatives such as
RT-DETR, YOLOX or D-FINE. Output layout is detected at load time rather than
assumed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from prahari.common.models import BBox, Capability, Detection, ObjectClass
from prahari.edge.detect.base import Detector, class_allowed

log = logging.getLogger("prahari.detect.onnx")

# COCO indices that map onto classes a border deployment cares about. Everything
# else in the 80-class set is discarded before it reaches the rule engine.
#
# Note index 19 ("cow"): livestock is not noise to be filtered out at a border,
# it is both the dominant false-alarm source and, on some sectors, the smuggled
# commodity. It gets a first-class object class.
COCO_TO_CLASS: dict[int, ObjectClass] = {
    0: ObjectClass.PERSON,
    1: ObjectClass.BICYCLE,
    2: ObjectClass.CAR,
    3: ObjectClass.MOTORCYCLE,
    5: ObjectClass.BUS,
    7: ObjectClass.TRUCK,
    8: ObjectClass.BOAT,
    19: ObjectClass.CATTLE,
    20: ObjectClass.CATTLE,   # sheep -> livestock
    17: ObjectClass.CATTLE,   # horse -> livestock
}


def letterbox(
    image: np.ndarray, new_shape: int = 640, colour: int = 114
) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize preserving aspect ratio and pad to a square, YOLO-style."""
    import cv2

    h, w = image.shape[:2]
    r = min(new_shape / h, new_shape / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((new_shape, new_shape, 3), colour, dtype=np.uint8)
    top, left = (new_shape - nh) // 2, (new_shape - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, r, (left, top)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression. boxes are xyxy."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep


class OnnxYoloDetector(Detector):
    name = "onnx-yolo"

    def __init__(
        self,
        model_path: Path | str,
        device: str = "auto",
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        input_size: int = 640,
        cuda_dll_dir=None,
    ) -> None:
        import onnxruntime as ort

        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {self.model_path}")

        providers = self._select_providers(device, ort, cuda_dll_dir)
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.device = "cuda" if "CUDAExecutionProvider" in self.session.get_providers() else "cpu"
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        # Input convention, probed from the model on the first real frame
        # rather than assumed. See `_probe_preprocessing`.
        self._scale_01: bool | None = None
        self._swap_rb: bool | None = None

        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        # Static shapes give the real input size; dynamic axes fall back to the
        # configured default.
        shape = inp.shape
        self.input_size = int(shape[2]) if isinstance(shape[2], int) else input_size
        self.output_names = [o.name for o in self.session.get_outputs()]
        self._layout: str | None = None
        self._yolox: bool | None = None
        self._grid_cache: tuple[np.ndarray, np.ndarray] | None = None
        log.info(
            "loaded %s on %s (input %s, providers=%s)",
            self.model_path.name, self.device, self.input_size, self.session.get_providers(),
        )

    @staticmethod
    def _select_providers(device: str, ort, cuda_dll_dir=None) -> list[str]:
        available = ort.get_available_providers()
        if device == "cpu":
            return ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" not in available:
            if device == "cuda":
                log.warning(
                    "CUDA requested but onnxruntime-gpu is not installed; falling "
                    "back to CPU. Install onnxruntime-gpu to use the GPU.")
            return ["CPUExecutionProvider"]
        # onnxruntime-gpu advertises CUDA whether or not the CUDA runtime is
        # actually loadable, and a session that cannot load it falls back to the
        # CPU without raising. Resolve the runtime first, so the provider list
        # reflects what will really run - see prahari.common.cuda.
        from prahari.common import cuda as cuda_support

        return cuda_support.providers_for(device, cuda_dll_dir)

    def warmup(self) -> None:
        dummy = np.zeros((1, 3, self.input_size, self.input_size), dtype=np.float32)
        try:
            self.session.run(self.output_names, {self.input_name: dummy})
        except Exception:
            log.exception("warmup inference failed")

    # -- output decoding -------------------------------------------------
    def _yolox_grids(self, n_anchors: int) -> tuple[np.ndarray, np.ndarray]:
        """Anchor-point grid and per-anchor stride for a YOLOX head.

        Cached, because it depends only on the input size.
        """
        if self._grid_cache is not None and self._grid_cache[0].shape[1] == n_anchors:
            return self._grid_cache

        grids, strides = [], []
        for stride in (8, 16, 32):
            size = self.input_size // stride
            yv, xv = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
            grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2).astype(np.float32)
            grids.append(grid)
            strides.append(np.full((1, grid.shape[1], 1), stride, dtype=np.float32))

        grid = np.concatenate(grids, axis=1)
        stride = np.concatenate(strides, axis=1)
        if grid.shape[1] != n_anchors:
            log.warning(
                "YOLOX grid has %d anchors but the model produced %d; the input "
                "size (%d) may not match the export",
                grid.shape[1], n_anchors, self.input_size,
            )
        self._grid_cache = (grid, stride)
        return self._grid_cache

    def _needs_yolox_decode(self, boxes: np.ndarray) -> bool:
        """Decide, from the data, whether boxes are still in grid units.

        A decoded head emits box centres spanning the input resolution - values
        into the hundreds. YOLOX's released ONNX exports emit offsets relative to
        an anchor point, which stay within a few units. Testing the magnitude is
        more robust than testing the filename, and costs one comparison once.
        """
        return float(np.abs(boxes[:, :2]).max()) < (self.input_size / 8.0)

    def _decode(self, raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Normalise the many YOLO output layouts into (boxes_xywh, scores, class_ids).

        Handles:
          * (1, 84, N)    - YOLOv8/v11 style, transposed, no objectness
          * (1, N, 84)    - same, already in row-major detections
          * (1, N, 85)    - YOLOv5 style with a separate objectness column
        """
        arr = raw[0] if raw.ndim == 3 else raw

        if self._layout is None:
            # The class dimension is the small one; the detection dimension is
            # the large one (thousands of anchors).
            if arr.shape[0] < arr.shape[1]:
                self._layout = "transposed"
            else:
                self._layout = "rows"
            log.debug("detected YOLO output layout: %s (shape %s)", self._layout, arr.shape)

        preds = arr.T if self._layout == "transposed" else arr

        ncol = preds.shape[1]
        boxes = preds[:, :4]
        if ncol >= 85 and self._layout == "rows":
            # A separate objectness column: YOLOv5 and YOLOX both look like this.
            if self._yolox is None:
                self._yolox = self._needs_yolox_decode(boxes)
                if self._yolox:
                    log.info("detected an undecoded YOLOX head; applying "
                             "anchor-point and stride decoding")
            if self._yolox:
                grid, stride = self._yolox_grids(preds.shape[0])
                n = min(preds.shape[0], grid.shape[1])
                boxes = boxes[:n].copy()
                preds = preds[:n]
                boxes[:, :2] = (boxes[:, :2] + grid[0, :n]) * stride[0, :n]
                boxes[:, 2:4] = np.exp(np.clip(boxes[:, 2:4], -20, 20)) * stride[0, :n]
            objectness = preds[:, 4:5]
            class_scores = preds[:, 5:] * objectness
        else:
            class_scores = preds[:, 4:]

        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(class_scores.shape[0]), class_ids]
        return boxes, scores, class_ids

    # -- input convention ------------------------------------------------
    def _blob(self, canvas: np.ndarray, scale_01: bool, swap_rb: bool) -> np.ndarray:
        pixels = canvas[:, :, ::-1] if swap_rb else canvas
        blob = pixels.transpose(2, 0, 1)[None].astype(np.float32)
        if scale_01:
            blob /= 255.0
        return np.ascontiguousarray(blob)

    def _probe_preprocessing(self, canvas: np.ndarray) -> None:
        """Work out what input this model actually wants, by trying.

        Detector families disagree, and the disagreement is not visible in the
        graph. Ultralytics YOLOv5/v8/v11 exports expect RGB scaled to 0..1;
        YOLOX's released exports expect BGR at the raw 0..255 range and no
        normalisation at all.

        Getting this wrong does not raise. It produces an empty detection list
        on every frame, which looks exactly like "the model cannot see anything
        in this footage" - and that is a conclusion one might plausibly reach
        about night-time border imagery, write down, and believe. It was in fact
        reached about MOT17, where the correct answer is 63 confident people in
        the first frame tried.

        Measured on yolox_tiny against a MOT17 frame: 0.88 best score without
        normalisation, 0.0001 with it. Four orders of magnitude, silently.

        So the convention is probed once, from the data, on the first real frame
        - the same principle as `_needs_yolox_decode` - and logged.
        """
        best = (-1.0, False, False)
        results = []
        for scale_01 in (False, True):
            for swap_rb in (False, True):
                try:
                    raw = self.session.run(
                        self.output_names,
                        {self.input_name: self._blob(canvas, scale_01, swap_rb)})[0]
                    _, scores, _ = self._decode(np.asarray(raw))
                    top = float(scores.max()) if scores.size else 0.0
                except Exception:
                    top = -1.0
                results.append((top, scale_01, swap_rb))
                if top > best[0]:
                    best = (top, scale_01, swap_rb)

        _, self._scale_01, self._swap_rb = best
        log.info(
            "detector input convention probed: %s, %s (best score %.3f). "
            "Alternatives scored %s",
            "0..1 normalised" if self._scale_01 else "raw 0..255",
            "RGB" if self._swap_rb else "BGR",
            best[0],
            ", ".join(f"{'norm' if a else 'raw'}/{'RGB' if b else 'BGR'}={t:.3f}"
                      for t, a, b in results),
        )
        if best[0] < 0.05:
            log.warning(
                "no input convention produced a confident detection on this "
                "frame (best %.4f). Either the imagery genuinely contains "
                "nothing this model recognises, or the model does not match "
                "this pre-processing at all.", best[0])

    # -- inference -------------------------------------------------------
    def infer(
        self,
        image: np.ndarray,
        *,
        allowed: set[Capability] | None = None,
        frame_index: int = 0,
        context: dict[str, Any] | None = None,
    ) -> list[Detection]:
        h0, w0 = image.shape[:2]
        canvas, ratio, (pad_x, pad_y) = letterbox(image, self.input_size)
        if self._scale_01 is None:
            self._probe_preprocessing(canvas)
        blob = self._blob(canvas, self._scale_01, self._swap_rb)

        try:
            raw = self.session.run(self.output_names, {self.input_name: blob})[0]
        except Exception:
            log.exception("inference failed on frame %s", frame_index)
            return []

        boxes_xywh, scores, class_ids = self._decode(np.asarray(raw))

        keep_mask = scores >= self.conf_threshold
        if not keep_mask.any():
            return []
        boxes_xywh = boxes_xywh[keep_mask]
        scores = scores[keep_mask]
        class_ids = class_ids[keep_mask]

        # cx,cy,w,h -> x1,y1,x2,y2, then undo the letterbox transform.
        cx, cy, bw, bh = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
        xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        xyxy[:, [0, 2]] = (xyxy[:, [0, 2]] - pad_x) / ratio
        xyxy[:, [1, 3]] = (xyxy[:, [1, 3]] - pad_y) / ratio
        xyxy[:, [0, 2]] = xyxy[:, [0, 2]].clip(0, w0)
        xyxy[:, [1, 3]] = xyxy[:, [1, 3]].clip(0, h0)

        out: list[Detection] = []
        for idx in nms(xyxy, scores, self.iou_threshold):
            oc = COCO_TO_CLASS.get(int(class_ids[idx]))
            if oc is None:
                continue
            if not class_allowed(oc.value, allowed):
                continue
            x1, y1, x2, y2 = (float(v) for v in xyxy[idx])
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            out.append(Detection(
                object_class=oc,
                confidence=round(float(scores[idx]), 3),
                bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
                frame_index=frame_index,
            ))
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device,
            "simulated": False,
            "model_file": self.model_path.name,
            "input_size": self.input_size,
            "input_convention": (
                None if self._scale_01 is None else
                f"{'0..1' if self._scale_01 else '0..255'}/"
                f"{'RGB' if self._swap_rb else 'BGR'}"),
            "providers": list(self.session.get_providers()),
            "note": (
                "Pretrained COCO-class detector. Performance on border imagery at "
                "night, at range and in adverse weather is NOT characterised by "
                "COCO metrics and must be validated on domain footage before any "
                "operational claim is made."
            ),
        }

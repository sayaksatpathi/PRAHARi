"""Lightweight face detection using OpenCV Haar cascades."""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("prahari.face")

class FaceDetector:
    def __init__(self) -> None:
        # Use the haarcascade provided by opencv-python
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            log.warning("Haar cascade not found at %s. Face detection disabled.", cascade_path)
            self.cascade = None
        else:
            self.cascade = cv2.CascadeClassifier(str(cascade_path))
            log.info("Face detector initialized with %s", cascade_path.name)

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Return list of (x, y, w, h) bounding boxes."""
        if self.cascade is None:
            return []
            
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Fast, low-accuracy settings suitable for video analytics
        faces = self.cascade.detectMultiScale(
            gray, 
            scaleFactor=1.1, 
            minNeighbors=5, 
            minSize=(30, 30)
        )
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]

    def describe(self) -> dict:
        return {
            "name": "OpenCV HaarCascade FrontalFace",
            "enabled": self.cascade is not None,
            "type": "face_detector"
        }


class YuNetFaceDetector:
    """OpenCV YuNet face detector — the deployable, permissively-licensed backend.

    Unlike the SCRFD candidate (research/non-commercial weights), YuNet ships in
    OpenCV Zoo under Apache/MIT terms, so it carries no licensing blocker for
    deployment, and it is far stronger than the Haar baseline. Same interface as
    ``FaceDetector``.
    """
    def __init__(self, model_path: str | Path = "models/face_yunet_2023mar.onnx",
                 conf_thresh: float = 0.6, nms_thresh: float = 0.3) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            log.warning("YuNet model not found at %s. Detector disabled.", self.model_path)
            self._det = None
            return
        self._det = cv2.FaceDetectorYN_create(
            str(self.model_path), "", (320, 320), conf_thresh, nms_thresh, 5000)
        self.conf_thresh = conf_thresh
        log.info("YuNet face detector initialized (%s)", self.model_path.name)

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        if self._det is None:
            return []
        h, w = image.shape[:2]
        self._det.setInputSize((w, h))
        _n, faces = self._det.detect(image)
        if faces is None:
            return []
        return [(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in faces]

    def describe(self) -> dict:
        return {
            "name": "OpenCV YuNet 2023mar",
            "enabled": self._det is not None,
            "type": "face_detector",
            "license": "Apache/MIT (deployable)",
        }


def build_face_detector(backend: str = "auto", **kwargs):
    """Return a face detector by backend name, behind one interface.

    All backends expose ``detect(image) -> [(x, y, w, h)]`` and ``describe()``.

    - ``"auto"`` (default, deployable): YuNet when its model is present, else the
      Haar cascade. This is the shipping path and it never touches research-only
      weights — resolving the SCRFD licensing gate for anything that deploys
      (docs/scrfd-licensing.md).
    - ``"yunet"``: the permissively-licensed (Apache/MIT) deployable backend.
    - ``"haar"``: the always-available OpenCV baseline (no external weights).
    - ``"scrfd"``: a **research-only-weights** candidate, gated — it refuses to
      load unless research use is explicitly acknowledged (evaluation only).
    """
    backend = (backend or "auto").lower()
    if backend in ("auto", "default"):
        yunet = YuNetFaceDetector(**kwargs)
        if getattr(yunet, "_det", None) is not None:
            return yunet
        log.info("YuNet model absent; face detection falls back to Haar baseline.")
        return FaceDetector()
    if backend in ("haar", "haarcascade", "opencv"):
        return FaceDetector()
    if backend in ("yunet", "yunet_2023"):
        return YuNetFaceDetector(**kwargs)
    if backend in ("scrfd", "scrfd_500m", "onnx"):
        from prahari.edge.detect.scrfd import ScrfdFaceDetector
        return ScrfdFaceDetector(**kwargs)
    raise ValueError(f"unknown face-detector backend: {backend!r}")

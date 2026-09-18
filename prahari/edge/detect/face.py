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


def build_face_detector(backend: str = "haar", **kwargs):
    """Return a face detector by backend name, behind one interface.

    Both backends expose ``detect(image) -> [(x, y, w, h)]`` and ``describe()``.
    Additive helper — the Haar ``FaceDetector`` above is the unchanged default
    baseline; ``"scrfd"`` selects the ONNX SCRFD-500M backend.
    """
    backend = (backend or "haar").lower()
    if backend in ("haar", "haarcascade", "opencv"):
        return FaceDetector()
    if backend in ("scrfd", "scrfd_500m", "onnx"):
        from prahari.edge.detect.scrfd import ScrfdFaceDetector
        return ScrfdFaceDetector(**kwargs)
    raise ValueError(f"unknown face-detector backend: {backend!r}")

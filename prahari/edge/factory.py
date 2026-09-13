"""Construction of sources and detectors from configuration.

Kept in one place so that "which detector am I actually running?" has a single
answer, and so the fallback to the simulated detector is explicit and logged
rather than happening silently. A demo that quietly ran synthetic detections
while claiming a real model would be worthless.
"""
from __future__ import annotations

import logging
from pathlib import Path

from prahari.common.config import Settings
from prahari.common.models import Camera, CameraRole, SensorType
from prahari.edge.detect.base import Detector
from prahari.edge.detect.synthetic import SyntheticDetector
from prahari.edge.sources.base import VideoSource
from prahari.edge.sources.simulator import OpticalProfile, SimulatedCamera

log = logging.getLogger("prahari.factory")


def build_detector(settings: Settings) -> Detector:
    """Pick a detector, preferring a real model and saying so either way."""
    choice = (settings.detector or "auto").lower()

    if choice == "synthetic":
        log.warning("detector: SYNTHETIC by configuration - detections are simulated")
        return SyntheticDetector(seed=settings.demo_seed,
                                 confidence_floor=settings.detection_confidence)

    model_path = Path(settings.model_path)
    if choice in ("auto", "onnx"):
        if not model_path.exists():
            if choice == "onnx":
                raise FileNotFoundError(
                    f"PRAHARI_DETECTOR=onnx but no model at {model_path}. "
                    f"See models/README.md for how to obtain one."
                )
            log.warning(
                "detector: no ONNX model at %s - falling back to the SYNTHETIC "
                "detector. Detections are simulated and are labelled as such in "
                "the dashboard. See models/README.md to install a real model.",
                model_path,
            )
            return SyntheticDetector(seed=settings.demo_seed,
                                     confidence_floor=settings.detection_confidence)
        try:
            from prahari.edge.detect.onnx_yolo import OnnxYoloDetector

            det = OnnxYoloDetector(
                model_path=model_path,
                device=settings.device,
                conf_threshold=settings.detection_confidence,
                iou_threshold=settings.nms_iou,
            )
            log.info("detector: ONNX %s on %s", model_path.name, det.device)
            return det
        except Exception:
            if choice == "onnx":
                raise
            log.exception(
                "detector: ONNX model failed to load - falling back to SYNTHETIC"
            )
            return SyntheticDetector(seed=settings.demo_seed,
                                     confidence_floor=settings.detection_confidence)

    raise ValueError(f"unknown detector setting: {settings.detector}")


# Optical preset per role, used when a simulated camera does not name one.
_ROLE_PRESET = {
    CameraRole.CHOKEPOINT: "gate_hd",
    CameraRole.PERIMETER: "perimeter_aged",
    CameraRole.APPROACH: "night_ir",
    CameraRole.RIVERINE: "perimeter_aged",
}


def build_source(camera: Camera, settings: Settings) -> VideoSource:
    """Construct the frame producer for a camera."""
    kind = camera.source_kind

    if kind == "simulator":
        sim = camera.sim_profile or {}
        preset = sim.get("preset") or _ROLE_PRESET.get(camera.role, "perimeter_aged")
        optical = OpticalProfile.preset(preset)
        if camera.sensor_type is SensorType.THERMAL:
            optical = OpticalProfile.preset("thermal")
        return SimulatedCamera(
            camera_id=camera.camera_id,
            width=camera.claimed_width,
            height=camera.claimed_height,
            fps=camera.claimed_fps,
            fov_deg=camera.field_of_view_deg,
            camera_height_m=float(sim.get("camera_height_m", 5.0)),
            tilt_deg=float(sim.get("tilt_deg", 2.0)),
            optical=optical,
            scenario=sim.get("scenario", camera.role.value),
            seed=settings.demo_seed + abs(hash(camera.camera_id)) % 9973,
            fence_distance_m=float(sim.get("fence_distance_m", 45.0)),
        )

    if kind in ("file", "rtsp", "onvif"):
        from prahari.edge.sources.stream import StreamSource

        return StreamSource(
            camera_id=camera.camera_id,
            url=camera.stream_url,
            username=camera.username,
            password=camera.password,
            nominal_fps=camera.claimed_fps,
            loop=(kind == "file"),
        )

    raise ValueError(f"unsupported source kind: {kind}")

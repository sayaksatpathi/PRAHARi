"""Tests for ANPR and its capability gating.

The gating is the point. Plate reading is commodity; refusing to attempt it
where the imagery cannot support it, and saying so, is not. These tests assert
the refusals as hard as they assert the reads.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.models import (
    BBox, Camera, CameraRole, Capability, ObjectClass, Point, SensorType,
)
from prahari.edge.anpr import (
    MIN_PLATE_WIDTH_PX, PlateRead, RepeatPlateTracker, SyntheticPlateReader,
    anpr_region, normalise_plate, vehicle_in_anpr_region,
)
from prahari.edge.profiling.certificate import issue_certificate
from prahari.edge.profiling.measure import CameraProfiler
from prahari.edge.sources.simulator import OpticalProfile, SimulatedCamera


# =====================================================================
# Plate normalisation
# =====================================================================

@pytest.mark.parametrize("raw,expected,valid", [
    ("WB24AB1234", "WB 24 AB 1234", True),
    ("wb 24 ab 1234", "WB 24 AB 1234", True),
    ("WB-24-AB-1234", "WB 24 AB 1234", True),
    ("DL8CAF5031", "DL 8 CAF 5031", True),
    ("MH12DE1433", "MH 12 DE 1433", True),
    ("!!!!", "", False),
])
def test_plate_normalisation(raw, expected, valid):
    text, ok = normalise_plate(raw)
    assert text == expected
    assert ok is valid


def test_garbage_is_not_reported_as_a_valid_plate():
    """A read that does not fit the format must not claim to."""
    text, ok = normalise_plate("XQ9")
    assert ok is False
    assert text == "XQ9", "the raw read is kept, but flagged invalid"


# =====================================================================
# Capability gating
# =====================================================================

def _profiled_camera(role, preset, fov, height, tilt, w, h, sensor=SensorType.VISIBLE,
                     scenario="chokepoint"):
    sim = SimulatedCamera("T", w, h, 12.0, fov, height,
                          OpticalProfile.preset(preset), scenario,
                          seed=31337, tilt_deg=tilt)
    sim.open()
    profiler = CameraProfiler("T", min_frames=20, min_ground_samples=20)
    for _ in range(400):
        frame = sim.read()
        if frame is None:
            continue
        profiler.observe_frame(frame.image, frame.timestamp.timestamp())
        for gt in frame.ground_truth:
            if gt["object_class"] != "person":
                continue
            x1, y1, x2, y2 = gt["bbox"]
            if y2 >= h - 2 or y1 <= 1 or x1 <= 1 or x2 >= w - 2:
                continue
            profiler.observe_person(foot_v=y2, px_height=y2 - y1, px_width=x2 - x1)
    measurement = profiler.build(claimed_fps=12.0)
    camera = Camera(camera_id="T", name="t", role=role, sensor_type=sensor,
                    claimed_width=w, claimed_height=h, field_of_view_deg=fov)
    return issue_certificate(camera, measurement), sim


def test_gate_camera_is_granted_anpr_in_part_of_frame():
    cert, _ = _profiled_camera(CameraRole.CHOKEPOINT, "gate_hd", 28.0, 3.0, 12.0,
                               1280, 720)
    assert cert.is_granted(Capability.ANPR)
    region = anpr_region(cert)
    assert region is not None and len(region) >= 3
    top = min(p.y for p in region)
    assert top > 0.0, "the grant must cover part of the frame, not all of it"


def test_wide_perimeter_camera_is_refused_anpr():
    cert, _ = _profiled_camera(CameraRole.PERIMETER, "perimeter_aged", 62.0, 6.0,
                               2.0, 1280, 720, scenario="perimeter")
    assert not cert.is_granted(Capability.ANPR)
    assert anpr_region(cert) is None
    grant = cert.grant_for(Capability.ANPR)
    assert grant is not None and grant.reason


def test_thermal_camera_is_refused_anpr_whatever_its_resolution():
    cert, _ = _profiled_camera(CameraRole.CHOKEPOINT, "gate_hd", 28.0, 3.0, 12.0,
                               1280, 720, sensor=SensorType.THERMAL)
    assert not cert.is_granted(Capability.ANPR)
    assert "thermal" in cert.grant_for(Capability.ANPR).reason.lower()


def test_vehicle_outside_the_certified_band_is_not_read():
    """The spatial gate, which is the part most systems skip."""
    region = [Point(x=0.0, y=0.7), Point(x=1.0, y=0.7),
              Point(x=1.0, y=1.0), Point(x=0.0, y=1.0)]
    near = BBox(x1=500, y1=600, x2=700, y2=700)     # foot at y=700 -> 0.97
    far = BBox(x1=500, y1=200, x2=560, y2=300)      # foot at y=300 -> 0.42
    assert vehicle_in_anpr_region(near, region, 1000, 720)
    assert not vehicle_in_anpr_region(far, region, 1000, 720)


def test_no_region_means_no_read():
    far = BBox(x1=0, y1=0, x2=100, y2=100)
    assert not vehicle_in_anpr_region(far, None, 1000, 720)


# =====================================================================
# Synthetic reader honesty
# =====================================================================

def test_synthetic_reader_refuses_an_illegible_plate():
    """The demo reader must not invent what the frame does not render.

    If it returned the right plate regardless of legibility, every claim about
    capability gating would be untestable theatre.
    """
    reader = SyntheticPlateReader(seed=7)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    vehicle = BBox(x1=100, y1=100, x2=200, y2=200)
    truth = [{
        "object_class": "truck",
        "bbox": [100, 100, 200, 200],
        "plate": "WB24AB1234",
        # 20 px wide: far too small for any glyph to have been drawn.
        "plate_bbox": [140, 180, 160, 188],
    }]
    assert reader.read(image, vehicle, {"ground_truth": truth}) is None


def test_synthetic_reader_reads_a_legible_plate():
    reader = SyntheticPlateReader(seed=7)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    vehicle = BBox(x1=100, y1=100, x2=300, y2=300)
    truth = [{
        "object_class": "truck",
        "bbox": [100, 100, 300, 300],
        "plate": "WB24AB1234",
        "plate_bbox": [140, 250, 280, 280],     # 140 px wide
    }]
    read = reader.read(image, vehicle, {"ground_truth": truth})
    assert read is not None
    assert read.simulated is True
    assert read.above_threshold is True
    assert "SYNTHETIC" in read.note.upper()


def test_marginal_plate_is_flagged_not_silently_promoted():
    reader = SyntheticPlateReader(seed=11)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    vehicle = BBox(x1=100, y1=100, x2=300, y2=300)
    truth = [{
        "object_class": "truck",
        "bbox": [100, 100, 300, 300],
        "plate": "WB24AB1234",
        "plate_bbox": [150, 250, 210, 268],     # 60 px: below the threshold
    }]
    read = reader.read(image, vehicle, {"ground_truth": truth})
    assert read is not None
    assert read.above_threshold is False
    assert str(MIN_PLATE_WIDTH_PX) in read.note


# =====================================================================
# Repeat-entity intelligence
# =====================================================================

def _read(plate: str, *, valid: bool = True, above: bool = True) -> PlateRead:
    text, fmt_ok = normalise_plate(plate)
    return PlateRead(
        text=plate, text_normalised=text, ocr_confidence=0.9,
        detection_confidence=0.9,
        plate_bbox=BBox(x1=0, y1=0, x2=120, y2=30), plate_width_px=120.0,
        above_threshold=above, format_valid=fmt_ok and valid, backend="test",
    )


def test_repeat_night_crossings_are_flagged():
    tracker = RepeatPlateTracker(min_sightings=3)
    base = datetime(2026, 9, 14, 2, 30, tzinfo=timezone.utc)
    finding = None
    for day in range(3):
        finding = tracker.observe(_read("WB24AB1234"), "CAM-011",
                                  base + timedelta(days=day))
    assert finding is not None
    assert finding["unusual_hour_sightings"] >= 3
    assert "night" in finding["summary"].lower()


def test_daytime_crossings_are_not_flagged_as_a_night_pattern():
    tracker = RepeatPlateTracker(min_sightings=3)
    base = datetime(2026, 9, 14, 11, 0, tzinfo=timezone.utc)
    findings = [tracker.observe(_read("WB24AB1234"), "CAM-011",
                                base + timedelta(days=d)) for d in range(4)]
    assert all(f is None for f in findings), \
        "routine daytime traffic must not become an intelligence finding"


def test_appearance_at_several_locations_is_flagged():
    tracker = RepeatPlateTracker(min_sightings=3)
    base = datetime(2026, 9, 14, 11, 0, tzinfo=timezone.utc)
    finding = None
    for i, cam in enumerate(["CAM-011", "CAM-022", "CAM-045"]):
        finding = tracker.observe(_read("MH12DE1433"), cam,
                                  base + timedelta(hours=i * 5))
    assert finding is not None
    assert len(finding["cameras"]) == 3


def test_low_quality_reads_never_feed_the_pattern():
    """A miscorrected plate could attach a pattern to a real registration that
    never made those crossings. Only dependable reads count."""
    tracker = RepeatPlateTracker(min_sightings=2)
    base = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    for day in range(5):
        assert tracker.observe(_read("WB24AB1234", above=False), "CAM-011",
                               base + timedelta(days=day)) is None
    for day in range(5):
        assert tracker.observe(_read("GARBAGE", valid=False), "CAM-011",
                               base + timedelta(days=day)) is None


def test_sightings_outside_the_window_are_forgotten():
    tracker = RepeatPlateTracker(window_hours=24.0, min_sightings=3)
    base = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    tracker.observe(_read("WB24AB1234"), "CAM-011", base)
    tracker.observe(_read("WB24AB1234"), "CAM-011", base + timedelta(days=10))
    assert len(tracker.history_for("WB 24 AB 1234")) == 1

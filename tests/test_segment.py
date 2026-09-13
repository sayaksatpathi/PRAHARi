"""Tests for the event-triggered segmentation refinement module.

Cover the parts that carry weight: that GrabCut produces a real mask with a
correct ground-contact point, that the mask-in-zone refinement distinguishes an
object genuinely inside a zone from a box that merely clips its corner, and that
backend selection and the fallback behave.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.models import BBox, Point, Zone, ZoneKind
from prahari.edge.segment.base import Segmenter, SegmentResult, finalise_mask
from prahari.edge.segment.grabcut import GrabCutSegmenter
from prahari.edge.segment.refine import refine_event


def _scene():
    """A pale object on a distinct ground, with a box around it."""
    img = np.full((300, 400, 3), (60, 90, 70), np.uint8)
    cv2.rectangle(img, (170, 90), (230, 240), (205, 185, 165), -1)
    cv2.circle(img, (200, 78), 18, (205, 185, 165), -1)
    box = BBox(x1=172, y1=60, x2=228, y2=238)
    return img, box


# --- GrabCut ---------------------------------------------------------------

def test_grabcut_segments_object():
    img, box = _scene()
    r = GrabCutSegmenter().segment(img, box)
    assert r is not None
    assert r.approximate is True
    assert r.area_px > 5000, "mask should cover most of the ~10k px object"
    assert len(r.polygon) >= 3


def test_grabcut_ground_contact_is_below_box_centre():
    img, box = _scene()
    r = GrabCutSegmenter().segment(img, box)
    gx, gy = r.ground_contact
    # The true object bottom is ~240; the foot point should be near it and
    # roughly horizontally centred on the object (~200).
    assert abs(gy - 240) < 20
    assert abs(gx - 200) < 40


def test_grabcut_tiny_box_returns_none():
    img, _ = _scene()
    assert GrabCutSegmenter().segment(img, BBox(x1=10, y1=10, x2=14, y2=14)) is None


# --- finalise_mask ---------------------------------------------------------

def test_finalise_mask_computes_area_and_foot():
    crop = np.zeros((50, 40), np.uint8)
    crop[10:45, 8:32] = 1                      # a block, bottom row at 44
    r = finalise_mask(crop, offset=(100, 200), backend="test",
                      approximate=True, latency_ms=1.0)
    assert r is not None
    assert r.area_px == 35 * 24
    # Ground contact is the bottom row of the block, offset into the frame.
    assert r.ground_contact[1] == 200 + 44


def test_finalise_mask_rejects_empty():
    assert finalise_mask(np.zeros((20, 20), np.uint8), (0, 0), "t",
                         approximate=True, latency_ms=0) is None


# --- refinement: mask-in-zone ---------------------------------------------

class _FixedSegmenter(Segmenter):
    """Returns a controllable rectangular mask, to test refinement precisely."""
    name = "fixed (test)"

    def __init__(self, rect):
        self._rect = rect      # (x1,y1,x2,y2) in full-frame coords

    def segment(self, image, box):
        x1, y1, x2, y2 = self._rect
        crop = np.ones((y2 - y1, x2 - x1), np.uint8)
        return finalise_mask(crop, offset=(x1, y1), backend=self.name,
                             approximate=True, latency_ms=0.0)


def _bottom_zone():
    # A polygon covering the bottom half of a 400x300 frame (normalised).
    return Zone(zone_id="Z1", camera_id="C", name="Restricted", kind=ZoneKind.POLYGON,
                points=[Point(x=0.0, y=0.5), Point(x=1.0, y=0.5),
                        Point(x=1.0, y=1.0), Point(x=0.0, y=1.0)])


def test_object_inside_zone_strengthens():
    img = np.zeros((300, 400, 3), np.uint8)
    # Mask entirely within the bottom half (y 200..260 of 300 -> normalised >0.5).
    seg = _FixedSegmenter((150, 200, 250, 260))
    box = BBox(x1=150, y1=200, x2=250, y2=260)
    ref = refine_event(seg, img, box, _bottom_zone(), box_says_in_zone=True)
    assert ref is not None
    assert ref.mask_in_zone_fraction > 0.9
    assert ref.ground_contact_in_zone is True
    assert ref.score_adjustment > 0


def test_box_clips_zone_but_object_outside_weakens():
    img = np.zeros((300, 400, 3), np.uint8)
    # Object sits in the TOP half (y 20..140 -> normalised <0.5), fully outside
    # the bottom zone, even though a loose box might have dipped a corner in.
    seg = _FixedSegmenter((150, 20, 250, 140))
    box = BBox(x1=150, y1=20, x2=250, y2=210)   # box bottom reaches into zone
    ref = refine_event(seg, img, box, _bottom_zone(), box_says_in_zone=True)
    assert ref is not None
    assert ref.mask_in_zone_fraction < 0.05
    assert ref.score_adjustment < 0, "a clipped-corner event should be weakened"


def test_refine_returns_none_when_segmenter_finds_nothing():
    class _Blind(Segmenter):
        name = "blind"
        def segment(self, image, box):
            return None
    img = np.zeros((300, 400, 3), np.uint8)
    ref = refine_event(_Blind(), img, BBox(x1=0, y1=0, x2=10, y2=10),
                       _bottom_zone(), box_says_in_zone=True)
    assert ref is None


# --- contains_fraction -----------------------------------------------------

def test_contains_fraction_all_and_none():
    crop = np.ones((20, 20), np.uint8)
    seg = finalise_mask(crop, offset=(0, 0), backend="t",
                        approximate=True, latency_ms=0)
    assert seg.contains_fraction(lambda x, y: True) == 1.0
    assert seg.contains_fraction(lambda x, y: False) == 0.0


# --- factory ---------------------------------------------------------------

def test_factory_off_returns_none():
    from prahari.edge.segment.factory import build_segmenter

    class S:
        segment_backend = "off"
    assert build_segmenter(S()) is None


def test_factory_grabcut_explicit():
    from prahari.edge.segment.factory import build_segmenter

    class S:
        segment_backend = "grabcut"
    seg = build_segmenter(S())
    assert isinstance(seg, GrabCutSegmenter)


def test_factory_auto_falls_back_to_grabcut_without_weights(tmp_path):
    from prahari.edge.segment.factory import build_segmenter

    class S:
        segment_backend = "auto"
        sam_encoder_path = tmp_path / "nope_enc.onnx"
        sam_decoder_path = tmp_path / "nope_dec.onnx"
        device = "cpu"
    seg = build_segmenter(S())
    assert isinstance(seg, GrabCutSegmenter)

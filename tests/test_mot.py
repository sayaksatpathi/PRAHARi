"""Tests for the MOT real-footage adapter and the detection-only harness.

These validate the entire real-footage evaluation path against a self-made MOT
sequence with known ground truth, so that when a real dataset is present the
plumbing is already proven and only the imagery and model are new. The multi-GB
public datasets cannot be downloaded in CI (or, as it happens, on the build
machine's connection), which is exactly why the path must be tested against a
fixture the test controls.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.config import get_settings
from prahari.common.models import BBox, Camera, CameraRole, Detection, ObjectClass
from prahari.edge.detect.base import Detector
from prahari.eval.harness import EvaluationHarness
from prahari.eval.mot import MotSequenceSource, discover_sequences, load_gt


# --- a MOT sequence fixture we fully control -------------------------------

def _make_mot_sequence(root: Path, frames: int = 12,
                       box=(100, 80, 60, 140)) -> Path:
    """Write a minimal but valid MOT sequence: seqinfo, frames, gt.

    A single pedestrian box that drifts across frames, so the tracker has
    something coherent to follow and the ground-plane fit has depth variation.
    """
    seq = root / "MOT-FIXTURE-01"
    (seq / "img1").mkdir(parents=True)
    (seq / "gt").mkdir(parents=True)

    W, H = 640, 480
    (seq / "seqinfo.ini").write_text(
        "[Sequence]\n"
        "name=MOT-FIXTURE-01\n"
        "imDir=img1\n"
        "frameRate=25\n"
        f"seqLength={frames}\n"
        f"imWidth={W}\n"
        f"imHeight={H}\n"
        "imExt=.jpg\n"
    )

    gt_lines = []
    x0, y0, w, h = box
    for i in range(1, frames + 1):
        img = np.full((H, W, 3), 40, dtype=np.uint8)
        # Person drifts right and slightly down (nearer), giving depth spread.
        x = x0 + i * 6
        y = y0 + i * 4
        bw = w + i          # grows as it nears the camera
        bh = h + i * 2
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (200, 200, 200), -1)
        cv2.imwrite(str(seq / "img1" / f"{i:06d}.jpg"), img)
        # frame,id,x,y,w,h,conf,class,visibility
        gt_lines.append(f"{i},1,{x},{y},{bw},{bh},1,1,1.0")
    (seq / "gt" / "gt.txt").write_text("\n".join(gt_lines) + "\n")
    return seq


# --- controllable detectors ------------------------------------------------

class PerfectDetector(Detector):
    """Returns exactly the ground-truth boxes - a matcher self-check."""
    name = "perfect (test)"

    def infer(self, image, *, allowed=None, frame_index=0, context=None):
        gt = (context or {}).get("ground_truth") or []
        out = []
        for g in gt:
            x1, y1, x2, y2 = g["bbox"]
            out.append(Detection(
                object_class=ObjectClass.PERSON, confidence=0.9,
                bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2), frame_index=frame_index))
        return out

    def describe(self):
        return {"name": self.name, "simulated": False}


class BlindDetector(Detector):
    name = "blind (test)"

    def infer(self, image, *, allowed=None, frame_index=0, context=None):
        return []

    def describe(self):
        return {"name": self.name, "simulated": False}


# --- GT parsing ------------------------------------------------------------

def test_load_gt_parses_pedestrians(tmp_path):
    seq = _make_mot_sequence(tmp_path)
    gt = load_gt(seq / "gt" / "gt.txt")
    assert len(gt) == 12
    box = gt[1][0]
    assert box["object_class"] == "person"
    assert len(box["bbox"]) == 4
    assert box["bbox"][2] > box["bbox"][0]      # x2 > x1


def test_load_gt_filters_non_pedestrian_and_ignore(tmp_path):
    gt_path = tmp_path / "gt.txt"
    gt_path.write_text(
        "1,1,10,10,20,40,1,1,1.0\n"      # pedestrian, kept
        "1,2,50,50,20,40,1,7,1.0\n"      # class 7 (vehicle), dropped
        "1,3,80,80,20,40,0,1,1.0\n"      # conf 0 (ignore), dropped
        "1,4,90,90,20,40,1,1,0.05\n"     # visibility below floor, dropped
    )
    gt = load_gt(gt_path)
    assert len(gt[1]) == 1
    assert gt[1][0]["bbox"][0] == 10.0


def test_discover_finds_single_sequence(tmp_path):
    seq = _make_mot_sequence(tmp_path)
    found = discover_sequences(seq)
    assert found == [seq]


def test_discover_finds_sequences_under_root(tmp_path):
    _make_mot_sequence(tmp_path)
    found = discover_sequences(tmp_path)
    assert len(found) == 1


# --- source ----------------------------------------------------------------

def test_mot_source_serves_frames_with_gt(tmp_path):
    seq = _make_mot_sequence(tmp_path, frames=6)
    src = MotSequenceSource(seq)
    assert src.open()
    assert src.width == 640 and src.height == 480
    assert not src.is_simulated

    seen = 0
    while True:
        frame = src.read()
        if frame is None:
            break
        seen += 1
        assert frame.ground_truth, "each frame should carry its GT box"
        assert frame.ground_truth[0]["object_class"] == "person"
    assert seen == 6
    src.close()


def test_mot_source_missing_dir_fails_cleanly(tmp_path):
    src = MotSequenceSource(tmp_path / "does-not-exist")
    assert src.open() is False


# --- detection-only harness end to end -------------------------------------

def _harness(seq, detector):
    src = MotSequenceSource(seq)
    camera = Camera(camera_id=seq.name, name=seq.name, role=CameraRole.APPROACH,
                    source_kind="file", claimed_width=640, claimed_height=480,
                    claimed_fps=25.0)
    return EvaluationHarness(camera=camera, source=src, detector=detector,
                             zones=[], settings=get_settings(),
                             true_camera_height_m=None)


def test_perfect_detector_scores_full_recall(tmp_path):
    seq = _make_mot_sequence(tmp_path, frames=16)
    result = _harness(seq, PerfectDetector()).run_detection_only(
        profile_frames=0, eval_frames=16)
    d = result.as_dict()
    # With no profiling warm-up, every frame is scored; a perfect detector must
    # hit every GT box.
    assert d["detection"]["recall"] == 1.0
    assert d["detection"]["false_negative"] == 0
    assert d["detection"]["true_positive"] == 16


def test_blind_detector_scores_zero_recall(tmp_path):
    seq = _make_mot_sequence(tmp_path, frames=16)
    result = _harness(seq, BlindDetector()).run_detection_only(
        profile_frames=0, eval_frames=16)
    d = result.as_dict()
    assert d["detection"]["recall"] == 0.0
    assert d["detection"]["false_negative"] == 16
    assert d["detection"]["true_positive"] == 0


def test_detection_only_reports_throughput_and_frames(tmp_path):
    seq = _make_mot_sequence(tmp_path, frames=10)
    result = _harness(seq, PerfectDetector()).run_detection_only(
        profile_frames=0, eval_frames=10)
    d = result.as_dict()
    assert d["frames"] == 10
    assert d["performance"]["throughput_fps"] > 0
    assert d["source_simulated"] is False

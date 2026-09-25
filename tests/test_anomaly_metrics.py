"""Tests for the Street Scene VAD metrics (frame AUC, RBDC, TBDC).

These lock the metric implementation to known-correct behaviour on synthetic
inputs, so the benchmark numbers rest on a verified scorer, not just a run.
"""
from __future__ import annotations

import numpy as np

from prahari.eval.anomaly import (
    frame_level_auc, iou, link_tracks, rbdc_tbdc,
)


def _gt_three_frames():
    gt = [
        [np.array([10, 10, 30, 30], float)],
        [np.array([12, 10, 32, 30], float)],
        [np.array([14, 10, 34, 30], float)],
    ]
    return gt, link_tracks(gt)


def test_iou_bounds():
    b = np.array([0, 0, 10, 10], float)
    assert iou(b, b) == 1.0
    assert iou(b, np.array([100, 100, 110, 110], float)) == 0.0
    half = iou(b, np.array([5, 0, 15, 10], float))
    assert 0.3 < half < 0.34            # overlap 50/150


def test_perfect_detector_scores_one():
    gt, tracks = _gt_three_frames()
    assert len(tracks) == 1 and tracks[0].length == 3
    pred = [[(b.copy(), 0.9) for b in frame] for frame in gt]
    r = rbdc_tbdc(gt, tracks, pred)
    assert r["rbdc"] == 1.0
    assert r["tbdc"] == 1.0


def test_no_detections_scores_zero():
    gt, tracks = _gt_three_frames()
    r = rbdc_tbdc(gt, tracks, [[] for _ in gt])
    assert r["rbdc"] == 0.0
    assert r["tbdc"] == 0.0


def test_wrong_location_scores_zero():
    gt, tracks = _gt_three_frames()
    fp = [[(np.array([100, 100, 120, 120], float), 0.9)] for _ in gt]
    r = rbdc_tbdc(gt, tracks, fp)
    assert r["rbdc"] == 0.0
    assert r["tbdc"] == 0.0


def test_partial_track_detection():
    """A track detected in 2 of 3 frames -> TBDC credit 2/3."""
    gt, tracks = _gt_three_frames()
    pred = [
        [(gt[0][0].copy(), 0.9)],
        [(gt[1][0].copy(), 0.9)],
        [],                                  # third frame missed
    ]
    r = rbdc_tbdc(gt, tracks, pred)
    # region TPR maxes at 2/3; TBDC track credit 2/3. With FP=0 the curve is flat
    # at that height across the [0,1] FP band, so both areas == 2/3.
    assert abs(r["rbdc"] - 2 / 3) < 1e-6
    assert abs(r["tbdc"] - 2 / 3) < 1e-6


def test_frame_auc():
    assert frame_level_auc([0.9, 0.8, 0.85, 0.1, 0.2], [1, 1, 1, 0, 0]) == 1.0
    # a fully wrong ranking -> 0.0
    assert frame_level_auc([0.1, 0.2, 0.15, 0.9, 0.8], [1, 1, 1, 0, 0]) == 0.0

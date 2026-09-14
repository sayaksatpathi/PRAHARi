"""CLEAR-MOT and IDF1 scoring.

These tests exist because the MOT17 numbers this project reports are only worth
as much as the scorer that produced them. A metric implementation that is subtly
wrong produces plausible figures, and plausible figures are worse than none: they
get quoted. So the scorer is checked against cases whose answers can be worked
out by hand, including the ones that distinguish a correct CLEAR-MOT from a
naive one - carried-forward matches, ID switches, and MOTA going negative.
"""
from __future__ import annotations

import pytest

from prahari.eval.tracking import (
    TrackingScorer,
    ground_truth_as_pairs,
    tracks_as_pairs,
)


def box(x, y, w=20, h=40):
    return [x, y, x + w, y + h]


# =====================================================================
# The easy cases, which must be exactly right
# =====================================================================

def test_perfect_tracking_scores_one():
    s = TrackingScorer()
    for i in range(10):
        s.update([(1, box(i * 5, 0)), (2, box(200 + i * 5, 0))],
                 [(101, box(i * 5, 0)), (102, box(200 + i * 5, 0))])
    d = s.as_dict()
    assert d["mota"] == 1.0
    assert d["motp_iou"] == 1.0
    assert d["idf1"] == 1.0
    assert d["id_switches"] == 0
    assert d["misses_fn"] == 0
    assert d["false_positives"] == 0
    assert d["mostly_tracked"] == 2


def test_detecting_nothing_scores_zero_not_an_error():
    s = TrackingScorer()
    for i in range(10):
        s.update([(1, box(i, 0))], [])
    d = s.as_dict()
    assert d["mota"] == 0.0          # 1 - 10/10
    assert d["misses_fn"] == 10
    assert d["idf1"] == 0.0
    assert d["mostly_lost"] == 1


def test_mota_goes_negative_when_false_positives_exceed_ground_truth():
    """A detector hallucinating more boxes than there are people scores below
    zero. That is the metric working, and it must not be clamped."""
    s = TrackingScorer()
    for i in range(10):
        s.update([(1, box(0, 0))],
                 [(101, box(0, 0)), (102, box(300, 0)), (103, box(400, 0))])
    d = s.as_dict()
    assert d["false_positives"] == 20
    assert d["mota"] == pytest.approx(1.0 - 20 / 10)
    assert d["mota"] < 0


# =====================================================================
# The cases that separate a correct CLEAR-MOT from a naive one
# =====================================================================

def test_an_identity_swap_is_counted_once_not_every_frame():
    """The tracker swaps a person's id halfway. That is one switch.

    Without carrying matches forward, a scorer re-solves the assignment each
    frame and charges a switch on every frame after the swap - which turns one
    real error into fifty and makes the metric useless.
    """
    s = TrackingScorer()
    for _ in range(5):
        s.update([(1, box(0, 0))], [(101, box(0, 0))])
    for _ in range(5):
        s.update([(1, box(0, 0))], [(202, box(0, 0))])
    d = s.as_dict()
    assert d["id_switches"] == 1
    assert d["misses_fn"] == 0
    assert d["false_positives"] == 0


def test_a_fragmented_track_keeps_mota_high_but_drops_idf1():
    """The failure that breaks dwell time and cross-camera handoff.

    Every frame is detected, so CLEAR-MOT sees almost nothing wrong. But the
    person is shattered into ten one-frame identities, and IDF1 - which scores
    identities over the whole sequence - reports it.
    """
    s = TrackingScorer()
    for i in range(10):
        s.update([(1, box(0, 0))], [(100 + i, box(0, 0))])
    d = s.as_dict()
    assert d["mota"] >= 0.0
    assert d["id_switches"] == 9
    assert d["idf1"] < 0.3, d
    assert d["gt_tracks"] == 1
    assert d["predicted_tracks"] == 10


def test_a_match_below_the_iou_threshold_is_a_miss_and_a_false_positive():
    s = TrackingScorer(iou_threshold=0.5)
    s.update([(1, box(0, 0))], [(101, box(60, 0))])      # no overlap at all
    d = s.as_dict()
    assert d["matches"] == 0
    assert d["misses_fn"] == 1
    assert d["false_positives"] == 1


def test_motp_reports_box_tightness_independently_of_count():
    """Loose but consistently-found boxes: MOTA perfect, MOTP not."""
    s = TrackingScorer(iou_threshold=0.3)
    for _ in range(10):
        s.update([(1, box(0, 0, 20, 40))], [(101, box(0, 0, 30, 40))])
    d = s.as_dict()
    assert d["mota"] == 1.0
    assert 0.6 < d["motp_iou"] < 0.7      # 20x40 inside 30x40 => IoU = 2/3


def test_partially_tracked_sits_between_mostly_tracked_and_mostly_lost():
    s = TrackingScorer()
    for i in range(10):
        preds = [(101, box(0, 0))] if i < 5 else []
        s.update([(1, box(0, 0))], preds)
    d = s.as_dict()
    assert d["mostly_tracked"] == 0
    assert d["partially_tracked"] == 1
    assert d["mostly_lost"] == 0


# =====================================================================
# Adapters
# =====================================================================

def test_ground_truth_entries_without_an_identity_are_skipped():
    """A fabricated id would make every frame look like an ID switch."""
    entries = [
        {"object_class": "person", "bbox": [0, 0, 10, 20], "track_id": 3},
        {"object_class": "person", "bbox": [5, 5, 15, 25]},          # no id
        {"object_class": "car", "bbox": [0, 0, 10, 20], "track_id": 9},
    ]
    pairs = ground_truth_as_pairs(entries)
    assert pairs == [(3, [0, 0, 10, 20])]


def test_tracks_convert_to_scorer_pairs():
    from datetime import datetime, timezone

    from prahari.common.models import BBox, ObjectClass, Track

    now = datetime.now(timezone.utc)
    track = Track(track_id=7, camera_id="CAM-1", object_class=ObjectClass.PERSON,
                  first_seen=now, last_seen=now,
                  bbox=BBox(x1=1, y1=2, x2=3, y2=4), confidence=0.9)
    assert tracks_as_pairs([track]) == [(7, [1.0, 2.0, 3.0, 4.0])]


def test_result_reports_which_assignment_was_used():
    """Greedy IDF1 is a lower bound, and the report must say so rather than
    presenting it as the metric."""
    d = TrackingScorer().as_dict()
    assert "optimal" in d["assignment"] or "greedy" in d["assignment"]


def test_empty_scorer_is_safe_to_report():
    d = TrackingScorer().as_dict()
    assert d["frames_scored"] == 0
    assert d["mota"] == 0.0
    assert d["idf1"] == 0.0

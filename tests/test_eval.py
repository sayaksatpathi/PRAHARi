"""Tests for the evaluation metrics.

These validate the harness's own plumbing - the matching and counting logic that
every reported number rests on. A metric you cannot trust to count correctly is
worse than no metric, so the matcher is tested against hand-checked cases before
any of its output is quoted.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.models import BBox, ObjectClass
from prahari.eval.metrics import DetectionCounts, EventScoring, match_detections


def _box(x1, y1, x2, y2):
    return BBox(x1=x1, y1=y1, x2=x2, y2=y2)


def test_perfect_match():
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    pred = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    c = match_detections(pred, gt)
    assert (c.true_positive, c.false_positive, c.false_negative) == (1, 0, 0)
    assert c.precision == 1.0 and c.recall == 1.0 and c.f1 == 1.0


def test_missed_detection_is_a_false_negative():
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    c = match_detections([], gt)
    assert (c.true_positive, c.false_positive, c.false_negative) == (0, 0, 1)
    assert c.recall == 0.0


def test_phantom_detection_is_a_false_positive():
    pred = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    c = match_detections(pred, [])
    assert (c.true_positive, c.false_positive, c.false_negative) == (0, 1, 0)
    assert c.precision == 0.0


def test_wrong_class_penalised_on_both_sides():
    """A box in the right place but the wrong class is counted a false positive
    (wrong label) and a false negative (true object missed). Cattle called a
    person must not be scored a hit."""
    gt = [(ObjectClass.CATTLE, _box(0, 0, 10, 20))]
    pred = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    c = match_detections(pred, gt)
    assert c.true_positive == 0
    assert c.false_positive == 1
    assert c.false_negative == 1
    assert c.by_class["person"]["fp"] == 1
    assert c.by_class["cattle"]["fn"] == 1


def test_low_iou_does_not_match():
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    pred = [(ObjectClass.PERSON, _box(50, 50, 60, 70))]
    c = match_detections(pred, gt, iou_threshold=0.4)
    assert c.true_positive == 0
    assert c.false_positive == 1 and c.false_negative == 1


def test_greedy_matches_best_overlap_first():
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    pred = [
        (ObjectClass.PERSON, _box(2, 2, 12, 22)),    # partial
        (ObjectClass.PERSON, _box(0, 0, 10, 20)),    # exact - must win the GT
    ]
    c = match_detections(pred, gt)
    assert c.true_positive == 1
    assert c.false_positive == 1     # the partial box is left over
    assert c.false_negative == 0


def test_counts_accumulate_across_frames():
    c = DetectionCounts()
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    pred = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]
    for _ in range(5):
        match_detections(pred, gt, counts=c)
    assert c.true_positive == 5
    assert c.recall == 1.0


def test_per_class_breakdown():
    gt = [(ObjectClass.PERSON, _box(0, 0, 10, 20)),
          (ObjectClass.CAR, _box(30, 30, 60, 50))]
    pred = [(ObjectClass.PERSON, _box(0, 0, 10, 20))]     # car missed
    c = match_detections(pred, gt)
    assert c.by_class["person"]["tp"] == 1
    assert c.by_class["car"]["fn"] == 1


def test_event_scoring_reports_suppression():
    ev = EventScoring()
    ev.total_events = 100
    ev.alerted_events = 8
    ev.true_events = 5
    ev.alerted_true_events = 5
    ev.false_alarm_events = 95
    ev.alerted_false_alarms = 3
    d = ev.as_dict(run_seconds=45.0)
    assert d["alert_suppression_rate"] == round(1 - 8 / 100, 3)
    assert d["alerted_false_alarms"] == 3
    assert d["raw_false_alarm_events"] == 95
    # The headline per-hour uses alerted, not raw.
    assert d["false_alarms_per_hour"] == round(3 / (45 / 3600), 1)


def test_event_scoring_zero_events():
    """No events must not divide by zero."""
    d = EventScoring().as_dict(run_seconds=45.0)
    assert d["alert_suppression_rate"] == 0.0
    assert d["intruder_detected"] is False

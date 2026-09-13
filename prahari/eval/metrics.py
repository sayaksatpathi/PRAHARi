"""Evaluation metrics.

Deliberately small and readable rather than pulling in a metrics framework: the
whole point of an evaluation harness for a system like this is that the numbers
can be trusted, and a number you cannot derive by reading forty lines of code is
a number a judge is right to distrust.

Everything here is honest about what it can and cannot measure. In particular,
detection recall against ground truth measures a real quantity *only* when the
detector is a real model on real imagery. With the synthetic detector it measures
that detector's modelled miss rate - which is itself real and worth reporting,
but it is a property of the simulation, not of a trained network. The harness
labels which case produced each number; the metrics here just compute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prahari.common.models import BBox, ObjectClass


# =====================================================================
# Detection: precision / recall against ground-truth boxes
# =====================================================================

@dataclass
class DetectionCounts:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    # Per-class, so "misses everything beyond 40 m" and "confuses cattle for
    # people" show up as distinct failures rather than a single blurred number.
    by_class: dict[str, dict[str, int]] = field(default_factory=dict)

    def _bump(self, cls: str, key: str) -> None:
        self.by_class.setdefault(cls, {"tp": 0, "fp": 0, "fn": 0})[key] += 1

    @property
    def precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict[str, Any]:
        per_class = {}
        for cls, c in sorted(self.by_class.items()):
            tp, fp, fn = c["tp"], c["fp"], c["fn"]
            p = tp / (tp + fp) if (tp + fp) else 0.0
            r = tp / (tp + fn) if (tp + fn) else 0.0
            per_class[cls] = {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": round(p, 3), "recall": round(r, 3),
            }
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "by_class": per_class,
        }


def match_detections(
    predictions: list[tuple[ObjectClass, BBox]],
    ground_truth: list[tuple[ObjectClass, BBox]],
    iou_threshold: float = 0.4,
    counts: DetectionCounts | None = None,
) -> DetectionCounts:
    """Greedy IoU matching between one frame's predictions and ground truth.

    Class-agnostic matching, then a class check: a prediction that overlaps a GT
    box but calls it the wrong class is scored as both a false positive (wrong
    label) and a false negative (the true object was missed). That is stricter
    than counting it a hit, and it is the right strictness for a border system
    where "vehicle, not person" changes the response.
    """
    counts = counts or DetectionCounts()
    used_gt: set[int] = set()

    # Highest-IoU pairs first.
    pairs = []
    for pi, (pcls, pbox) in enumerate(predictions):
        for gi, (gcls, gbox) in enumerate(ground_truth):
            iou = pbox.iou(gbox)
            if iou >= iou_threshold:
                pairs.append((iou, pi, gi))
    pairs.sort(reverse=True)

    matched_pred: set[int] = set()
    for _iou, pi, gi in pairs:
        if pi in matched_pred or gi in used_gt:
            continue
        matched_pred.add(pi)
        used_gt.add(gi)
        pcls = predictions[pi][0]
        gcls = ground_truth[gi][0]
        if pcls == gcls:
            counts.true_positive += 1
            counts._bump(gcls.value, "tp")
        else:
            # Localised but mislabelled: penalise on both sides.
            counts.false_positive += 1
            counts.false_negative += 1
            counts._bump(pcls.value, "fp")
            counts._bump(gcls.value, "fn")

    for pi in range(len(predictions)):
        if pi not in matched_pred:
            counts.false_positive += 1
            counts._bump(predictions[pi][0].value, "fp")
    for gi in range(len(ground_truth)):
        if gi not in used_gt:
            counts.false_negative += 1
            counts._bump(ground_truth[gi][0].value, "fn")

    return counts


# =====================================================================
# Event level: did the system react to what actually happened?
# =====================================================================

@dataclass
class EventScoring:
    """Scores generated events against a scripted ground-truth incident.

    This is the metric that matters most for this system, and the one a
    detection benchmark cannot give you: not "did you draw a box" but "did you
    raise the right alarm, in time, without crying wolf".

    Two false-alarm numbers are kept, and the distinction is the whole point of
    the alert governor. RAW counts every event the rule engine emitted;
    ALERTED counts only those the governor let interrupt an operator. The number
    a judge should care about is the alerted one - a recorded-but-suppressed
    event costs nobody's attention. The raw number is reported alongside it as
    the flood the governor is holding back.
    """
    intruder_detected: bool = False
    detection_latency_s: float | None = None    # appearance -> first true event
    true_events: int = 0                         # events attributable to the incident
    false_alarm_events: int = 0                  # raw: from ambient traffic / noise
    alerted_true_events: int = 0                 # true events that also alerted
    alerted_false_alarms: int = 0                # false alarms that got through the governor
    alerted_events: int = 0                      # of all events, how many interrupted
    total_events: int = 0

    def as_dict(self, run_seconds: float) -> dict[str, Any]:
        hours = max(run_seconds / 3600.0, 1e-9)
        return {
            "intruder_detected": self.intruder_detected,
            "detection_latency_s": (round(self.detection_latency_s, 2)
                                    if self.detection_latency_s is not None else None),
            "true_events": self.true_events,
            "alerted_true_events": self.alerted_true_events,
            "raw_false_alarm_events": self.false_alarm_events,
            "raw_false_alarms_per_hour": round(self.false_alarm_events / hours, 1),
            # The headline: false alarms that actually reached the operator.
            "alerted_false_alarms": self.alerted_false_alarms,
            "false_alarms_per_hour": round(self.alerted_false_alarms / hours, 1),
            "alerted_events": self.alerted_events,
            "total_events": self.total_events,
            "alert_suppression_rate": (
                round(1 - self.alerted_events / self.total_events, 3)
                if self.total_events else 0.0),
        }

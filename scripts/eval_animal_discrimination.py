"""Measure how much the class-aware scorer suppresses animal false alarms.

Cattle, strays and wildlife are the single largest false-alarm source on a border
video feed. Prahari does not filter them out (livestock is also a smuggled
commodity) — it classifies them and lowers their concern. This quantifies that:
the identical event, scored for a human vs an animal, and the animal alert rate
actually observed on the running node.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.models import BBox, EventType, ObjectClass, Priority
from prahari.edge.priority import ScoringContext, score_event


class _Track:
    def __init__(self, cls: ObjectClass):
        self.object_class = cls
        self.bbox = BBox(x1=0, y1=0, x2=60, y2=140)   # upright-sized
        self.confidence = 0.80
        self.speed_mps = 1.0
        self.dwell_seconds = 0.0
        self.track_id = 1


def matched_pair() -> None:
    print("=== Matched event: identical border line-crossing, human vs animal ===")
    ctx = ScoringContext(near_boundary=True)
    scores = {}
    for cls in (ObjectClass.PERSON, ObjectClass.CATTLE):
        s, factors, pri = score_event(EventType.LINE_CROSSING, _Track(cls), ctx)
        scores[cls] = (s, pri)
        cw = next((f.weight for f in factors if f.name == "object class"), 0.0)
        print(f"  {cls.value:8}  score {s:.3f}  {pri.value.upper():8}  "
              f"(object-class weight {cw:+.2f})")
    drop = scores[ObjectClass.PERSON][0] - scores[ObjectClass.CATTLE][0]
    print(f"  -> class-driven reduction: {drop:.3f} "
          f"({100 * drop / max(1e-6, scores[ObjectClass.PERSON][0]):.0f}% lower for livestock)")
    human_crit = scores[ObjectClass.PERSON][1].rank >= Priority.CRITICAL.rank
    animal_crit = scores[ObjectClass.CATTLE][1].rank >= Priority.CRITICAL.rank
    print(f"  -> human crosses the CRITICAL (always-alert) bar: {human_crit}; "
          f"animal: {animal_crit}")


def live_rate(db_path: str = "var/prahari-edge.db") -> None:
    print("\n=== Observed on the running node (if present) ===")
    if not Path(db_path).exists():
        print("  (no node DB found — skip)")
        return
    c = sqlite3.connect(db_path)
    for cls in ("person", "cattle"):
        row = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(alerted),0) FROM events WHERE object_class=?",
            (cls,)).fetchone()
        total, alerted = row[0], row[1]
        rate = (100 * alerted / total) if total else 0.0
        print(f"  {cls:8}  events {total:5}  alerted {alerted:5}  ({rate:.1f}%)")


if __name__ == "__main__":
    matched_pair()
    live_rate()

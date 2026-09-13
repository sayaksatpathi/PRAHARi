"""Learnt pattern of life.

The hardest problem in border video analytics is not detecting a person. It is
deciding which of the several hundred people a camera sees each day is worth
waking someone for. On an open border - Indo-Nepal and Indo-Bhutan are open by
treaty, with heavy lawful daily traffic - a rule that fires on "a person crossed
the line" fires thousands of times a day and is worthless.

So Prahari learns what each camera normally sees, per zone, per object class,
per hour of the week, and scores deviation from that rather than the bare event.
The 10:00 market crowd stops being interesting after the first morning. A single
walker at 03:00 on the same camera does not.

Design notes:

*   Hour-of-week, not hour-of-day: border traffic has a strong weekly cycle
    (market days, weekly haats), and collapsing that into a daily average throws
    away the signal that makes Saturday morning unremarkable.

*   Counts decay. A camera that was repointed six months ago should not be
    judged against what it used to see, so old observations lose weight.

*   The model reports how much evidence it has. A bucket with three observations
    has no business generating confident opinions, and the scorer weights its
    contribution accordingly rather than pretending otherwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from prahari.common.db import Database
from prahari.common.models import ObjectClass

HOURS_PER_WEEK = 168

# Half-life for observation decay, in days. Roughly a season: long enough to
# learn a stable weekly rhythm, short enough to follow a camera being repointed
# or a route being closed.
DECAY_HALF_LIFE_DAYS = 45.0


def hour_of_week(dt: datetime) -> int:
    """0..167, Monday 00:00 = 0."""
    return dt.weekday() * 24 + dt.hour


@dataclass
class NormalcyVerdict:
    ratio: float            # >1 means busier than this bucket normally is
    samples: int            # how many observations the judgement rests on
    expected: float
    observed_bucket: float
    detail: str

    @property
    def is_unusual(self) -> bool:
        return self.samples >= 5 and self.ratio >= 2.0

    @property
    def is_routine(self) -> bool:
        return self.samples >= 5 and self.ratio <= 0.6


class NormalcyModel:
    """Per-camera occupancy prior, persisted in the edge database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- learning --------------------------------------------------------
    def observe(
        self,
        camera_id: str,
        object_class: ObjectClass,
        when: datetime,
        zone_id: str = "",
        weight: float = 1.0,
    ) -> None:
        """Record that this camera saw this kind of thing at this time.

        Called once per confirmed track, not once per frame - otherwise a person
        standing still for two minutes would teach the model that this bucket is
        enormously busy, and the next genuine event there would be scored as
        routine.
        """
        self.db.bump_normalcy(
            camera_id=camera_id,
            zone_id=zone_id or "",
            object_class=object_class.value,
            hour_of_week=hour_of_week(when),
            amount=weight,
        )

    # -- scoring ---------------------------------------------------------
    def evaluate(
        self,
        camera_id: str,
        object_class: ObjectClass,
        when: datetime,
        zone_id: str = "",
    ) -> NormalcyVerdict:
        """How unusual is this observation, for this camera, at this hour?

        The ratio compares what this camera typically sees in an *average* hour
        against what it typically sees in *this* hour. A bucket that is normally
        far quieter than average yields a high ratio, so activity there counts
        for more.
        """
        how = hour_of_week(when)
        bucket = self.db.normalcy_count(camera_id, zone_id or "", object_class.value, how)
        total = self.db.normalcy_total(camera_id, object_class.value, zone_id or "")

        if total <= 0:
            return NormalcyVerdict(
                ratio=1.0, samples=0, expected=0.0, observed_bucket=0.0,
                detail="no pattern of life learnt for this camera yet",
            )

        average_bucket = total / HOURS_PER_WEEK
        # The +0.5 floor keeps a never-before-seen bucket from producing an
        # infinite ratio on the strength of a single observation.
        ratio = average_bucket / max(bucket, 0.5)

        day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][how // 24]
        hour = how % 24
        detail = (
            f"{day} {hour:02d}:00 on this camera normally sees {bucket:.0f} "
            f"{object_class.value} observations against a {average_bucket:.1f} "
            f"hourly average ({total:.0f} learnt in total)"
        )
        return NormalcyVerdict(
            ratio=round(float(ratio), 2),
            samples=int(total),
            expected=round(average_bucket, 2),
            observed_bucket=round(bucket, 1),
            detail=detail,
        )

    # -- maintenance -----------------------------------------------------
    def decay(self, days_elapsed: float = 1.0) -> None:
        """Age the counts so the model tracks change rather than accumulating
        an average of everything the camera has ever seen."""
        if days_elapsed <= 0:
            return
        factor = math.pow(0.5, days_elapsed / DECAY_HALF_LIFE_DAYS)
        self.db.execute("UPDATE normalcy SET count = count * ?", (factor,))
        # Drop buckets that have decayed into irrelevance, so the table does not
        # grow without bound on a node that runs for years.
        self.db.execute("DELETE FROM normalcy WHERE count < 0.05")

    def seed_baseline(
        self,
        camera_id: str,
        object_class: ObjectClass,
        busy_hours: range,
        busy_weight: float = 40.0,
        quiet_weight: float = 1.0,
        zone_id: str = "",
    ) -> None:
        """Pre-load a plausible daily rhythm.

        Used only by demo mode, so the normalcy model has something to reason
        against without waiting a week of wall-clock time for a camera to learn
        it. Clearly a demo affordance: a real deployment learns this from its own
        observations, and `docs/demo.md` says so.
        """
        for how in range(HOURS_PER_WEEK):
            hour = how % 24
            weight = busy_weight if hour in busy_hours else quiet_weight
            self.db.bump_normalcy(
                camera_id, zone_id or "", object_class.value, how, amount=weight
            )

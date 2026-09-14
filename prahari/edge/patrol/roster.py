"""The declared patrol roster.

Patrol profiles are configuration, not learnt state. Somebody with authority at
the post declares that a patrol walks this route, through these zones, in this
window, in this direction - and the system is then allowed to hold movement to
that declaration. That direction of causality matters: Prahari does not *infer*
which nightly movement is friendly and quietly stop reporting it, because a
system that learns to ignore whatever it sees often enough will eventually learn
to ignore an established smuggling route.

The roster is therefore:

*   explicit - every profile is written down, attributable and editable;
*   auditable - every change goes through the audit log;
*   revocable - `active=False` takes a profile out of play instantly, without
    deleting the record of what it used to permit.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from prahari.common.models import ObjectClass, PatrolProfile, PatrolWindow

log = logging.getLogger("prahari.patrol.roster")


class PatrolRoster:
    """Patrol profiles, held in memory and persisted to the node database."""

    def __init__(self, db=None) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._profiles: dict[str, PatrolProfile] = {}
        if db is not None:
            for profile in db.list_patrols():
                self._profiles[profile.patrol_id] = profile
            if self._profiles:
                log.info("loaded %d patrol profile(s)", len(self._profiles))

    # -- reads -----------------------------------------------------------
    def get(self, patrol_id: str) -> PatrolProfile | None:
        with self._lock:
            return self._profiles.get(patrol_id)

    def all(self) -> list[PatrolProfile]:
        with self._lock:
            return sorted(self._profiles.values(), key=lambda p: p.patrol_id)

    def active_for(self, camera_id: str) -> list[PatrolProfile]:
        """Active profiles whose route includes this camera."""
        with self._lock:
            return [p for p in self._profiles.values()
                    if p.active and p.covers_camera(camera_id)]

    def cameras_covered(self) -> set[str]:
        with self._lock:
            return {c for p in self._profiles.values() if p.active for c in p.cameras}

    # -- writes ----------------------------------------------------------
    def upsert(self, profile: PatrolProfile) -> PatrolProfile:
        with self._lock:
            self._profiles[profile.patrol_id] = profile
        if self.db is not None:
            self.db.upsert_patrol(profile)
        return profile

    def remove(self, patrol_id: str) -> bool:
        with self._lock:
            existed = self._profiles.pop(patrol_id, None) is not None
        if self.db is not None:
            self.db.delete_patrol(patrol_id)
        return existed

    def set_active(self, patrol_id: str, active: bool) -> PatrolProfile | None:
        with self._lock:
            profile = self._profiles.get(patrol_id)
            if profile is None:
                return None
            updated = profile.model_copy(update={"active": active})
            self._profiles[patrol_id] = updated
        if self.db is not None:
            self.db.upsert_patrol(updated)
        return updated

    def as_dict(self) -> dict:
        profiles = self.all()
        return {
            "profiles": [p.model_dump(mode="json") for p in profiles],
            "active": sum(1 for p in profiles if p.active),
            "total": len(profiles),
            "cameras_covered": sorted(self.cameras_covered()),
        }


# =====================================================================
# Demo roster
# =====================================================================

def demo_patrols(node_id: str, now: datetime | None = None) -> list[PatrolProfile]:
    """Two patrol profiles for the demonstration fleet.

    Windows are generated relative to the current local time rather than fixed
    at 02:00, for one reason: a demo is run at whatever hour the demo is run,
    and a patrol layer that can only be shown working in the small hours cannot
    be shown working. ALPHA's window is open now, so a patrol walked on its
    route right now is suppressed live; BRAVO's closed a short while ago, so the
    same movement on its route lands inside the tolerance band and is downgraded
    instead. That contrast is the point of the demonstration.

    Everything about these two profiles is demo scaffolding and is marked as
    such in `metadata`, so the bootstrap can refresh them without touching
    profiles a real operator has entered.
    """
    now = now or datetime.now()
    minute_now = now.hour * 60 + now.minute

    def window(start_offset: int, length: int, tolerance: int) -> PatrolWindow:
        start = (minute_now + start_offset) % 1440
        return PatrolWindow(
            start_minute=start,
            end_minute=(start + length) % 1440,
            tolerance_minutes=tolerance,
            label="generated for the demonstration, relative to node start",
        )

    return [
        PatrolProfile(
            patrol_id="PATROL-ALPHA",
            name="Alpha - North Fence Foot Patrol",
            sector=node_id,
            # The route: north perimeter, then the approach track, then the gate.
            # The same corridor the cross-camera coordinator knows about, which
            # is what lets route consistency be checked at all.
            cameras=["CAM-014", "CAM-022", "CAM-011"],
            zones=["CAM-014-ZONE-1", "CAM-014-LINE-1",
                   "CAM-022-ROUTE-1",
                   "CAM-011-ZONE-1", "CAM-011-LINE-1"],
            windows=[window(-6, 26, 18)],   # open now, a realistic transit
            # The demo restricted zones treat movement toward the camera as
            # inbound from the border; the patrol walks that way on its outward
            # leg, which is why a reversal is a meaningful signal here.
            expected_heading_deg=270.0,
            heading_tolerance_deg=70.0,
            object_classes=[ObjectClass.PERSON],
            max_group_size=4,
            min_speed_mps=0.4,
            max_speed_mps=2.2,
            identity_matching_enabled=False,
            max_suppressions_per_window=6,
            notes=("Two-man foot patrol, north fence line to the gate. Declared "
                   "by the duty officer; not inferred by the system."),
            metadata={"demo": True},
        ),
        PatrolProfile(
            patrol_id="PATROL-BRAVO",
            name="Bravo - South Track Vehicle Patrol",
            sector=node_id,
            cameras=["CAM-045"],
            zones=["CAM-045-ZONE-1", "CAM-045-LINE-1"],
            windows=[window(-46, 25, 25)],  # closed ~21 min ago: late, within tolerance
            expected_heading_deg=270.0,
            heading_tolerance_deg=80.0,
            object_classes=[ObjectClass.PERSON, ObjectClass.CAR,
                            ObjectClass.TRUCK, ObjectClass.MOTORCYCLE],
            max_group_size=6,
            min_speed_mps=0.0,
            max_speed_mps=9.0,
            # Identity is available on this route because the gate camera is
            # certified for ANPR, and the deployment has opted in for this
            # profile only. A plate that is read and is not on this list stops
            # the profile matching at all - identity is used to *withhold* a
            # suppression, never on its own to grant one.
            identity_matching_enabled=True,
            vehicle_plates=["WB24SSB001"],
            max_suppressions_per_window=4,
            notes=("Vehicle patrol on the south approach. Runs earlier than "
                   "Alpha, so movement seen now is late rather than scheduled."),
            metadata={"demo": True},
        ),
    ]


def seed_demo_patrols(roster: PatrolRoster, node_id: str,
                      now: datetime | None = None) -> list[PatrolProfile]:
    """Install/refresh the demo profiles, leaving operator-entered ones alone.

    Refreshed on every start because the windows are relative to the clock; a
    profile whose `metadata.demo` flag is absent is never touched.
    """
    seeded: list[PatrolProfile] = []
    for profile in demo_patrols(node_id, now):
        existing = roster.get(profile.patrol_id)
        if existing is not None and not existing.metadata.get("demo"):
            continue
        # Preserve an operator's active/inactive choice across a refresh.
        if existing is not None:
            profile = profile.model_copy(update={"active": existing.active})
        seeded.append(roster.upsert(profile))
    if seeded:
        log.info("demo patrol roster: %s",
                 ", ".join(f"{p.patrol_id} ({p.windows[0].describe()})"
                           for p in seeded if p.windows))
    return seeded

"""Cross-camera coordinator — the step from cameras to a corridor.

A single edge node owns all its cameras, so cross-camera reasoning lives on the
node rather than needing the sector core. The coordinator consumes confirmed
tracks from every camera pipeline and does three things a single camera cannot:

  * **Handoff.** When a track appears at a camera, it asks whether this is the
    same object that recently left a neighbouring camera — matched on the learned
    transition time, the object class, and a coarse appearance signature — and if
    so continues that object under one global identity across cameras.

  * **Corridor reasoning.** An object that entered a corridor and was travelling
    it, then went silent before reaching the far end, did not simply leave frame
    — it left the corridor. That is a `corridor_dropout`, and it is exactly the
    kind of thing a per-camera view cannot see.

  * **Sector-wide repeat detection.** The same entity reappearing across several
    cameras, or at odd hours, is a pattern; a global identity is what makes it
    visible.

The matching is deliberately conservative. A wrong handoff invents a journey that
did not happen, which is worse than two separate tracks, so an unmatched arrival
becomes a new entity rather than being forced onto a weak candidate. Findings are
returned as plain dicts for the node to turn into events, keeping this module pure
logic and fully testable without the pipeline around it.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from prahari.edge.crosscam.appearance import blend, similarity
from prahari.edge.crosscam.topology import Topology

# Minimum combined score to accept a handoff. Below this an arrival is treated as
# a new entity - a false link is more damaging than a missed one.
HANDOFF_ACCEPT = 0.45
# When both objects have an appearance signature, the match must actually look
# alike - good timing alone must not link two visibly different people. When a
# signature is absent (thermal, night, a crop too small), the coordinator falls
# back to timing and class with a tighter timing bar, because it has less to go
# on and a loose accept there would invent journeys.
APPEARANCE_MIN = 0.40
TIMING_ONLY_MIN = 0.60


def _synchronized(fn):
    """Guard a coordinator method with its instance lock (reentrant)."""
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@dataclass
class ExitRecord:
    global_id: int
    camera_id: str
    object_class: str
    signature: np.ndarray | None
    exit_time: datetime

    def age_s(self, now: datetime) -> float:
        return (now - self.exit_time).total_seconds()


@dataclass
class Entity:
    """One object, tracked across cameras under a single global identity."""
    global_id: int
    object_class: str
    signature: np.ndarray | None
    first_seen: datetime
    last_seen: datetime
    current_camera: str | None
    # (camera_id, enter_time, exit_time|None)
    sightings: list[list[Any]] = field(default_factory=list)
    handoffs: int = 0
    in_corridor: bool = False
    dropout_flagged: bool = False

    @property
    def cameras(self) -> list[str]:
        return sorted({s[0] for s in self.sightings})

    def as_dict(self) -> dict[str, Any]:
        return {
            "global_id": self.global_id,
            "object_class": self.object_class,
            "cameras": self.cameras,
            "handoffs": self.handoffs,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "current_camera": self.current_camera,
            "sightings": [
                {"camera": s[0],
                 "enter": s[1].isoformat(),
                 "exit": s[2].isoformat() if s[2] else None}
                for s in self.sightings
            ],
        }


class CrossCameraCoordinator:
    def __init__(self, topology: Topology, node_id: str = "node",
                 exit_retention_s: float = 240.0) -> None:
        self.topology = topology
        self.node_id = node_id
        self.exit_retention_s = exit_retention_s
        self._entities: dict[int, Entity] = {}
        self._open_exits: list[ExitRecord] = []
        self._ids = itertools.count(1)
        # Map a live (camera, local track id) to a global entity id.
        self._local_to_global: dict[tuple[str, int], int] = {}
        # Camera pipelines run in executor threads and all call this one
        # coordinator, so every mutating entry point is guarded.
        self._lock = threading.RLock()

    # -- track lifecycle -------------------------------------------------
    @_synchronized
    def on_track_confirmed(self, camera_id: str, track_id: int,
                           object_class: str, signature, when: datetime
                           ) -> dict[str, Any] | None:
        """A confirmed track appeared. Link it to a recent exit, or start anew.

        Returns a handoff finding when a link is made, else None.
        """
        key = (camera_id, track_id)
        if key in self._local_to_global:
            return None                      # already assigned

        match, edge, score = self._best_handoff(camera_id, object_class,
                                                 signature, when)
        if match is not None:
            entity = self._entities[match.global_id]
            entity.handoffs += 1
            entity.current_camera = camera_id
            entity.last_seen = when
            entity.signature = blend(entity.signature, signature)
            entity.sightings.append([camera_id, when, None])
            entity.in_corridor = True
            entity.dropout_flagged = False
            self._local_to_global[key] = entity.global_id
            self._open_exits.remove(match)
            if edge is not None:
                edge.observe_transition(match.age_s(when))
            return {
                "kind": "handoff",
                "global_id": entity.global_id,
                "from_camera": match.camera_id,
                "to_camera": camera_id,
                "object_class": object_class,
                "transition_s": round(match.age_s(when), 1),
                "score": round(score, 3),
                "cameras": entity.cameras,
                "summary": (
                    f"{object_class.title()} handed off "
                    f"{match.camera_id} → {camera_id} "
                    f"in {match.age_s(when):.0f}s (global #{entity.global_id})"),
            }

        # No match: a new global entity.
        gid = next(self._ids)
        self._entities[gid] = Entity(
            global_id=gid, object_class=object_class, signature=signature,
            first_seen=when, last_seen=when, current_camera=camera_id,
            sightings=[[camera_id, when, None]])
        self._local_to_global[key] = gid
        return None

    @_synchronized
    def on_track_update(self, camera_id: str, track_id: int, signature,
                        when: datetime) -> None:
        """A live track was seen again this frame. Keep the entity fresh."""
        gid = self._local_to_global.get((camera_id, track_id))
        if gid is None:
            return
        ent = self._entities.get(gid)
        if ent is None:
            return
        ent.last_seen = when
        if signature is not None:
            ent.signature = blend(ent.signature, signature, alpha=0.15)

    @_synchronized
    def on_track_lost(self, camera_id: str, track_id: int,
                      when: datetime) -> None:
        """A track retired. Open it for handoff to a neighbouring camera."""
        key = (camera_id, track_id)
        gid = self._local_to_global.pop(key, None)
        if gid is None:
            return
        ent = self._entities.get(gid)
        if ent is None:
            return
        ent.current_camera = None
        for s in reversed(ent.sightings):
            if s[0] == camera_id and s[2] is None:
                s[2] = when
                break

        # If this camera has outgoing corridor edges, the object is expected
        # somewhere next; open an exit and mark it a corridor traveller.
        if self.topology.edges_from(camera_id):
            ent.in_corridor = True
            for edge in self.topology.edges_from(camera_id):
                edge.departures += 1
            self._open_exits.append(ExitRecord(
                global_id=gid, camera_id=camera_id,
                object_class=ent.object_class, signature=ent.signature,
                exit_time=when))

    # -- periodic reasoning ----------------------------------------------
    @_synchronized
    def tick(self, now: datetime) -> list[dict[str, Any]]:
        """Expire stale exits and raise corridor-dropout findings."""
        findings: list[dict[str, Any]] = []

        # Corridor dropout: a corridor traveller that exited and has not been
        # seen anywhere for longer than the widest outgoing window it could have
        # taken. It should have arrived at the next camera by now; it did not.
        still_open: list[ExitRecord] = []
        for rec in self._open_exits:
            edges = self.topology.edges_from(rec.camera_id)
            max_hi = max((e.window()[1] for e in edges), default=self.exit_retention_s)
            age = rec.age_s(now)
            ent = self._entities.get(rec.global_id)
            if age <= max_hi:
                still_open.append(rec)
                continue
            # Past every plausible arrival window and never claimed by a handoff.
            if ent is not None and not ent.dropout_flagged and ent.handoffs >= 1:
                # Only flag genuine corridor travellers (already handed off at
                # least once), so a single-camera track that merely ended does
                # not masquerade as a disappearance.
                ent.dropout_flagged = True
                findings.append({
                    "kind": "corridor_dropout",
                    "global_id": rec.global_id,
                    "camera_id": rec.camera_id,
                    "object_class": rec.object_class,
                    "expected_at": self.topology.neighbours(rec.camera_id),
                    "silent_for_s": round(age, 1),
                    "summary": (
                        f"{rec.object_class.title()} (global #{rec.global_id}) "
                        f"entered the corridor at {rec.camera_id} and never "
                        f"reached {', '.join(self.topology.neighbours(rec.camera_id))} "
                        f"- silent for {age:.0f}s"),
                })
            # Drop it either way; it is no longer an open handoff candidate.
        # Also expire very old exits regardless.
        self._open_exits = [r for r in still_open
                            if r.age_s(now) <= self.exit_retention_s]
        return findings

    # -- matching --------------------------------------------------------
    def _best_handoff(self, camera_id: str, object_class: str, signature,
                      when: datetime):
        best = None
        best_edge = None
        best_score = 0.0
        for edge in self.topology.edges_to(camera_id):
            lo, hi = edge.window()
            for rec in self._open_exits:
                if rec.camera_id != edge.src:
                    continue
                if rec.object_class != object_class:
                    continue
                elapsed = rec.age_s(when)
                if not (lo <= elapsed <= hi):
                    continue
                timing = edge.timing_score(elapsed)

                have_both = signature is not None and rec.signature is not None
                if have_both:
                    appear = similarity(signature, rec.signature)
                    # Appearance must actually agree; timing cannot rescue a
                    # visibly different object.
                    if appear < APPEARANCE_MIN:
                        continue
                    score = 0.5 * timing + 0.5 * appear
                else:
                    # No appearance evidence: timing and class only, tighter bar.
                    if timing < TIMING_ONLY_MIN:
                        continue
                    score = timing * 0.75      # discounted for the missing cue

                if score > best_score:
                    best, best_edge, best_score = rec, edge, score
        if best is not None and best_score >= HANDOFF_ACCEPT:
            return best, best_edge, best_score
        return None, None, 0.0

    # -- introspection ---------------------------------------------------
    @_synchronized
    def sector_view(self, limit: int = 100) -> dict[str, Any]:
        ents = sorted(self._entities.values(),
                      key=lambda e: e.last_seen, reverse=True)[:limit]
        multi = [e for e in ents if len(e.cameras) > 1]
        return {
            "entities_total": len(self._entities),
            "multi_camera_entities": len(multi),
            "open_handoffs": len(self._open_exits),
            "entities": [e.as_dict() for e in ents],
            "topology": self.topology.as_dict(),
        }

    def entity(self, global_id: int) -> Entity | None:
        return self._entities.get(global_id)

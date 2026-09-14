"""Tests for cross-camera handoff, corridor reasoning and topology learning.

The coordinator is pure logic, so it is tested directly without the pipeline:
construct a topology, feed it track lifecycle events with controlled timing and
appearance, and assert the handoffs, entities and dropouts that result. The
matching is conservative by design, and these tests pin that down - a wrong link
invents a journey, so the tests check the refusals as hard as the links.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.edge.crosscam.appearance import similarity
from prahari.edge.crosscam.coordinator import CrossCameraCoordinator
from prahari.edge.crosscam.topology import Topology, demo_topology


def _sig(seed: int) -> np.ndarray:
    """A normalised two-half signature, deterministic per seed.

    Concentrated in a few bins, like a real object's colour histogram - an object
    has a handful of dominant colours, not a uniform spread. This makes different
    seeds genuinely dissimilar (mass in different bins), which is how real
    appearance behaves; a uniform-random histogram would make every object look
    ~50% alike and defeat the point of the cue.
    """
    rng = np.random.default_rng(seed)
    half = 128
    out = []
    for _ in range(2):
        v = np.full(half, 0.001, dtype=np.float32)
        peaks = rng.choice(half, size=4, replace=False)
        v[peaks] = rng.random(4).astype(np.float32) + 0.5
        v /= v.sum()
        out.append(v)
    return np.concatenate(out)


def _topo() -> Topology:
    t = Topology()
    t.add_edge("A", "B", 30.0, 6.0)
    t.add_edge("B", "A", 30.0, 6.0)
    return t


def _t0():
    return datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


# --- appearance ------------------------------------------------------------

def test_similarity_self_is_one():
    s = _sig(1)
    assert similarity(s, s) == pytest.approx(1.0, abs=1e-5)


def test_similarity_missing_is_zero():
    assert similarity(None, _sig(1)) == 0.0


# --- handoff ---------------------------------------------------------------

def test_handoff_links_across_cameras():
    c = CrossCameraCoordinator(_topo())
    t0 = _t0()
    sig = _sig(7)
    # Object appears at A, then leaves.
    c.on_track_confirmed("A", 1, "person", sig, t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=2))
    # Appears at B ~30s later, same look.
    finding = c.on_track_confirmed("B", 5, "person", sig, t0 + timedelta(seconds=31))
    assert finding is not None
    assert finding["kind"] == "handoff"
    assert finding["from_camera"] == "A" and finding["to_camera"] == "B"
    view = c.sector_view()
    assert view["multi_camera_entities"] == 1
    assert view["entities_total"] == 1, "the two sightings are one entity"


def test_arrival_too_late_is_a_new_entity():
    c = CrossCameraCoordinator(_topo())
    t0 = _t0()
    sig = _sig(7)
    c.on_track_confirmed("A", 1, "person", sig, t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=2))
    # Far outside the 30±6s window.
    finding = c.on_track_confirmed("B", 5, "person", sig, t0 + timedelta(seconds=120))
    assert finding is None
    assert c.sector_view()["entities_total"] == 2


def test_different_class_does_not_hand_off():
    c = CrossCameraCoordinator(_topo())
    t0 = _t0()
    c.on_track_confirmed("A", 1, "person", _sig(7), t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=2))
    finding = c.on_track_confirmed("B", 5, "car", _sig(7), t0 + timedelta(seconds=31))
    assert finding is None
    assert c.sector_view()["entities_total"] == 2


def test_dissimilar_appearance_does_not_hand_off():
    c = CrossCameraCoordinator(_topo())
    t0 = _t0()
    c.on_track_confirmed("A", 1, "person", _sig(1), t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=2))
    # Right timing and class, but a very different look.
    finding = c.on_track_confirmed("B", 5, "person", _sig(999),
                                   t0 + timedelta(seconds=31))
    assert finding is None
    assert c.sector_view()["entities_total"] == 2


def test_handoff_learns_transition_time():
    c = CrossCameraCoordinator(_topo())
    t0 = _t0()
    edge = c.topology.edge("A", "B")
    before = edge.mean_transition_s
    # Several consistent 24s handoffs should pull the learned mean toward 24.
    for i in range(6):
        base = t0 + timedelta(seconds=i * 200)
        sig = _sig(100 + i)
        c.on_track_confirmed("A", i, "person", sig, base)
        c.on_track_lost("A", i, base + timedelta(seconds=1))
        c.on_track_confirmed("B", 100 + i, "person", sig,
                             base + timedelta(seconds=24))
    assert edge.observations >= 5
    assert edge.mean_transition_s < before, "learned time should move toward 24s"
    assert 20 < edge.mean_transition_s < 30


# --- corridor dropout ------------------------------------------------------

def test_corridor_dropout_flagged_after_window():
    # A -> B -> C corridor. An object handed A->B, then leaves B toward C and
    # never arrives, is a dropout.
    t = Topology()
    t.add_edge("A", "B", 20.0, 4.0)
    t.add_edge("B", "C", 20.0, 4.0)
    c = CrossCameraCoordinator(t)
    t0 = _t0()
    sig = _sig(7)

    c.on_track_confirmed("A", 1, "person", sig, t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=1))
    hand = c.on_track_confirmed("B", 2, "person", sig, t0 + timedelta(seconds=20))
    assert hand is not None and hand["kind"] == "handoff"

    # It leaves B toward C...
    c.on_track_lost("B", 2, t0 + timedelta(seconds=25))
    # ...and never arrives. Well past B->C's window.
    findings = c.tick(t0 + timedelta(seconds=25 + 60))
    kinds = [f["kind"] for f in findings]
    assert "corridor_dropout" in kinds
    drop = next(f for f in findings if f["kind"] == "corridor_dropout")
    assert drop["camera_id"] == "B"
    assert "C" in drop["expected_at"]


def test_single_camera_track_ending_is_not_a_dropout():
    """A track that was only ever seen at one camera and ends is not a corridor
    disappearance - it never entered the corridor as a confirmed traveller."""
    t = Topology()
    t.add_edge("A", "B", 20.0, 4.0)
    c = CrossCameraCoordinator(t)
    t0 = _t0()
    c.on_track_confirmed("A", 1, "person", _sig(7), t0)
    c.on_track_lost("A", 1, t0 + timedelta(seconds=5))
    findings = c.tick(t0 + timedelta(seconds=200))
    assert all(f["kind"] != "corridor_dropout" for f in findings)


# --- topology --------------------------------------------------------------

def test_demo_topology_is_connected():
    t = demo_topology()
    assert t.edge("CAM-014", "CAM-022") is not None
    assert t.edge("CAM-022", "CAM-011") is not None
    # Bidirectional.
    assert t.edge("CAM-022", "CAM-014") is not None


def test_edge_window_and_timing_score():
    t = _topo()
    e = t.edge("A", "B")
    lo, hi = e.window()
    assert lo < 30 < hi
    assert e.timing_score(30.0) > e.timing_score(45.0)

"""Unit tests for the parts of Prahari where being wrong is expensive.

Focused on the claims the project actually makes, rather than on coverage for
its own sake:

  * geometry - because a tripwire that tests the wrong side is worse than none
  * profiling - because the whole capability argument rests on the ground-plane
    fit recovering real geometry
  * capability gating - because "camera-aware" has to be enforced, not asserted
  * the hash chain - because tamper-evidence that can be silently broken is
    theatre
  * dedupe and alert governance - because the alert flood is the failure mode
    that kills these deployments
  * credential redaction - because a camera password reaching a log is a real
    incident
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.db import Database
from prahari.common.geometry import (
    angular_difference, compass_label, heading_deg, point_in_polygon,
    segments_intersect, side_of_line,
)
from prahari.common.models import (
    DORI, BBox, Camera, CameraRole, Capability, Event, EventType, ObjectClass,
    Point, Priority, SensorType, SyncState, Zone, ZoneKind,
)
from prahari.edge.alerting import AlertGovernor
from prahari.edge.auth import hash_password, issue_token, verify_password, verify_token
from prahari.edge.evidence import EvidenceLedger, compute_entry_hash
from prahari.edge.priority import ScoringContext, score_event
from prahari.edge.profiling.certificate import issue_certificate, verify_certificate
from prahari.edge.profiling.measure import CameraProfiler
from prahari.edge.sources.simulator import OpticalProfile, SimulatedCamera
from prahari.edge.track.bytetrack import ByteTracker


# =====================================================================
# Geometry
# =====================================================================

SQUARE = [Point(x=0.2, y=0.2), Point(x=0.8, y=0.2),
          Point(x=0.8, y=0.8), Point(x=0.2, y=0.8)]


def test_point_in_polygon():
    assert point_in_polygon(0.5, 0.5, SQUARE)
    assert not point_in_polygon(0.1, 0.5, SQUARE)
    assert not point_in_polygon(0.5, 0.9, SQUARE)


def test_segments_intersect():
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0))
    assert not segments_intersect((0, 0), (0.4, 0.4), (0.6, 0.6), (1, 1))


def test_side_of_line_flips_on_crossing():
    a, b = Point(x=0.0, y=0.5), Point(x=1.0, y=0.5)
    above = side_of_line((0.5, 0.2), a, b)
    below = side_of_line((0.5, 0.8), a, b)
    assert above != 0 and below != 0
    assert above != below, "a tripwire must report opposite sides across the line"


def test_heading_and_compass():
    assert heading_deg((0, 0), (1, 0)) == pytest.approx(0.0)
    # Image y grows downward, so moving up-frame is north.
    assert heading_deg((0, 1), (0, 0)) == pytest.approx(90.0)
    assert compass_label(heading_deg((0, 1), (0, 0))) == "North"
    assert heading_deg((0, 0), (0, 0)) is None


def test_angular_difference_wraps():
    assert angular_difference(350, 10) == pytest.approx(20)
    assert angular_difference(10, 350) == pytest.approx(20)
    assert angular_difference(0, 180) == pytest.approx(180)


# =====================================================================
# DORI
# =====================================================================

def test_dori_bands():
    assert DORI.from_px_per_metre(300) is DORI.IDENTIFY
    assert DORI.from_px_per_metre(130) is DORI.RECOGNISE
    assert DORI.from_px_per_metre(70) is DORI.OBSERVE
    assert DORI.from_px_per_metre(30) is DORI.DETECT
    assert DORI.from_px_per_metre(5) is DORI.MONITOR
    assert DORI.IDENTIFY.satisfies(DORI.DETECT)
    assert not DORI.DETECT.satisfies(DORI.IDENTIFY)


# =====================================================================
# Camera profiling - the load-bearing claim
# =====================================================================

def _profile_camera(**kwargs) -> tuple[SimulatedCamera, "CameraMeasurement"]:
    sim = SimulatedCamera(camera_id="T", seed=4242, **kwargs)
    sim.open()
    profiler = CameraProfiler("T", min_frames=20, min_ground_samples=8)
    for _ in range(260):
        frame = sim.read()
        if frame is None:
            continue
        profiler.observe_frame(frame.image, frame.timestamp.timestamp())
        h, w = frame.image.shape[:2]
        for gt in frame.ground_truth:
            if gt["object_class"] != "person":
                continue
            x1, y1, x2, y2 = gt["bbox"]
            if y2 >= h - 2 or y1 <= 1 or x1 <= 1 or x2 >= w - 2:
                continue
            profiler.observe_person(foot_v=y2, px_height=y2 - y1)
    return sim, profiler.build(claimed_fps=sim.nominal_fps)


def test_ground_plane_recovers_true_camera_height():
    """The self-calibration must recover geometry it was never told.

    This is the foundation of every capability decision: if the recovered
    camera height is wrong, every pixels-on-target figure derived from it is
    wrong, and the capability certificate is fiction.
    """
    sim, m = _profile_camera(width=1280, height=720, fov_deg=62.0,
                             camera_height_m=6.0, tilt_deg=2.0,
                             scenario="perimeter")
    assert m.ground_plane_estimated, "ground plane should be recoverable"
    recovered = (m.height - 1 - m.horizon_y) / m.px_per_metre_near
    assert recovered == pytest.approx(6.0, rel=0.15)
    assert m.horizon_y == pytest.approx(sim.horizon_y, abs=6.0)


def test_profiler_waits_for_calibration_samples():
    """Frame count alone must not be enough to certify a camera."""
    profiler = CameraProfiler("T", min_frames=5, min_ground_samples=8,
                              max_frames=1000)
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    for i in range(40):
        profiler.observe_frame(img, float(i))
    assert not profiler.ready, "must not certify with zero geometry samples"
    for i in range(8):
        profiler.observe_person(foot_v=100 - i, px_height=40 + i)
    assert profiler.ready


def test_profiler_gives_up_eventually():
    """A camera watching an empty field still has to be certified, honestly."""
    profiler = CameraProfiler("T", min_frames=5, min_ground_samples=8,
                              max_frames=30)
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    for i in range(30):
        profiler.observe_frame(img, float(i))
    assert profiler.ready
    m = profiler.build(claimed_fps=12.0)
    assert not m.ground_plane_estimated
    assert any("ground plane not estimated" in n for n in m.notes)


def test_degraded_camera_is_refused_fine_detail():
    """A soft, noisy, over-compressed camera must not be handed ANPR."""
    _, m = _profile_camera(width=704, height=480, fov_deg=70.0,
                           camera_height_m=4.5, tilt_deg=3.0,
                           optical=OpticalProfile.preset("degraded_legacy"),
                           scenario="approach")
    camera = Camera(camera_id="LEGACY", name="legacy", role=CameraRole.APPROACH,
                    claimed_width=704, claimed_height=480)
    cert = issue_certificate(camera, m)
    assert not cert.is_granted(Capability.ANPR)
    grant = cert.grant_for(Capability.ANPR)
    assert grant is not None and grant.reason, "a refusal must state its reason"


def test_face_recognition_never_granted():
    """Withheld as product policy, regardless of how good the camera is."""
    _, m = _profile_camera(width=1280, height=720, fov_deg=28.0,
                           camera_height_m=3.0, tilt_deg=12.0,
                           optical=OpticalProfile.preset("gate_hd"),
                           scenario="chokepoint")
    camera = Camera(camera_id="GATE", name="gate", role=CameraRole.CHOKEPOINT)
    cert = issue_certificate(camera, m)
    assert not cert.is_granted(Capability.FACE_RECOGNITION)
    grant = cert.grant_for(Capability.FACE_RECOGNITION)
    assert "policy" in grant.reason.lower()


def test_thermal_sensor_refused_plate_reading():
    _, m = _profile_camera(width=1280, height=720, fov_deg=28.0,
                           camera_height_m=3.0, tilt_deg=12.0,
                           optical=OpticalProfile.preset("gate_hd"),
                           scenario="chokepoint")
    camera = Camera(camera_id="TH", name="thermal gate",
                    role=CameraRole.CHOKEPOINT, sensor_type=SensorType.THERMAL)
    cert = issue_certificate(camera, m)
    assert not cert.is_granted(Capability.ANPR)
    assert "thermal" in cert.grant_for(Capability.ANPR).reason.lower()


def test_certificate_digest_detects_tampering():
    _, m = _profile_camera(scenario="perimeter", camera_height_m=6.0, tilt_deg=2.0)
    camera = Camera(camera_id="C", name="c", role=CameraRole.PERIMETER)
    cert = issue_certificate(camera, m)
    assert verify_certificate(cert)
    # Quietly grant a capability the measurement never justified.
    for g in cert.grants:
        if g.capability is Capability.ANPR:
            g.granted = True
    assert not verify_certificate(cert), "an edited certificate must not verify"


# =====================================================================
# Tracking
# =====================================================================

def _det(x1, y1, x2, y2, conf=0.9, cls=ObjectClass.PERSON):
    from prahari.common.models import Detection
    return Detection(object_class=cls, confidence=conf,
                     bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2))


def test_tracker_keeps_identity_across_frames():
    tracker = ByteTracker("C", min_hits_to_confirm=2)
    now = datetime.now(timezone.utc)
    ids = set()
    for i in range(6):
        tracks = tracker.update([_det(100 + i * 5, 100, 130 + i * 5, 180)],
                                now + timedelta(seconds=i * 0.1), i * 0.1)
        ids.update(t.track_id for t in tracks)
    assert len(ids) == 1, "one object walking must stay one track"


def test_tracker_recovers_through_weak_detections():
    """ByteTrack's second pass: a distant figure in poor light drops to low
    confidence, and dropping it there is how a tracker loses the target that
    mattered."""
    tracker = ByteTracker("C", min_hits_to_confirm=2, high_confidence=0.55)
    now = datetime.now(timezone.utc)
    for i in range(3):
        tracker.update([_det(100, 100, 130, 180, conf=0.9)],
                       now + timedelta(seconds=i * 0.1), i * 0.1)
    before = {t.track_id for t in tracker.tracks()}
    # Same object, now barely detected.
    tracks = tracker.update([_det(102, 100, 132, 180, conf=0.35)],
                            now + timedelta(seconds=0.4), 0.4)
    assert {t.track_id for t in tracks} == before


def test_tracker_retires_lost_tracks():
    tracker = ByteTracker("C", min_hits_to_confirm=2, max_lost_frames=3)
    now = datetime.now(timezone.utc)
    for i in range(3):
        tracker.update([_det(10, 10, 40, 90)], now, i * 0.1)
    for i in range(6):
        tracker.update([], now, 1.0 + i * 0.1)
    assert len(tracker) == 0


# =====================================================================
# Priority scoring
# =====================================================================

def test_livestock_scores_below_a_person():
    from prahari.common.models import Track
    now = datetime.now(timezone.utc)

    def track_of(cls):
        return Track(track_id=1, camera_id="C", object_class=cls,
                     first_seen=now, last_seen=now,
                     bbox=BBox(x1=0, y1=0, x2=10, y2=20), confidence=0.8)

    ctx = ScoringContext(detection_confidence=0.8)
    person, _, _ = score_event(EventType.ZONE_INTRUSION, track_of(ObjectClass.PERSON), ctx)
    cattle, _, _ = score_event(EventType.ZONE_INTRUSION, track_of(ObjectClass.CATTLE), ctx)
    assert cattle < person, "cattle at a border are overwhelmingly benign"


def test_operator_feedback_damps_repeated_false_alarms():
    naive, _, _ = score_event(EventType.LOITERING, None, ScoringContext())
    damped, factors, _ = score_event(
        EventType.LOITERING, None,
        ScoringContext(feedback_true=1, feedback_false=9))
    assert damped < naive
    assert any("feedback" in f.name for f in factors)


def test_score_is_explainable():
    _, factors, _ = score_event(EventType.LINE_CROSSING, None,
                                ScoringContext(is_night=True))
    assert factors, "every score must carry its derivation"
    assert any(f.name == "night conditions" for f in factors)


def test_priority_bands():
    assert Priority.from_score(0.9) is Priority.CRITICAL
    assert Priority.from_score(0.7) is Priority.HIGH
    assert Priority.from_score(0.5) is Priority.MEDIUM
    assert Priority.from_score(0.3) is Priority.LOW
    assert Priority.from_score(0.1) is Priority.INFO


# =====================================================================
# Alert governance
# =====================================================================

def _event(score: float, priority: Priority) -> Event:
    return Event(event_id=f"E{score}", camera_id="C", node_id="N",
                 event_type=EventType.ZONE_INTRUSION, priority=priority,
                 priority_score=score)


def test_governor_rations_alerts_but_records_everything():
    gov = AlertGovernor(budget_per_hour=5)
    decisions = [gov.decide(_event(0.5, Priority.MEDIUM)) for _ in range(40)]
    alerted = sum(1 for d in decisions if d.alerted)
    assert alerted < 40, "the governor must ration once over budget"
    assert gov.suppressed_last_hour > 0
    assert gov.status()["suppression_rate"] > 0


def test_governor_never_suppresses_critical():
    gov = AlertGovernor(budget_per_hour=1)
    for _ in range(30):
        gov.decide(_event(0.5, Priority.MEDIUM))
    decision = gov.decide(_event(0.95, Priority.CRITICAL))
    assert decision.alerted, "a camera under attack is never rate-limited"


def test_governor_explains_suppression():
    gov = AlertGovernor(budget_per_hour=1)
    gov.decide(_event(0.9, Priority.HIGH))
    for _ in range(10):
        d = gov.decide(_event(0.3, Priority.LOW))
    assert not d.alerted
    assert "threshold" in d.reason


# =====================================================================
# Evidence ledger
# =====================================================================

@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def _make_event(i: int) -> Event:
    return Event(event_id=f"EVT-{i:04d}", camera_id="CAM-1", node_id="N1",
                 event_type=EventType.LINE_CROSSING, priority=Priority.HIGH,
                 priority_score=0.7)


def test_ledger_chain_verifies(db):
    ledger = EvidenceLedger(db)
    for i in range(12):
        db.insert_event(ledger.append(_make_event(i)))
    result = ledger.verify()
    assert result["valid"], result["message"]
    assert result["entries"] == 12


def test_ledger_detects_edited_event(db):
    ledger = EvidenceLedger(db)
    for i in range(6):
        db.insert_event(ledger.append(_make_event(i)))
    victim = db.get_event("EVT-0003")
    victim.priority_score = 0.01            # quietly downgrade an event
    db.update_event(victim)
    result = ledger.verify()
    assert not result["valid"]
    assert result["broken_at"] == victim.ledger_index


def test_ledger_detects_deleted_event(db):
    ledger = EvidenceLedger(db)
    for i in range(6):
        db.insert_event(ledger.append(_make_event(i)))
    db.execute("DELETE FROM events WHERE event_id=?", ("EVT-0002",))
    assert not ledger.verify()["valid"]


def test_ledger_append_is_idempotent(db):
    """Re-appending must not re-index an event.

    An earlier implementation re-appended on clip completion, which handed the
    event a second position and orphaned the first - silently breaking every
    link after it.
    """
    ledger = EvidenceLedger(db)
    event = ledger.append(_make_event(1))
    first_index, first_hash = event.ledger_index, event.entry_hash
    again = ledger.append(event)
    assert again.ledger_index == first_index
    assert again.entry_hash == first_hash


def test_acknowledgement_does_not_break_the_chain(db):
    """Operational state must not be committed to, or working the alert queue
    would invalidate the evidence."""
    ledger = EvidenceLedger(db)
    for i in range(4):
        db.insert_event(ledger.append(_make_event(i)))
    event = db.get_event("EVT-0001")
    event.acknowledged = True
    event.acknowledged_by = "operator"
    event.operator_feedback = "false_alarm"
    db.update_event(event)
    assert ledger.verify()["valid"]


# =====================================================================
# Store-and-forward queue
# =====================================================================

def test_only_sealed_events_are_shippable(db):
    ledger = EvidenceLedger(db)
    sealed = ledger.append(_make_event(1))
    db.insert_event(sealed)
    db.insert_event(_make_event(2))          # unsealed: clip still capturing
    pending = db.pending_events(limit=10)
    assert [e.event_id for e in pending] == ["EVT-0001"]


def test_queue_prioritises_significant_events(db):
    ledger = EvidenceLedger(db)
    low = _make_event(1)
    low.priority, low.priority_score = Priority.LOW, 0.3
    high = _make_event(2)
    high.priority, high.priority_score = Priority.CRITICAL, 0.95
    db.insert_event(ledger.append(low))
    db.insert_event(ledger.append(high))
    assert db.pending_events(limit=2)[0].event_id == high.event_id


def test_eviction_targets_lowest_priority_evidence(db):
    from prahari.common.models import EvidenceRef
    ledger = EvidenceLedger(db)
    for i, prio in enumerate([Priority.CRITICAL, Priority.LOW, Priority.HIGH]):
        ev = _make_event(i)
        ev.priority = prio
        ev.evidence = EvidenceRef(clip_path=f"/tmp/{i}.mp4", size_bytes=1000)
        db.insert_event(ledger.append(ev))
    candidates = db.eviction_candidates(limit=1)
    assert candidates[0].priority is Priority.LOW, \
        "storage pressure must sacrifice the least significant evidence first"


# =====================================================================
# Security
# =====================================================================

def test_camera_credentials_never_serialised():
    cam = Camera(camera_id="C", name="c",
                 stream_url="rtsp://admin:s3cr3t@10.0.0.7/Streaming/1",
                 username="admin", password="s3cr3t")
    public = cam.public_dict()
    blob = str(public)
    assert "s3cr3t" not in blob
    assert "password" not in public
    assert public["has_credentials"] is True
    assert "<redacted>" in public["stream_url"]


def test_password_hashing_roundtrip():
    salt, digest = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", salt, digest)
    assert not verify_password("wrong password", salt, digest)


def test_token_signature_is_checked():
    token = issue_token("operator", "operator", "secret-key", 3600)
    assert verify_token(token, "secret-key").username == "operator"
    assert verify_token(token, "different-key") is None
    body, _, sig = token.partition(".")
    assert verify_token(body + ".tampered", "secret-key") is None


def test_expired_token_rejected():
    assert verify_token(issue_token("u", "viewer", "k", -1), "k") is None


def test_role_hierarchy():
    from prahari.edge.auth import Principal
    assert Principal("a", "admin").may("operator")
    assert not Principal("v", "viewer").may("operator")
    assert Principal("o", "operator").may("viewer")


# =====================================================================
# Simulator invariants
# =====================================================================

def test_simulator_is_deterministic():
    def run(seed):
        sim = SimulatedCamera("D", 320, 240, 10.0, 60.0, 5.0, seed=seed)
        sim.open()
        for _ in range(5):
            sim.read()
        frame = sim.read()
        return frame.image

    assert np.array_equal(run(7), run(7)), "the same seed must replay identically"
    assert not np.array_equal(run(7), run(8))


def test_perspective_is_physically_consistent():
    """A person twice as far away must be half as tall in pixels."""
    sim = SimulatedCamera("P", 1280, 720, 12.0, 60.0, 5.0)
    sim.open()
    assert sim.px_per_metre_at(20.0) == pytest.approx(
        2 * sim.px_per_metre_at(40.0), rel=1e-6)


def test_visible_depth_range_is_sane():
    sim = SimulatedCamera("P", 1280, 720, 12.0, 28.0, 3.0, tilt_deg=12.0)
    sim.open()
    near, far = sim.visible_depth_range()
    assert 0 < near < far
    # The fence must be clamped into view, or every tripwire on it is dead.
    assert near <= sim.fence_distance_m <= far

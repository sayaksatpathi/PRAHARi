"""Prahari domain model.

These types are the contract between the edge node and the sector core, and
between the pipeline stages inside the edge node. They are deliberately
transport-agnostic: the same schema travels over the in-process bus, the REST
API, the WebSocket feed and the store-and-forward sync queue.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# Enumerations
# =====================================================================

class CameraRole(str, enum.Enum):
    """What a camera is *for*. Drives which analytics are even candidates."""
    PERIMETER = "perimeter"      # long view along a fence line or ridge
    APPROACH = "approach"        # medium view of a route leading to the border
    CHOKEPOINT = "chokepoint"    # gate, bridge, checkpost - close, controlled
    RIVERINE = "riverine"        # river crossing / ghat - boats matter here


class BorderProfile(str, enum.Enum):
    """The two operating doctrines Prahari supports.

    FENCED  - a fenced/managed border. Crossing the line is itself the event.
    OPEN    - an open border (e.g. Indo-Nepal, Indo-Bhutan) where lawful
              crossing is constant and high-volume. A line crossing is NOT an
              event; deviation from the learnt pattern of life is.
    """
    FENCED = "fenced"
    OPEN = "open"


class SensorType(str, enum.Enum):
    VISIBLE = "visible"
    THERMAL = "thermal"
    IR_ILLUMINATED = "ir_illuminated"


class Capability(str, enum.Enum):
    """Analytics that a camera may or may not be able to justify."""
    PERSON_DETECTION = "person_detection"
    VEHICLE_DETECTION = "vehicle_detection"
    ANIMAL_DETECTION = "animal_detection"
    BOAT_DETECTION = "boat_detection"
    UAV_DETECTION = "uav_detection"
    TRACKING = "tracking"
    DIRECTION_ANALYSIS = "direction_analysis"
    SPEED_ESTIMATION = "speed_estimation"
    ANPR = "anpr"
    FACE_DETECTION = "face_detection"
    FACE_RECOGNITION = "face_recognition"   # never auto-granted; see docs/privacy.md


class DORI(str, enum.Enum):
    """IEC 62676-4 pixels-on-target bands.

    The whole point of Prahari's profiling module: an analytic is only offered
    where the imagery actually supports it. Values are minimum pixels per metre
    of target as defined by the standard.
    """
    MONITOR = "monitor"              # 12 px/m  - "something is there"
    DETECT = "detect"                # 25 px/m  - a person is present
    OBSERVE = "observe"              # 62 px/m  - characteristic details
    RECOGNISE = "recognise"          # 125 px/m - a known individual, maybe
    IDENTIFY = "identify"            # 250 px/m - courtroom-grade identification

    @property
    def px_per_metre(self) -> float:
        return _DORI_PX_PER_M[self]

    @staticmethod
    def from_px_per_metre(v: float) -> "DORI":
        for band in (DORI.IDENTIFY, DORI.RECOGNISE, DORI.OBSERVE, DORI.DETECT):
            if v >= band.px_per_metre:
                return band
        return DORI.MONITOR

    def satisfies(self, required: "DORI") -> bool:
        return self.px_per_metre >= required.px_per_metre


_DORI_PX_PER_M: dict[DORI, float] = {
    DORI.MONITOR: 12.0,
    DORI.DETECT: 25.0,
    DORI.OBSERVE: 62.0,
    DORI.RECOGNISE: 125.0,
    DORI.IDENTIFY: 250.0,
}


class ObjectClass(str, enum.Enum):
    PERSON = "person"
    BICYCLE = "bicycle"
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BUS = "bus"
    TRUCK = "truck"
    BOAT = "boat"
    CATTLE = "cattle"          # the single largest false-alarm source at a border
    UAV = "uav"
    UNKNOWN = "unknown"

    @property
    def is_vehicle(self) -> bool:
        return self in {ObjectClass.CAR, ObjectClass.MOTORCYCLE,
                        ObjectClass.BUS, ObjectClass.TRUCK}


class EventType(str, enum.Enum):
    # --- fenced-border doctrine ---
    LINE_CROSSING = "line_crossing"
    ZONE_INTRUSION = "zone_intrusion"
    WRONG_DIRECTION = "wrong_direction"
    LOITERING = "loitering"
    GROUP_MOVEMENT = "group_movement"
    VEHICLE_MOVEMENT = "vehicle_movement"
    # --- open-border doctrine ---
    OFF_ROUTE_MOVEMENT = "off_route_movement"      # movement away from a lawful route
    TEMPORAL_ANOMALY = "temporal_anomaly"          # traffic at an unusual hour
    REPEAT_ENTITY = "repeat_entity"                # same plate, many odd crossings
    CORRIDOR_DROPOUT = "corridor_dropout"          # entered corridor, never exited
    NIGHT_MOVEMENT = "night_movement"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    # --- sensor integrity ---
    CAMERA_TAMPER = "camera_tamper"
    CAMERA_OFFLINE = "camera_offline"
    STREAM_REPLAY_SUSPECTED = "stream_replay_suspected"


class Priority(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]

    @staticmethod
    def from_score(score: float) -> "Priority":
        if score >= 0.85:
            return Priority.CRITICAL
        if score >= 0.65:
            return Priority.HIGH
        if score >= 0.45:
            return Priority.MEDIUM
        if score >= 0.25:
            return Priority.LOW
        return Priority.INFO


class SyncState(str, enum.Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    SYNCED = "synced"
    EVIDENCE_EVICTED = "evidence_evicted"   # metadata kept, clip dropped under disk pressure
    FAILED = "failed"


class LinkMode(str, enum.Enum):
    """Adaptive transmission posture. Shown verbatim in the dashboard."""
    ONLINE = "online"              # full metadata + evidence flows
    DEGRADED = "degraded"          # metadata + thumbnails, clips on demand
    EVENT_ONLY = "event_only"      # metadata only; evidence stays at the edge
    OFFLINE = "offline"            # nothing leaves; local queue grows
    SYNCING = "syncing"            # draining the backlog


# =====================================================================
# Geometry
# =====================================================================

class Point(BaseModel):
    x: float
    y: float


class ZoneKind(str, enum.Enum):
    POLYGON = "polygon"       # an area
    LINE = "line"             # a virtual tripwire
    ROUTE = "route"           # a lawful corridor (open-border doctrine)


class Zone(BaseModel):
    zone_id: str
    camera_id: str
    name: str
    kind: ZoneKind
    points: list[Point]
    # For LINE zones: which side counts as "in". For ROUTE: permitted heading.
    direction_deg: float | None = Field(
        default=None, description="Permitted/expected heading in degrees, 0=East, CCW"
    )
    direction_tolerance_deg: float = 60.0
    enabled: bool = True
    # Open-border doctrine: a ROUTE zone can be marked lawful, so traffic through
    # it is normal and traffic *outside* it is what gets scored.
    is_lawful_route: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("points")
    @classmethod
    def _min_points(cls, v: list[Point]) -> list[Point]:
        if len(v) < 2:
            raise ValueError("a zone needs at least 2 points")
        return v


# =====================================================================
# Camera + capability profiling
# =====================================================================

class CameraMeasurement(BaseModel):
    """What we *measured*, as opposed to what the camera's datasheet claims.

    A decade-old dome that reports 1080p may deliver the effective resolution of
    a 480p camera after lens fog, focus drift and aggressive recompression.
    Profiling exists so Prahari never assigns an analytic the imagery cannot
    support.
    """
    measured_at: datetime = Field(default_factory=utcnow)
    frames_sampled: int = 0

    width: int = 0
    height: int = 0
    claimed_fps: float = 0.0
    measured_fps: float = 0.0

    # Effective sharpness: variance of Laplacian, normalised. Low => soft optics.
    sharpness: float = 0.0
    effective_resolution_factor: float = Field(
        default=1.0, description="0-1; measured detail relative to nominal pixel count"
    )
    # Temporal noise floor (sensor gain at night) and block-artifact severity.
    noise_sigma: float = 0.0
    compression_artifact_score: float = 0.0
    mean_luma: float = 0.0
    is_low_light: bool = False

    # Scene geometry, self-calibrated from accumulated person bounding boxes.
    ground_plane_estimated: bool = False
    px_per_metre_near: float = 0.0
    px_per_metre_far: float = 0.0
    horizon_y: float | None = None

    notes: list[str] = Field(default_factory=list)


class CapabilityGrant(BaseModel):
    """One analytic, and the measured reason it is or is not permitted."""
    capability: Capability
    granted: bool
    dori_required: DORI
    dori_achieved: DORI | None = None
    # Analytics are gated *spatially*: a camera's near field may support ANPR
    # while its far field barely supports detection. This is the image-space
    # region where the grant holds.
    valid_region: list[Point] = Field(default_factory=list)
    reason: str = ""


class CapabilityCertificate(BaseModel):
    """A measured, versioned statement of what this camera can actually do.

    This is the artefact Prahari issues per camera. It is what makes the claim
    "camera-aware analytics" checkable rather than rhetorical.
    """
    certificate_id: str
    camera_id: str
    issued_at: datetime = Field(default_factory=utcnow)
    version: int = 1
    measurement: CameraMeasurement
    grants: list[CapabilityGrant] = Field(default_factory=list)
    overall_dori: DORI = DORI.MONITOR
    digest: str = ""   # sha256 over the certificate body, for audit

    def granted(self) -> set[Capability]:
        return {g.capability for g in self.grants if g.granted}

    def is_granted(self, cap: Capability) -> bool:
        return any(g.capability is cap and g.granted for g in self.grants)

    def grant_for(self, cap: Capability) -> CapabilityGrant | None:
        for g in self.grants:
            if g.capability is cap:
                return g
        return None


class CameraHealth(str, enum.Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    TAMPERED = "tampered"
    UNKNOWN = "unknown"


class Camera(BaseModel):
    camera_id: str
    name: str
    location: str = ""
    latitude: float | None = None
    longitude: float | None = None

    # Source. `stream_url` may contain credentials; it is NEVER serialised to
    # the API or the logs - see `public_dict()`.
    source_kind: Literal["simulator", "file", "rtsp", "onvif"] = "simulator"
    stream_url: str = ""
    username: str = ""
    password: str = ""

    role: CameraRole = CameraRole.PERIMETER
    border_profile: BorderProfile = BorderProfile.FENCED
    sensor_type: SensorType = SensorType.VISIBLE

    # Claimed (from datasheet/ONVIF); profiling measures the truth.
    claimed_width: int = 1920
    claimed_height: int = 1080
    claimed_fps: float = 25.0
    codec: str = "h264"
    night_capable: bool = False
    estimated_range_m: float = 100.0
    field_of_view_deg: float = 60.0

    health: CameraHealth = CameraHealth.UNKNOWN
    enabled: bool = True
    edge_node: str = "BOP-EDGE-01"

    certificate_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    # Simulator-only knobs, used to give profiling something real to measure.
    sim_profile: dict[str, Any] = Field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        """Serialisation that is safe to hand to the API, the UI and the logs.

        Camera credentials never leave the process. This is enforced here rather
        than at each call site so it cannot be forgotten.
        """
        d = self.model_dump(mode="json")
        d.pop("password", None)
        d.pop("username", None)
        url = d.get("stream_url") or ""
        if "@" in url and "//" in url:
            scheme, _, rest = url.partition("//")
            _, _, hostpart = rest.rpartition("@")
            url = scheme + "//<redacted>@" + hostpart
        d["stream_url"] = url
        d["has_credentials"] = bool(self.password or self.username)
        return d


# =====================================================================
# Detection / tracking
# =====================================================================

class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def foot(self) -> tuple[float, float]:
        """Bottom-centre: where the object meets the ground plane."""
        return ((self.x1 + self.x2) / 2.0, self.y2)

    def iou(self, other: "BBox") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


class Detection(BaseModel):
    object_class: ObjectClass
    confidence: float
    bbox: BBox
    frame_index: int = 0
    timestamp: datetime = Field(default_factory=utcnow)


class Track(BaseModel):
    track_id: int
    camera_id: str
    object_class: ObjectClass
    first_seen: datetime
    last_seen: datetime
    bbox: BBox
    confidence: float
    # Trajectory in image space; used for direction, dwell and crossing tests.
    history: list[Point] = Field(default_factory=list)
    direction_deg: float | None = None
    speed_mps: float | None = None          # only when the ground plane is known
    dwell_seconds: float = 0.0
    zones: list[str] = Field(default_factory=list)
    hits: int = 0
    lost_frames: int = 0

    @property
    def age_seconds(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds()


# =====================================================================
# Events, evidence, alerts
# =====================================================================

class PriorityFactor(BaseModel):
    """One contribution to the Event Priority Score, kept so the operator can
    see *why* an alert fired. Unexplained scores are the fastest route to an
    operator muting the system."""
    name: str
    weight: float
    detail: str = ""


class EvidenceRef(BaseModel):
    frame_path: str | None = None
    thumb_path: str | None = None
    clip_path: str | None = None
    mask_path: str | None = None            # segmentation overlay, when refined
    frame_sha256: str | None = None
    clip_sha256: str | None = None
    mask_sha256: str | None = None
    clip_seconds: float = 0.0
    size_bytes: int = 0


class Event(BaseModel):
    event_id: str
    camera_id: str
    node_id: str
    event_type: EventType
    priority: Priority
    priority_score: float = 0.0
    priority_factors: list[PriorityFactor] = Field(default_factory=list)

    object_class: ObjectClass = ObjectClass.UNKNOWN
    track_id: int | None = None
    confidence: float = 0.0

    zone_id: str | None = None
    zone_name: str | None = None
    direction_deg: float | None = None
    speed_mps: float | None = None

    # Wall-clock time, plus the monotonic reading at capture. An edge node that
    # has been offline for days has no NTP; keeping both lets the core correct
    # for clock drift at resync instead of silently trusting a drifted stamp.
    timestamp: datetime = Field(default_factory=utcnow)
    monotonic_ns: int = 0
    clock_synced: bool = True

    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceRef | None = None

    sync_state: SyncState = SyncState.PENDING
    synced_at: datetime | None = None

    # Tamper-evident ledger linkage (see edge/evidence.py).
    ledger_index: int = 0
    prev_hash: str = ""
    entry_hash: str = ""

    # Whether this event interrupted an operator. Recording and alerting are
    # separate decisions: everything is recorded, only some things alert.
    alerted: bool = True
    alert_decision: str = ""

    # Friendly-force assessment, when a patrol profile was in play. None means
    # the patrol layer found nothing to consider; it never means "cleared".
    patrol: PatrolAssessment | None = None

    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    operator_feedback: str | None = None   # "true_positive" | "false_alarm" | None


# =====================================================================
# Friendly force (patrol) suppression
# =====================================================================

class PatrolDecision(str, enum.Enum):
    """What the patrol layer concluded about one event.

    Deliberately four states rather than a boolean. A whitelist has two - known
    or unknown - and that is exactly the property that makes a whitelist a
    security hole, because everything resembling the known thing inherits its
    immunity. Here, resembling a patrol without conforming to one is its own
    outcome, and it raises the alert rather than lowering it.
    """
    NOT_MATCHED = "not_matched"   # no patrol accounts for this; normal pipeline
    SUPPRESSED = "suppressed"     # conforms on every cue; recorded, not alerted
    DOWNGRADED = "downgraded"     # probably the patrol, not certainly; retained, lowered
    DEVIATION = "deviation"       # is the patrol, but is not behaving like it; escalated


class PatrolWindow(BaseModel):
    """When a patrol is expected to be on its route.

    Times are minutes since local midnight, so a window can be read and edited
    by a duty officer without a timezone argument. A window whose end is before
    its start spans midnight, which is the common case for a night patrol.
    """
    days: list[int] = Field(
        default_factory=list,
        description="Weekday numbers, 0=Monday..6=Sunday. Empty means every day.")
    start_minute: int = 0
    end_minute: int = 1439
    # Patrols are run by people, and people are late. Inside the window is a
    # schedule match; inside the tolerance is "plausibly the same patrol,
    # running behind" - which is explicitly NOT enough to suppress.
    tolerance_minutes: int = 20
    label: str = ""

    @field_validator("start_minute", "end_minute")
    @classmethod
    def _in_day(cls, v: int) -> int:
        if not 0 <= v <= 1439:
            raise ValueError("minute-of-day must be in 0..1439")
        return v

    @property
    def wraps_midnight(self) -> bool:
        return self.end_minute < self.start_minute

    def _contains_minute(self, minute: int) -> bool:
        if self.wraps_midnight:
            return minute >= self.start_minute or minute <= self.end_minute
        return self.start_minute <= minute <= self.end_minute

    def minutes_outside(self, when: datetime) -> float:
        """0.0 if `when` falls inside the window, else how far outside, in minutes.

        Returns infinity when the weekday does not match at all, because a
        Tuesday patrol is not evidence about a Saturday.
        """
        weekday = when.weekday()
        minute = when.hour * 60 + when.minute
        if self.days and weekday not in self.days:
            # A window that wraps midnight is still running in the following
            # day's early minutes, so admit the previous day as well.
            prev = (weekday - 1) % 7
            if not (self.wraps_midnight and prev in self.days
                    and minute <= self.end_minute):
                return float("inf")
        if self._contains_minute(minute):
            return 0.0

        def circular(a: int, b: int) -> float:
            d = abs(a - b)
            return float(min(d, 1440 - d))

        return min(circular(minute, self.start_minute),
                   circular(minute, self.end_minute))

    def describe(self) -> str:
        def hhmm(m: int) -> str:
            return f"{m // 60:02d}:{m % 60:02d}"
        days = ("daily" if not self.days else
                " ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d]
                         for d in sorted(self.days)))
        return f"{hhmm(self.start_minute)}-{hhmm(self.end_minute)} {days}"


class PatrolProfile(BaseModel):
    """A declared friendly-force movement: who, where, when, which way.

    A statement of what *should* happen, used in both directions. Movement that
    conforms to it is not worth interrupting anyone for; movement that matches
    the profile's identity but breaks one of its expectations is worth more than
    an ordinary alert, because a patrol walking its route backwards at 03:00 is
    either lost, under duress, or not the patrol.
    """
    patrol_id: str
    name: str
    sector: str = ""
    active: bool = True

    # Route: the camera sequence the patrol is expected to walk, and the zones
    # on those cameras it is entitled to be in. A camera not on this list is not
    # this patrol, full stop.
    cameras: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)

    windows: list[PatrolWindow] = Field(default_factory=list)

    expected_heading_deg: float | None = None
    heading_tolerance_deg: float = 70.0

    object_classes: list[ObjectClass] = Field(
        default_factory=lambda: [ObjectClass.PERSON])
    max_group_size: int = 8
    min_speed_mps: float = 0.0
    max_speed_mps: float = 3.0

    # Optional identifier. Prahari will not let identity grant a suppression
    # unless the deployment has explicitly configured it, because "the system
    # stopped alerting because it thought it recognised someone" is the failure
    # mode that makes a video system inadmissible. Plate reading is the only
    # identity signal on this node; face recognition is never granted at all
    # (docs/privacy.md).
    identity_matching_enabled: bool = False
    vehicle_plates: list[str] = Field(default_factory=list)

    # Bound on how much this profile may suppress within one window. A patrol is
    # a handful of transits, not an open licence: once the budget is spent the
    # remaining look-alikes go through the normal pipeline. This is the
    # safeguard against somebody simply walking in behind the patrol - see
    # docs/patrol-suppression.md.
    max_suppressions_per_window: int = 6

    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    def covers_camera(self, camera_id: str) -> bool:
        return camera_id in self.cameras

    def window_for(self, when: datetime) -> tuple["PatrolWindow | None", float]:
        """The nearest window to `when`, and how many minutes outside it we are."""
        best: PatrolWindow | None = None
        best_gap = float("inf")
        for w in self.windows:
            gap = w.minutes_outside(when)
            # `best is None` must come first. A window on a day that does not
            # match at all reports an infinite gap, and `inf < inf` is False -
            # so comparing on the gap alone returned "this patrol has no window"
            # for a wrong-day observation, which reads as a missing cue rather
            # than as the decisive disagreement it actually is.
            if best is None or gap < best_gap:
                best, best_gap = w, gap
        return best, best_gap


class PatrolCue(BaseModel):
    """One evaluated cue, kept so the operator can see the whole derivation."""
    name: str
    score: float            # 0..1, how well this cue agreed
    weight: float           # its share of the assessment
    evaluable: bool = True  # False when the data for it was not available
    detail: str = ""


class PatrolAssessment(BaseModel):
    """The patrol layer's complete, auditable finding for one event.

    Stored on the event and sealed into the hash chain, so the record of *why*
    an operator was not interrupted is as tamper-evident as the record of the
    event itself. A suppression that cannot be audited afterwards is worse than
    no suppression at all.
    """
    decision: PatrolDecision = PatrolDecision.NOT_MATCHED
    patrol_id: str | None = None
    patrol_name: str | None = None
    match_score: float = 0.0          # 0..1, bounded
    identity_confidence: float = 0.0  # is this the patrol at all?
    conformance: float = 0.0          # is it behaving as the patrol should?
    cues: list[PatrolCue] = Field(default_factory=list)
    reason: str = ""
    score_adjustment: float = 0.0
    deviations: list[str] = Field(default_factory=list)
    candidates_considered: int = 0

    # Expected versus observed, shown side by side in the operator UI, because
    # "suppressed, trust me" is not a reviewable statement.
    expected_route: list[str] = Field(default_factory=list)
    expected_window: str | None = None
    expected_heading_deg: float | None = None
    observed_time: datetime | None = None
    observed_heading_deg: float | None = None
    observed_camera: str | None = None
    observed_zone: str | None = None

    @property
    def matched(self) -> bool:
        return self.decision is not PatrolDecision.NOT_MATCHED

class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    FALSE_ALARM = "false_alarm"

class Incident(BaseModel):
    incident_id: str
    node_id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    status: IncidentStatus = IncidentStatus.OPEN
    event_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    notes: str = ""
class SystemStatus(BaseModel):
    node_id: str
    node_name: str
    sector: str
    link_mode: LinkMode
    core_reachable: bool
    cameras_total: int
    cameras_online: int
    events_total: int
    events_pending_sync: int
    queue_bytes: int
    alerts_last_hour: int
    alert_budget_per_hour: int
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_free_bytes: int = 0
    uptime_seconds: float = 0.0
    detector_backend: str = "unknown"
    device: str = "cpu"

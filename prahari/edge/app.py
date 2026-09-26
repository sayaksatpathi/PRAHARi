"""Prahari edge node - HTTP API, WebSocket feed and dashboard host.

This process is the whole edge node: it owns the cameras, runs the analytics,
writes the evidence, keeps the offline queue and serves the operator interface.
It is designed to run standalone with no sector core reachable at all, because
that is its normal condition for part of every week at a real outpost.

The dashboard is served from here as static files with no build step. That is a
deliberate operational choice, not a shortcut: an edge node that needs a Node
toolchain and a package install to render its own interface is an edge node that
cannot be updated over a VSAT link or recovered from a USB stick.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from prahari.common.bus import get_bus, subj
from prahari.common.config import get_settings
from prahari.common.db import Database
from prahari.common.models import (
    Camera,
    Capability,
    LinkMode,
    PatrolProfile,
    Priority,
    SyncState,
    SystemStatus,
    Zone,
)
from prahari.edge.alerting import AlertGovernor
from prahari.edge.anpr import RepeatPlateTracker, build_plate_reader
from prahari.edge.auth import (
    LoginThrottle, Principal, UserStore, issue_token, verify_token,
)
from prahari.edge.demo import bootstrap_demo_site
from prahari.edge.evidence import EvidenceLedger, EvidenceStore
from prahari.edge.factory import (
    build_detector, build_source, warn_if_detector_cannot_see,
)
from prahari.edge.normalcy import NormalcyModel
from prahari.edge.patrol.matcher import (
    STRONG_MATCH, UNCERTAIN_MATCH, PatrolMatcher,
)
from prahari.edge.patrol.roster import PatrolRoster, seed_demo_patrols
from prahari.edge.patrol.scenarios import run_scenarios
from prahari.edge.pipeline import CameraPipeline
from prahari.edge.segment.factory import build_segmenter
from prahari.edge.crosscam.coordinator import CrossCameraCoordinator
from prahari.edge.crosscam.topology import demo_topology
from prahari.edge.priority import ScoringContext, score_event
from prahari.edge.evidence import compute_entry_hash  # noqa: F401
from collections import deque
from uuid import uuid4
from prahari.edge.sync import SyncManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-24s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("prahari.app")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class NodeRuntime:
    """Everything the node owns, in one place."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.db = Database(self.settings.db_path, secret_key=self.settings.secret_key)
        self.bus = get_bus()
        self.evidence = EvidenceStore(self.settings.evidence_dir)
        self.ledger = EvidenceLedger(self.db)
        self.normalcy = NormalcyModel(self.db)
        self.users = UserStore(self.db)
        self.login_throttle = LoginThrottle()
        self.sync = SyncManager(
            self.db, self.settings.core_url, self.settings.node_id,
            core_token=self.settings.core_token,
            retry_seconds=self.settings.sync_retry_seconds,
            batch_size=self.settings.sync_batch_size,
            queue_max_bytes=self.settings.queue_max_bytes,
            enabled=self.settings.sync_enabled,
        )
        self.sync.attach_evidence_store(self.evidence)
        # One governor for the whole node, not one per camera: the budget is a
        # budget on a human being's attention, and that human watches the site.
        self.governor = AlertGovernor(self.settings.alert_budget_per_hour)
        self.pipelines: dict[str, CameraPipeline] = {}
        self.detector = None
        self.segmenter = None
        # Cross-camera reasoning is node-wide: one coordinator consumes tracks
        # from every pipeline.
        self.coordinator = CrossCameraCoordinator(demo_topology(), self.settings.node_id)
        # Friendly-force suppression. The roster is declared configuration, the
        # matcher is the policy; both are node-wide, because a patrol crosses
        # cameras and its suppression budget has to be shared across them.
        self.patrol_roster = PatrolRoster(self.db)
        self.patrol = PatrolMatcher(self.patrol_roster)
        self.recent_handoffs = deque(maxlen=120)
        self._crosscam_task = None
        self.plate_reader = None
        self._plate_readers: dict[str, Any] = {}
        # A short rolling log, so a read survives the vehicle driving out of
        # frame. Per-track state is discarded when the track retires, which is
        # correct for the pipeline and useless for an operator asking what was
        # just read.
        self.recent_plate_reads: list[dict[str, Any]] = []
        # Plate history is node-wide, not per camera: the signal that matters on
        # an open border is the same vehicle appearing at several places.
        self.repeat_plates = RepeatPlateTracker(
            window_hours=self.settings.anpr_repeat_window_hours,
            min_sightings=self.settings.anpr_repeat_min_sightings,
        )
        self.started_at = time.monotonic()

    async def start(self) -> None:
        password = self.users.ensure_bootstrap_admin()
        if password:
            log.warning("=" * 66)
            log.warning("  Initial administrator account created")
            log.warning("     username: admin")
            log.warning("     password: %s", password)
            log.warning("  This is shown once. Store it now.")
            log.warning("=" * 66)

        if self.settings.demo_mode:
            bootstrap_demo_site(self.db, self.settings)
            # Refreshed each start: the demo windows are relative to the clock,
            # so that the patrol layer can be demonstrated at any hour. Profiles
            # an operator entered are never touched.
            seed_demo_patrols(self.patrol_roster, self.settings.node_id)

        # Hydrate persistent state
        handoffs = self.db.get_node_state("recent_handoffs")
        if handoffs:
            self.recent_handoffs.extend(handoffs.get("handoffs", []))

        from prahari.edge.crosscam.coordinator import Entity
        for gid, data in self.db.list_global_entities().items():
            try:
                self.coordinator._entities[int(gid)] = Entity.from_dict(data)
                # Keep the id counter above any loaded IDs
                while next(self.coordinator._ids) <= int(gid):
                    pass
            except Exception:
                log.exception("failed to load global entity %s", gid)

        from prahari.edge.anpr import PlateSighting
        for plate, data in self.db.list_plates().items():
            try:
                sightings = [
                    PlateSighting(
                        plate=s["plate"],
                        camera_id=s["camera_id"],
                        at=datetime.fromisoformat(s["at"]),
                        confidence=s["confidence"]
                    )
                    for s in data.get("sightings", [])
                ]
                self.repeat_plates.sightings[plate] = sightings
            except Exception:
                log.exception("failed to load plate %s", plate)

        self.detector = build_detector(self.settings)
        log.info("detector backend: %s", self.detector.describe())

        self.segmenter = build_segmenter(self.settings)
        if self.segmenter is not None:
            self.segmenter.warmup()
            log.info("segmentation backend: %s", self.segmenter.describe())



        for camera in self.db.list_cameras():
            if not camera.enabled:
                continue
            await self.add_pipeline(camera)

        await self.sync.start()
        self._crosscam_task = asyncio.create_task(self._crosscam_loop(),
                                                  name="prahari-crosscam")
        log.info("edge node %s ready with %d camera(s)",
                 self.settings.node_id, len(self.pipelines))

    def _record_plate_read(self, entry: dict[str, Any]) -> None:
        self.recent_plate_reads.append(entry)
        if len(self.recent_plate_reads) > 200:
            del self.recent_plate_reads[:-200]

    def _plate_reader_for(self, camera: Camera, source) -> Any:
        """A plate reader for this camera, called lazily once it is certified.

        Readers are shared per source kind so the ONNX sessions are loaded at
        most once each, and only when some camera has actually earned the right
        to read plates.
        """
        simulated = bool(getattr(source, "is_simulated", False))
        key = ("sim" if simulated else "real")
        if key not in self._plate_readers:
            self._plate_readers[key] = build_plate_reader(
                self.settings, simulated_source=simulated)
            if self._plate_readers[key]:
                log.info("ANPR backend (%s sources): %s", key,
                         self._plate_readers[key].describe())
        return self._plate_readers[key]

    async def add_pipeline(self, camera: Camera) -> None:
        if camera.camera_id in getattr(self, "_supervisors", {}):
            return
            
        if not hasattr(self, "_supervisors"):
            self._supervisors = {}
            
        task = asyncio.create_task(self._pipeline_supervisor(camera), name=f"supervisor-{camera.camera_id}")
        self._supervisors[camera.camera_id] = task

    async def _pipeline_supervisor(self, camera: Camera) -> None:
        backoff = 2.0
        while True:
            pipeline = None
            try:
                source = build_source(camera, self.settings)
                warn_if_detector_cannot_see(self.detector, camera, source)
                pipeline = CameraPipeline(
                    camera=camera, source=source, detector=self.detector,
                    db=self.db, bus=self.bus, settings=self.settings,
                    evidence_store=self.evidence, ledger=self.ledger,
                    normalcy=self.normalcy, meter=self.sync.meter,
                    governor=self.governor,
                    plate_reader_factory=self._plate_reader_for,
                    on_plate_read=self._record_plate_read,
                    segmenter=self.segmenter,
                    repeat_plates=self.repeat_plates,
                    coordinator=self.coordinator,
                    on_handoff=self._on_handoff,
                    patrol_matcher=self.patrol,
                )
                self.pipelines[camera.camera_id] = pipeline
                await pipeline.start()
                
                if getattr(pipeline, "_task", None):
                    backoff = 2.0
                    await pipeline._task
            except asyncio.CancelledError:
                if pipeline:
                    await pipeline.stop()
                raise
            except Exception:
                log.exception("supervisor caught error for %s", camera.camera_id)
            finally:
                if pipeline:
                    await pipeline.stop()
                    self.pipelines.pop(camera.camera_id, None)
            
            await asyncio.sleep(backoff)
            backoff = min(300.0, backoff * 1.5)

    async def remove_pipeline(self, camera_id: str) -> None:
        if hasattr(self, "_supervisors") and camera_id in self._supervisors:
            task = self._supervisors.pop(camera_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        pipeline = self.pipelines.pop(camera_id, None)
        if pipeline:
            await pipeline.stop()

    async def stop(self) -> None:
        if self._crosscam_task:
            self._crosscam_task.cancel()
            try:
                await self._crosscam_task
            except asyncio.CancelledError:
                pass
        await self.sync.stop()
        for pipeline in list(self.pipelines.values()):
            await pipeline.stop()
        self.db.close()

    # -- cross-camera ----------------------------------------------------
    def _on_handoff(self, finding: dict) -> None:
        """A pipeline reported a cross-camera handoff. Log and publish it."""
        self.recent_handoffs.append(finding)
        self.bus.publish_soon(
            subj(self.settings.node_id, "crosscam", None),
            {"handoff": finding})

    async def _crosscam_loop(self) -> None:
        """Periodic corridor reasoning: raise dropout events and persist state."""
        tick_count = 0
        last_decay = self.db.get_node_state("last_decay")
        last_decay_time = datetime.fromisoformat(last_decay["time"]) if last_decay else datetime.now(timezone.utc)

        while True:
            try:
                await asyncio.sleep(5.0)
                tick_count += 1
                now_dt = datetime.now(timezone.utc)
                
                # Daily tasks (Normalcy decay and Evidence pruning)
                days_elapsed = (now_dt - last_decay_time).total_seconds() / 86400.0
                if days_elapsed >= 1.0:
                    self.normalcy.decay(days_elapsed)
                    deleted = self.evidence.prune(self.settings.evidence_retention_days)
                    if deleted > 0:
                        log.info("retention scheduler pruned %d old evidence days", deleted)
                    last_decay_time = now_dt
                    self.db.set_node_state("last_decay", {"time": now_dt.isoformat()})

                findings = self.coordinator.tick(now_dt)
                for f in findings:
                    if f.get("kind") == "corridor_dropout":
                        await self._emit_crosscam_event(f)
                
                # Persist state every 15 seconds (3 ticks)
                if tick_count % 3 == 0:
                    self.db.set_node_state("recent_handoffs", {"handoffs": list(self.recent_handoffs)})
                    for gid, ent in self.coordinator._entities.items():
                        self.db.upsert_global_entity(str(gid), ent.as_dict())
                    for plate, sightings in self.repeat_plates.sightings.items():
                        self.db.upsert_plate_sighting(plate, {
                            "sightings": [
                                {"plate": s.plate, "camera_id": s.camera_id,
                                 "at": s.at.isoformat(), "confidence": s.confidence}
                                for s in sightings
                            ]
                        })
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cross-camera loop error")

    async def _emit_crosscam_event(self, finding: dict) -> None:
        """Turn a corridor-dropout finding into a sealed, alertable event.

        It has no single trigger frame - the object already left the corridor -
        so it carries metadata and its cross-camera trail rather than a clip. It
        is sealed into the same hash chain and synchronised like any other event.
        """
        now = datetime.now(timezone.utc)
        score, factors, priority = score_event(
            EventType.CORRIDOR_DROPOUT, None,
            ScoringContext(extra={"corridor dropout": 0.12}))
        event = Event(
            event_id="EVT-" + uuid4().hex[:12].upper(),
            camera_id=finding.get("camera_id", "SECTOR"),
            node_id=self.settings.node_id,
            event_type=EventType.CORRIDOR_DROPOUT,
            priority=priority, priority_score=score, priority_factors=factors,
            object_class=ObjectClass(finding.get("object_class", "unknown"))
            if finding.get("object_class") in ObjectClass._value2member_map_
            else ObjectClass.UNKNOWN,
            timestamp=now, monotonic_ns=time.monotonic_ns(),
            summary=finding.get("summary", "corridor dropout"),
            detail={"crosscam": finding},
        )
        if self.governor is not None:
            decision = self.governor.decide(event)
            event.alerted = decision.alerted
            event.alert_decision = decision.reason
        self.db.insert_event(self.ledger.append(event))
        log.info("[SECTOR] CORRIDOR_DROPOUT %s", finding.get("summary", ""))
        await self.bus.publish(
            subj(self.settings.node_id, "events", event.camera_id),
            {"event": event.model_dump(mode="json")})

    # -- status ----------------------------------------------------------
    def system_status(self) -> SystemStatus:
        online = sum(1 for p in self.pipelines.values()
                     if p.camera.health.value == "online")
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        disk = shutil.disk_usage(self.settings.data_dir)
        return SystemStatus(
            node_id=self.settings.node_id,
            node_name=self.settings.node_name,
            sector=self.settings.sector,
            link_mode=self.sync.link_mode,
            core_reachable=self.sync.core_reachable,
            cameras_total=len(self.pipelines),
            cameras_online=online,
            events_total=self.db.count_events(),
            events_pending_sync=self.db.count_events(SyncState.PENDING),
            queue_bytes=self.db.queue_bytes(),
            alerts_last_hour=self.db.alerts_since(hour_ago, min_rank=2),
            alert_budget_per_hour=self.settings.alert_budget_per_hour,
            disk_free_bytes=disk.free,
            uptime_seconds=round(time.monotonic() - self.started_at, 1),
            detector_backend=self.detector.name if self.detector else "none",
            device=self.detector.device if self.detector else "cpu",
        )


runtime = NodeRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="Prahari Edge Node",
    description=(
        "AI video-intelligence layer for existing CCTV infrastructure. "
        "Camera-aware analytics, edge-first processing, evidence-backed events "
        "and store-and-forward operation across connectivity loss."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# =====================================================================
# Auth plumbing
# =====================================================================

async def current_principal(
    authorization: str = Header(default=""),
    token: str | None = None
) -> Principal:
    tok = authorization.removeprefix("Bearer ").strip()
    if not tok and token:
        tok = token
    principal = verify_token(tok, runtime.settings.secret_key) if tok else None
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def require(role: str):
    async def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.may(role):  # type: ignore[arg-type]
            raise HTTPException(
                status_code=403,
                detail=f"this action requires the {role} role; you have {principal.role}",
            )
        return principal
    return _dep


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login", tags=["auth"])
async def login(req: LoginRequest, request: Request):
    client_ip = str(request.client.host if request.client else "?")
    # Brute-force protection: throttle per (username, source-IP) so a campaign of
    # guesses against one account from one host is locked out, without letting an
    # attacker lock a legitimate operator signing in from elsewhere.
    throttle_key = f"{req.username}|{client_ip}"
    wait = runtime.login_throttle.retry_after(throttle_key)
    if wait > 0:
        runtime.db.audit(req.username, "auth.throttled", detail=client_ip)
        raise HTTPException(status_code=429, detail="too many attempts; try later",
                            headers={"Retry-After": str(int(wait) + 1)})

    principal = runtime.users.authenticate(req.username, req.password)
    if principal is None:
        locked = runtime.login_throttle.record_failure(throttle_key)
        runtime.db.audit(req.username, "auth.failed",
                         detail=f"{client_ip}"
                                + (f" (locked {int(locked)}s)" if locked else ""))
        raise HTTPException(status_code=401, detail="invalid username or password")

    runtime.login_throttle.record_success(throttle_key)
    token = issue_token(principal.username, principal.role,
                        runtime.settings.secret_key,
                        runtime.settings.token_ttl_seconds)
    runtime.db.audit(principal.username, "auth.login")
    return {"token": token, "username": principal.username, "role": principal.role,
            "expires_in": runtime.settings.token_ttl_seconds}


@app.get("/api/auth/me", tags=["auth"])
async def me(principal: Principal = Depends(current_principal)):
    return {"username": principal.username, "role": principal.role}


# =====================================================================
# System
# =====================================================================

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "node": runtime.settings.node_id}


@app.get("/api/system/status", tags=["system"])
async def system_status(principal: Principal = Depends(current_principal)):
    status = runtime.system_status()
    return {
        **status.model_dump(mode="json"),
        "sync": runtime.sync.status(),
        "alerting": runtime.governor.status(),
        "detector": runtime.detector.describe() if runtime.detector else {},
        "segmentation": (runtime.segmenter.describe() if runtime.segmenter
                         else {"enabled": False}),
        "anpr": ({"enabled": True,
                  "backends": [r.describe() for r in runtime._plate_readers.values() if r]}
                 if runtime._plate_readers
                 else {"enabled": False,
                       "note": "no camera on this node is certified for ANPR yet"}),
        "cameras": [p.status() for p in runtime.pipelines.values()],
    }


@app.get("/api/system/bandwidth", tags=["system"])
async def bandwidth(principal: Principal = Depends(current_principal)):
    return runtime.sync.meter.snapshot()


@app.get("/api/system/ledger/verify", tags=["system"])
async def verify_ledger(principal: Principal = Depends(current_principal)):
    """Walk the evidence hash chain and report the first break, if any."""
    return runtime.ledger.verify()


@app.get("/api/system/audit", tags=["system"])
async def audit_log(limit: int = 200, principal: Principal = Depends(require("admin"))):
    return {"entries": runtime.db.list_audit(limit)}


# =====================================================================
# Cameras
# =====================================================================

@app.get("/api/cameras", tags=["cameras"])
async def list_cameras(principal: Principal = Depends(current_principal)):
    out = []
    for camera in runtime.db.list_cameras():
        entry = camera.public_dict()
        pipeline = runtime.pipelines.get(camera.camera_id)
        entry["runtime"] = pipeline.status() if pipeline else None
        out.append(entry)
    return {"cameras": out}


@app.get("/api/cameras/{camera_id}", tags=["cameras"])
async def get_camera(camera_id: str, principal: Principal = Depends(current_principal)):
    camera = runtime.db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="no such camera")
    pipeline = runtime.pipelines.get(camera_id)
    cert = runtime.db.latest_certificate(camera_id)
    return {
        **camera.public_dict(),
        "runtime": pipeline.status() if pipeline else None,
        "certificate": cert.model_dump(mode="json") if cert else None,
        "zones": [z.model_dump(mode="json") for z in runtime.db.list_zones(camera_id)],
    }


@app.post("/api/cameras", tags=["cameras"])
async def create_camera(camera: Camera, principal: Principal = Depends(require("admin"))):
    if runtime.db.get_camera(camera.camera_id):
        raise HTTPException(status_code=409, detail="camera already exists")
    runtime.db.upsert_camera(camera)
    runtime.db.audit(principal.username, "camera.create", camera.camera_id)
    if camera.enabled:
        await runtime.add_pipeline(camera)
    return camera.public_dict()


@app.put("/api/cameras/{camera_id}", tags=["cameras"])
async def update_camera(camera_id: str, camera: Camera,
                        principal: Principal = Depends(require("admin"))):
    existing = runtime.db.get_camera(camera_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="no such camera")
    # An empty password in the request means "unchanged", not "clear it".
    # Otherwise every edit through the UI would silently drop the credentials.
    if not camera.password:
        camera.password = existing.password
    if not camera.username:
        camera.username = existing.username
    camera.camera_id = camera_id
    runtime.db.upsert_camera(camera)
    runtime.db.audit(principal.username, "camera.update", camera_id)
    await runtime.remove_pipeline(camera_id)
    if camera.enabled:
        await runtime.add_pipeline(camera)
    return camera.public_dict()


@app.delete("/api/cameras/{camera_id}", tags=["cameras"])
async def delete_camera(camera_id: str, principal: Principal = Depends(require("admin"))):
    await runtime.remove_pipeline(camera_id)
    if not runtime.db.delete_camera(camera_id):
        raise HTTPException(status_code=404, detail="no such camera")
    runtime.db.audit(principal.username, "camera.delete", camera_id)
    return {"deleted": camera_id}


@app.post("/api/cameras/{camera_id}/test", tags=["cameras"])
async def test_camera(camera_id: str, principal: Principal = Depends(require("admin"))):
    """Probe a camera and report what actually came back.

    Reports CONNECTED only after a frame has genuinely been read. A stream that
    opens and then delivers nothing is reported FAILED, because reporting it as
    connected is a lie the operator discovers at the worst possible moment.
    """
    camera = runtime.db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="no such camera")
    if camera.source_kind == "simulator":
        return {"status": "CONNECTED", "simulated": True,
                "note": "simulated source; no network connection is involved"}
    source = build_source(camera, runtime.settings)
    result = await asyncio.get_running_loop().run_in_executor(
        None, source.test_connection)
    runtime.db.audit(principal.username, "camera.test", camera_id, result.get("status"))
    return result


@app.post("/api/cameras/{camera_id}/profile", tags=["cameras"])
async def reprofile_camera(camera_id: str, principal: Principal = Depends(require("admin"))):
    """Discard the current certificate and re-measure from scratch."""
    pipeline = runtime.pipelines.get(camera_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="camera is not running")
    from prahari.edge.profiling.measure import CameraProfiler

    pipeline.profiler = CameraProfiler(
        camera_id, min_frames=40, min_ground_samples=28,
        max_frames=int(pipeline.source.nominal_fps * 90),
    )
    pipeline.certificate = None
    pipeline.state = "profiling"
    runtime.db.audit(principal.username, "camera.reprofile", camera_id)
    return {"status": "profiling", "camera_id": camera_id,
            "note": "the camera will re-measure its optics and re-fit its ground "
                    "plane; analytics beyond person detection are suspended until "
                    "a new certificate is issued"}


@app.get("/api/cameras/{camera_id}/certificate", tags=["cameras"])
async def get_certificate(camera_id: str, principal: Principal = Depends(current_principal)):
    cert = runtime.db.latest_certificate(camera_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="no certificate issued yet")
    from prahari.edge.profiling.certificate import verify_certificate

    return {**cert.model_dump(mode="json"), "digest_valid": verify_certificate(cert)}


@app.get("/api/cameras/{camera_id}/stream", tags=["cameras"])
async def camera_stream(camera_id: str):
    """MJPEG preview.

    Unauthenticated on purpose: browsers cannot attach an Authorization header
    to an <img> tag, and the alternative - tokens in query strings - would write
    credentials into every proxy and access log between here and the operator.
    The stream carries no stored data and the node is expected to sit on an
    isolated operations network. docs/security.md records this as a known
    limitation with its intended fix (short-lived signed stream URLs).
    """
    pipeline = runtime.pipelines.get(camera_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="camera is not running")

    async def frames():
        boundary = b"--prahariframe\r\n"
        while camera_id in runtime.pipelines:
            jpeg = pipeline.latest_jpeg
            if jpeg:
                yield boundary + b"Content-Type: image/jpeg\r\n"
                yield f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                yield jpeg + b"\r\n"
            await asyncio.sleep(1.0 / max(1.0, pipeline.source.nominal_fps))

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=prahariframe",
    )


# =====================================================================
# Zones
# =====================================================================

@app.get("/api/zones", tags=["zones"])
async def list_zones(camera_id: str | None = None,
                     principal: Principal = Depends(current_principal)):
    return {"zones": [z.model_dump(mode="json")
                      for z in runtime.db.list_zones(camera_id)]}


@app.post("/api/zones", tags=["zones"])
async def create_zone(zone: Zone, principal: Principal = Depends(require("admin"))):
    runtime.db.upsert_zone(zone)
    pipeline = runtime.pipelines.get(zone.camera_id)
    if pipeline:
        pipeline.reload_zones()
    runtime.db.audit(principal.username, "zone.upsert", zone.zone_id)
    return zone.model_dump(mode="json")


@app.delete("/api/zones/{zone_id}", tags=["zones"])
async def delete_zone(zone_id: str, principal: Principal = Depends(require("admin"))):
    zones = runtime.db.list_zones()
    target = next((z for z in zones if z.zone_id == zone_id), None)
    if not runtime.db.delete_zone(zone_id):
        raise HTTPException(status_code=404, detail="no such zone")
    if target:
        pipeline = runtime.pipelines.get(target.camera_id)
        if pipeline:
            pipeline.reload_zones()
    runtime.db.audit(principal.username, "zone.delete", zone_id)
    return {"deleted": zone_id}


# =====================================================================
# Events and alerts
# =====================================================================

@app.get("/api/events", tags=["events"])
async def list_events(
    limit: int = 100, offset: int = 0, camera_id: str | None = None,
    min_priority: str = "info", unacknowledged: bool = False,
    alerted_only: bool = False,
    principal: Principal = Depends(current_principal),
):
    try:
        min_rank = Priority(min_priority).rank
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown priority")
    events = runtime.db.list_events(
        limit=min(limit, 500), offset=offset, camera_id=camera_id,
        min_rank=min_rank, unacknowledged_only=unacknowledged,
        alerted_only=alerted_only,
    )
    return {"events": [e.model_dump(mode="json") for e in events],
            "total": runtime.db.count_events()}


@app.get("/api/events/{event_id}", tags=["events"])
async def get_event(event_id: str, principal: Principal = Depends(current_principal)):
    event = runtime.db.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="no such event")
    return event.model_dump(mode="json")


@app.get("/api/events/{event_id}/export", tags=["events"])
async def export_evidence(
    event_id: str,
    principal: Principal = Depends(require("admin"))
):
    """Export a verifiable, tamper-evident bundle for this event."""
    event = runtime.db.get_event(event_id)
    if not event or not event.evidence:
        raise HTTPException(404, "Event or evidence missing")
    
    import zipfile
    import io
    import json
    
    # In a real app this creates a zip asynchronously or streams it,
    # but for Prahari edge node this simple in-memory zip suffices for the demo.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add event metadata
        zf.writestr("event.json", json.dumps(event.as_dict(), indent=2))
        
        # Add evidence metadata (which has the hashes of the clips/frames)
        d = runtime.evidence._dir_for(event.camera_id, event_id, event.timestamp)
        meta_path = d / "meta.json"
        if meta_path.exists():
            zf.write(meta_path, arcname="manifest.json")
            
        # Add actual media files
        if event.evidence.trigger_frame:
            p = d / "frame.jpg"
            if p.exists(): zf.write(p, arcname="frame.jpg")
            
        if event.evidence.clip_path:
            p = d / "clip.mp4"
            if p.exists(): zf.write(p, arcname="clip.mp4")
            
        if getattr(event.evidence, "mask_path", None):
            p = d / "mask.png"
            if p.exists(): zf.write(p, arcname="mask.png")
            
        # Add ledger/provenance context
        # In a real chain, we'd include the predecessor hashes and node signatures.
        provenance = {
            "node_id": runtime.settings.node_id,
            "exported_by": principal.username,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        zf.writestr("provenance.json", json.dumps(provenance, indent=2))
        
    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=evidence_{event_id}.zip"}
    )

@app.get("/api/events/{event_id}/evidence/{kind}", tags=["events"])
async def get_evidence(
    event_id: str, kind: str,
    principal: Principal = Depends(current_principal)
):
    event = runtime.db.get_event(event_id)
    if event is None or event.evidence is None:
        raise HTTPException(status_code=404, detail="no evidence for this event")
    ref = event.evidence
    path_str = {"frame": ref.frame_path, "thumb": ref.thumb_path,
                "clip": ref.clip_path}.get(kind)
    if not path_str:
        raise HTTPException(
            status_code=404,
            detail=(f"no {kind} for this event"
                    + (" (the clip was evicted under storage pressure; the trigger "
                       "frame and hashes were retained)"
                       if kind == "clip" and event.sync_state is SyncState.EVIDENCE_EVICTED
                       else "")),
        )
    path = Path(path_str)
    try:
        path.resolve().relative_to(runtime.settings.evidence_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid evidence path")
    if not path.exists():
        raise HTTPException(status_code=410, detail="evidence file is no longer on disk")
    media = "video/mp4" if kind == "clip" else "image/jpeg"
    return FileResponse(path, media_type=media)


class AckRequest(BaseModel):
    feedback: str | None = None       # "true_positive" | "false_alarm"
    note: str | None = None


@app.post("/api/alerts/{event_id}/acknowledge", tags=["alerts"])
async def acknowledge(event_id: str, req: AckRequest,
                      principal: Principal = Depends(require("operator"))):
    """Acknowledge an alert and, optionally, tell the system it was wrong.

    The feedback is the important half. It feeds the per-camera, per-event-type
    prior in the scorer, so a camera that keeps producing false alarms is damped
    automatically and the operator sees the alert rate fall rather than being
    asked to tolerate it indefinitely.
    """
    event = runtime.db.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="no such event")

    event.acknowledged = True
    event.acknowledged_by = principal.username
    event.acknowledged_at = datetime.now(timezone.utc)
    if req.feedback in ("true_positive", "false_alarm"):
        event.operator_feedback = req.feedback
        runtime.db.record_feedback(event.camera_id, event.event_type.value,
                                   is_false_alarm=(req.feedback == "false_alarm"))
    if req.note:
        event.detail["operator_note"] = req.note
    runtime.db.update_event(event)
    runtime.db.audit(principal.username, "alert.acknowledge", event_id, req.feedback or "")

    await runtime.bus.publish(
        subj(runtime.settings.node_id, "alerts", event.camera_id),
        {"acknowledged": event_id, "by": principal.username},
    )
    tp, fa = runtime.db.feedback_for(event.camera_id, event.event_type.value)
    return {"event_id": event_id, "acknowledged": True,
            "feedback": event.operator_feedback,
            "camera_feedback_totals": {"true_positive": tp, "false_alarm": fa}}


@app.get("/api/alerts/feedback", tags=["alerts"])
async def feedback_summary(principal: Principal = Depends(current_principal)):
    """Per-camera false-alarm accounting - the number that should fall over time."""
    rows = runtime.db.all_feedback()
    total_tp = sum(r["true_positive"] for r in rows)
    total_fa = sum(r["false_alarm"] for r in rows)
    total = total_tp + total_fa
    return {
        "by_camera": rows,
        "totals": {
            "true_positive": total_tp,
            "false_alarm": total_fa,
            "false_alarm_rate": round(total_fa / total, 3) if total else None,
            "note": ("Rate is over operator-reviewed alerts only, not over all "
                     "detections, and carries no claim about alerts nobody reviewed."),
        },
    }


@app.get("/api/anpr/plates", tags=["anpr"])
async def anpr_plates(principal: Principal = Depends(current_principal)):
    """Plate sightings held on this node.

    Deliberately counts and timestamps only. This is not a movement profile of
    an identified person: it records that a registration was seen by a camera at
    a time, which is what the repeat-entity signal needs and nothing more. See
    docs/privacy.md.
    """
    if not runtime._plate_readers:
        return {"enabled": False,
                "note": ("no camera on this node is certified for plate reading, "
                         "or none has finished profiling yet"),
                "plates": []}

    tracker = runtime.repeat_plates
    plates = []
    for plate, sightings in tracker.sightings.items():
        plates.append({
            "plate": plate,
            "sightings": len(sightings),
            "cameras": sorted({s.camera_id for s in sightings}),
            "first_seen": min(s.at for s in sightings).isoformat(),
            "last_seen": max(s.at for s in sightings).isoformat(),
            "night_sightings": sum(1 for s in sightings
                                   if tracker._is_unusual_hour(s.at)),
            "mean_confidence": round(
                sum(s.confidence for s in sightings) / len(sightings), 3),
        })
    plates.sort(key=lambda p: p["sightings"], reverse=True)
    return {
        "enabled": True,
        "backends": [r.describe() for r in runtime._plate_readers.values() if r],
        "recent_reads": runtime.recent_plate_reads[-40:],
        "window_hours": tracker.window_hours,
        "plates": plates,
        "note": ("Plates read on simulated imagery are synthetic demonstration "
                 "data and are not real vehicle registrations."),
    }


# =====================================================================
# Cross-camera intelligence
# =====================================================================

@app.get("/api/crosscam/sector", tags=["crosscam"])
async def crosscam_sector(principal: Principal = Depends(current_principal)):
    """Global entities tracked across cameras, plus the topology.

    This is the sector view a single camera cannot give: which objects have been
    handed off between cameras, how many cross more than one, and the corridor
    graph the reasoning runs on.
    """
    view = runtime.coordinator.sector_view()
    view["recent_handoffs"] = list(runtime.recent_handoffs)[-30:]
    return view


@app.get("/api/crosscam/topology", tags=["crosscam"])
async def crosscam_topology(principal: Principal = Depends(current_principal)):
    """The camera adjacency graph with learned transition times."""
    return runtime.coordinator.topology.as_dict()


@app.get("/api/crosscam/entities/{global_id}", tags=["crosscam"])
async def crosscam_entity(global_id: int,
                          principal: Principal = Depends(current_principal)):
    ent = runtime.coordinator.entity(global_id)
    if ent is None:
        raise HTTPException(status_code=404, detail="no such global entity")
    return ent.as_dict()


# =====================================================================
# Friendly-force (patrol) suppression
# =====================================================================

@app.get("/api/patrols", tags=["patrols"])
async def list_patrols(principal: Principal = Depends(current_principal)):
    """The declared patrol roster, plus what the layer has done with it."""
    data = runtime.patrol_roster.as_dict()
    data["metrics"] = runtime.patrol.metrics.as_dict()
    data["policy"] = {
        "strong_match": STRONG_MATCH,
        "uncertain_match": UNCERTAIN_MATCH,
        "note": ("A patrol profile is a declaration made by somebody at the "
                 "post, not something the node inferred. Suppression requires "
                 "every checkable cue to agree at once; matching a patrol's "
                 "identity while breaking its expectations raises the priority "
                 "instead of lowering it. No event is ever deleted or hidden."),
    }
    return data


@app.post("/api/patrols", tags=["patrols"])
async def upsert_patrol(profile: PatrolProfile,
                        principal: Principal = Depends(require("admin"))):
    saved = runtime.patrol_roster.upsert(profile)
    runtime.db.audit(principal.username, "patrol.upsert", profile.patrol_id,
                     {"cameras": profile.cameras, "active": profile.active,
                      "windows": [w.describe() for w in profile.windows]})
    return saved.model_dump(mode="json")


@app.post("/api/patrols/{patrol_id}/active", tags=["patrols"])
async def set_patrol_active(patrol_id: str, active: bool = True,
                            principal: Principal = Depends(require("admin"))):
    updated = runtime.patrol_roster.set_active(patrol_id, active)
    if updated is None:
        raise HTTPException(status_code=404, detail="no such patrol profile")
    runtime.db.audit(principal.username, "patrol.set_active", patrol_id,
                     {"active": active})
    return updated.model_dump(mode="json")


@app.delete("/api/patrols/{patrol_id}", tags=["patrols"])
async def delete_patrol(patrol_id: str,
                        principal: Principal = Depends(require("admin"))):
    if not runtime.patrol_roster.remove(patrol_id):
        raise HTTPException(status_code=404, detail="no such patrol profile")
    runtime.db.audit(principal.username, "patrol.delete", patrol_id)
    return {"deleted": patrol_id}


@app.get("/api/patrols/metrics", tags=["patrols"])
async def patrol_metrics(principal: Principal = Depends(current_principal)):
    """What the patrol layer did to this node's events during this run."""
    return {
        "metrics": runtime.patrol.metrics.as_dict(),
        "budgets": runtime.patrol.budget_state(),
        "active_profiles": sum(1 for p in runtime.patrol_roster.all() if p.active),
    }


@app.get("/api/patrols/scenarios", tags=["patrols"])
async def patrol_scenarios(principal: Principal = Depends(current_principal)):
    """Run the five reference scenarios through the real matching engine.

    Nothing here is a stored result: the same `PatrolMatcher` the pipeline uses
    is run over five fixed observations and its actual decisions are returned
    alongside the expected ones, so a disagreement is visible rather than
    presentable.
    """
    results = run_scenarios()
    return {
        "scenarios": results,
        "all_passed": all(r["passed"] for r in results),
        "note": ("Evaluated live against the production matching engine on "
                 "fixed, timezone-independent observations. These are "
                 "deterministic policy checks, not measurements on real "
                 "footage."),
    }


# =====================================================================
# Demo controls
# =====================================================================

class DemoAction(BaseModel):
    action: str
    camera_id: str | None = None
    plate: str | None = None
    size: int | None = None
    mode: str | None = None


@app.post("/api/demo/action", tags=["demo"])
async def demo_action(req: DemoAction, principal: Principal = Depends(require("operator"))):
    """Drive the demonstration.

    Every action here injects something into the *simulated scene* and then
    leaves the real pipeline to detect it. Nothing fabricates an event directly:
    the intruder is drawn into the frame and has to be detected, tracked and
    ruled on like anything else, which is what makes the demo a demonstration
    rather than a slideshow.
    """
    action = req.action
    camera_id = req.camera_id or "CAM-014"
    pipeline = runtime.pipelines.get(camera_id)

    def sim_of(p):
        src = p.source if p else None
        return src if src is not None and getattr(src, "is_simulated", False) else None

    if action == "network_outage":
        runtime.sync.force_offline = True
        runtime.sync.link_mode = LinkMode.OFFLINE
        runtime.db.audit(principal.username, "demo.network_outage")
        return {"status": "offline",
                "note": "the link is now down. Detection continues; events queue locally."}

    if action == "network_restore":
        runtime.sync.force_offline = False
        await runtime.sync.tick()
        runtime.db.audit(principal.username, "demo.network_restore")
        return {"status": "restored", "sync": runtime.sync.status()}

    if pipeline is None:
        raise HTTPException(status_code=404, detail="camera is not running")
    sim = sim_of(pipeline)
    if sim is None and action != "tamper":
        raise HTTPException(
            status_code=400,
            detail="this action only applies to a simulated camera")

    if action == "journey":
        # Stage a corridor journey: a subject walks the lawful corridor, appearing
        # at each camera in turn at the topology's transition times, so the
        # coordinator can hand it off across cameras live. The independent demo
        # scenes never share an object otherwise.
        import asyncio as _asyncio
        corridor = [("CAM-014", 0.0), ("CAM-022", 35.0), ("CAM-011", 85.0)]
        async def _walk():
            for cam, delay in corridor:
                if delay:
                    await _asyncio.sleep(delay)
                p = runtime.pipelines.get(cam)
                s = getattr(p, "source", None) if p else None
                if s is not None and getattr(s, "is_simulated", False):
                    s.inject_intruder()
        _asyncio.create_task(_walk())
        return {"status": "journey",
                "note": "a subject will walk the corridor CAM-014 -> CAM-022 -> "
                        "CAM-011 at the learned transition times; watch "
                        "Cross-Camera for the handoffs"}

    if action == "patrol":
        # Walk the declared patrol on its own route, in its own direction. The
        # matcher then has to decide, from the real track, whether this conforms
        # - nothing about the outcome is pre-arranged.
        ids = sim.inject_patrol(size=req.size or 2)
        return {"status": "injected", "camera_id": camera_id, "actor_ids": ids,
                "note": ("a foot patrol is walking its route inbound. If this "
                         "camera is on an active patrol profile and the window "
                         "is open, the resulting events are recorded and sealed "
                         "but not raised as alerts - watch Patrol Suppression")}

    if action == "patrol_reverse":
        ids = sim.inject_patrol(size=req.size or 2, outbound=True)
        return {"status": "injected", "camera_id": camera_id, "actor_ids": ids,
                "note": ("the patrol is walking its route backwards. Matching "
                         "the patrol on schedule and route while breaking its "
                         "expected direction is escalated, not suppressed")}

    if action == "intrusion":
        actor = sim.inject_intruder()
        return {"status": "injected", "camera_id": camera_id, "actor_id": actor,
                "note": "a subject is approaching the fence line and will cross it"}

    if action == "loiter":
        actor = sim.inject_loiterer()
        return {"status": "injected", "camera_id": camera_id, "actor_id": actor,
                "note": f"subject will dwell past the "
                        f"{runtime.settings.loitering_threshold_seconds:.0f}s threshold"}

    if action == "group":
        ids = sim.inject_group(size=req.size or 4)
        return {"status": "injected", "camera_id": camera_id, "actor_ids": ids}

    if action == "vehicle":
        actor = sim.inject_vehicle(plate=req.plate or "WB24AB1234")
        return {"status": "injected", "camera_id": camera_id, "actor_id": actor,
                "note": "synthetic plate, labelled as demo data; it will only be "
                        "read once the vehicle enters the image band this "
                        "camera is certified for"}

    if action == "tamper":
        mode = req.mode or "covered"
        if sim is None:
            raise HTTPException(status_code=400, detail="simulated cameras only")
        sim.set_tamper(None if mode == "clear" else mode)
        pipeline.tamper.__init__()      # reset the detector's streak state
        return {"status": "tamper", "mode": mode, "camera_id": camera_id}

    raise HTTPException(status_code=400, detail=f"unknown demo action: {action}")


# =====================================================================
# WebSocket
# =====================================================================

@app.websocket("/ws")
async def websocket_feed(ws: WebSocket, token: str | None = None):
    principal = verify_token(token, runtime.settings.secret_key) if token else None
    if principal is None:
        await ws.close(code=1008, reason="Authentication required")
        return
    await ws.accept()
    queue = runtime.bus.queue(f"prahari.{runtime.settings.node_id}.*", maxsize=128)
    status_task: asyncio.Task | None = None

    async def push_status():
        while True:
            try:
                await ws.send_json({
                    "subject": "system.status",
                    "data": {**runtime.system_status().model_dump(mode="json"),
                             "sync": runtime.sync.status()},
                })
            except Exception:
                return
            await asyncio.sleep(2.0)

    try:
        status_task = asyncio.create_task(push_status())
        while True:
            message = await queue.get()
            await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("websocket closed", exc_info=True)
    finally:
        if status_task:
            status_task.cancel()
        runtime.bus.release_queue(queue)


# =====================================================================
# Dashboard
# =====================================================================

if WEB_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root():
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({
        "service": "Prahari Edge Node",
        "node": runtime.settings.node_id,
        "docs": "/docs",
        "note": "dashboard not found; expected at web/index.html",
    })


def main() -> None:
    import uvicorn

    settings = get_settings()
    kwargs = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        kwargs["ssl_certfile"] = str(settings.ssl_certfile)
        kwargs["ssl_keyfile"] = str(settings.ssl_keyfile)

    uvicorn.run(
        "prahari.edge.app:app",
        host=settings.host, port=settings.port,
        reload=False, log_level="info",
        **kwargs
    )


if __name__ == "__main__":
    main()

"""Store-and-forward synchronisation and adaptive transmission.

The operating assumption is that the link to the sector core is unreliable, slow
and metered - a VSAT terminal or a marginal 4G backhaul at a border outpost, not
a datacentre uplink. Everything here follows from that:

*   **Detection never depends on the link.** The pipeline runs to completion at
    the edge whether or not anything upstream is reachable. Loss of connectivity
    degrades reporting, never sensing.

*   **Metadata is pushed, video is pulled.** An event record is a few hundred
    bytes and goes immediately. A clip is megabytes and stays at the edge until
    an operator asks for it. This is the single largest bandwidth lever, and it
    matters more than any codec choice.

*   **The queue degrades gracefully.** When storage runs out during a long
    outage, clips are dropped oldest-and-lowest-priority first. Event metadata
    is never dropped - a weakened record is recoverable, a missing one is not.

*   **Time is treated as suspect.** An edge node offline for days has no NTP and
    its clock drifts. Every event carries a monotonic reading alongside wall
    clock, so the core can correct the backlog on reconnection instead of
    silently trusting a drifted timestamp.

Bandwidth figures reported by this module are measured, not modelled: the
comparison baseline is the actual encoded size of the frames this node handled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from prahari.common.db import Database
from prahari.common.models import Event, LinkMode, SyncState

log = logging.getLogger("prahari.sync")


@dataclass
class BandwidthMeter:
    """Measured transmission accounting.

    `observed_frame_bytes` accumulates the encoded size of frames the node
    actually processed. That is the honest baseline for "what continuous
    streaming would have cost", because it is this node's own imagery at this
    node's own settings - not a brochure figure.
    """
    observed_frame_bytes: int = 0
    observed_frames: int = 0
    sent_bytes: int = 0
    sent_events: int = 0
    sent_evidence: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def observe_frame(self, encoded_bytes: int) -> None:
        self.observed_frame_bytes += encoded_bytes
        self.observed_frames += 1

    def record_sent(self, nbytes: int, *, evidence: bool = False) -> None:
        self.sent_bytes += nbytes
        if evidence:
            self.sent_evidence += 1
        else:
            self.sent_events += 1

    def snapshot(self) -> dict[str, Any]:
        elapsed = max(1e-6, time.monotonic() - self.started_at)
        continuous_bps = (self.observed_frame_bytes * 8) / elapsed
        actual_bps = (self.sent_bytes * 8) / elapsed
        saved_pct = 0.0
        if self.observed_frame_bytes > 0:
            saved_pct = max(0.0, 1.0 - (self.sent_bytes / self.observed_frame_bytes)) * 100
        return {
            "elapsed_seconds": round(elapsed, 1),
            "frames_observed": self.observed_frames,
            "continuous_stream_bitrate_bps": int(continuous_bps),
            "actual_uplink_bitrate_bps": int(actual_bps),
            "bytes_observed": self.observed_frame_bytes,
            "bytes_transmitted": self.sent_bytes,
            "reduction_percent": round(saved_pct, 2),
            "events_sent": self.sent_events,
            "evidence_sent": self.sent_evidence,
            "basis": (
                "Measured against the encoded size of frames this node actually "
                "processed during this run. Not a projection."
            ),
        }


class SyncManager:
    """Owns link state, the outbound queue and the eviction policy."""

    def __init__(
        self,
        db: Database,
        core_url: str,
        node_id: str,
        *,
        retry_seconds: float = 10.0,
        batch_size: int = 25,
        queue_max_bytes: int = 2 * 1024 * 1024 * 1024,
        enabled: bool = True,
    ) -> None:
        self.db = db
        self.core_url = core_url.rstrip("/")
        self.node_id = node_id
        self.retry_seconds = retry_seconds
        self.batch_size = batch_size
        self.queue_max_bytes = queue_max_bytes
        self.enabled = enabled

        self.link_mode = LinkMode.OFFLINE
        self.core_reachable = False
        self.meter = BandwidthMeter()
        self.last_sync_at: datetime | None = None
        self.last_error: str | None = None

        # Demo/testing override. When set, the node behaves as though the link
        # is physically down regardless of whether the core is actually up.
        self.force_offline = False
        # Set when the node believes its clock is untrustworthy (no NTP since
        # boot, or a long outage).
        self.clock_trusted = True

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._evidence_store = None

    def attach_evidence_store(self, store) -> None:
        self._evidence_store = store

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="prahari-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sync tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.retry_seconds)
            except asyncio.TimeoutError:
                pass

    # -- link state ------------------------------------------------------
    async def probe(self) -> bool:
        if self.force_offline or not self.enabled:
            self.core_reachable = False
            return False
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(self.core_url + "/health")
            self.core_reachable = r.status_code == 200
            if self.core_reachable:
                self.last_error = None
        except Exception as exc:
            self.core_reachable = False
            self.last_error = str(exc)[:200]
        return self.core_reachable

    def _recompute_mode(self, pending: int) -> None:
        if not self.core_reachable:
            self.link_mode = LinkMode.OFFLINE
        elif pending > self.batch_size:
            self.link_mode = LinkMode.SYNCING
        elif self.queue_pressure() > 0.75:
            # Running low on space: stop shipping evidence, keep shipping records.
            self.link_mode = LinkMode.EVENT_ONLY
        else:
            self.link_mode = LinkMode.ONLINE

    def queue_pressure(self) -> float:
        return min(1.0, self.db.queue_bytes() / max(1, self.queue_max_bytes))

    # -- the loop --------------------------------------------------------
    async def tick(self) -> None:
        pending_count = self.db.count_events(SyncState.PENDING)
        await self.probe()
        self._recompute_mode(pending_count)

        self.enforce_storage_policy()

        if not self.core_reachable:
            return

        batch = self.db.pending_events(self.batch_size)
        if not batch:
            self._recompute_mode(0)
            return

        await self.flush(batch)

    async def flush(self, batch: list[Event]) -> int:
        """Upload a batch. Idempotent: the core keys on event_id, so a retry
        after a half-failed upload cannot create duplicates."""
        payload = {
            "node_id": self.node_id,
            "clock_trusted": self.clock_trusted,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "events": [e.model_dump(mode="json") for e in batch],
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(self.core_url + "/api/ingest/events", json=payload)
                r.raise_for_status()
                body = r.json()
        except Exception as exc:
            self.last_error = str(exc)[:200]
            log.warning("event batch upload failed: %s", self.last_error)
            for ev in batch:
                ev.sync_state = SyncState.FAILED
                self.db.update_event(ev)
            return 0

        accepted = set(body.get("accepted", [e.event_id for e in batch]))
        import json as _json
        self.meter.record_sent(len(_json.dumps(payload).encode("utf-8")))

        now = datetime.now(timezone.utc)
        synced = 0
        for ev in batch:
            if ev.event_id not in accepted:
                continue
            # Ship evidence only when the link posture allows it. In EVENT_ONLY
            # the clip stays here and the core holds a reference it can request.
            if self.link_mode is LinkMode.ONLINE and ev.evidence:
                await self._push_evidence(ev)
            ev.sync_state = SyncState.SYNCED
            ev.synced_at = now
            self.db.update_event(ev)
            synced += 1

        self.last_sync_at = now
        if synced:
            log.info("synchronised %d event(s) to core", synced)
        return synced

    async def _push_evidence(self, event: Event) -> None:
        ref = event.evidence
        if not ref:
            return
        # The thumbnail, not the clip. A 20 KB still reaches the operator in
        # under a second on a VSAT link; a 4 MB clip does not, and on a metered
        # link it is the difference between a viable deployment and an invoice.
        path = Path(ref.thumb_path) if ref.thumb_path else None
        if not path or not path.exists():
            return
        try:
            blob = path.read_bytes()
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{self.core_url}/api/ingest/evidence/{event.event_id}",
                    content=blob,
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Prahari-Node": self.node_id,
                        "X-Prahari-Frame-SHA256": ref.frame_sha256 or "",
                    },
                )
                r.raise_for_status()
            self.meter.record_sent(len(blob), evidence=True)
        except Exception as exc:
            log.warning("evidence push failed for %s: %s", event.event_id, exc)

    # -- storage policy --------------------------------------------------
    def enforce_storage_policy(self) -> int:
        """Drop clips, never records, when the queue outgrows its budget."""
        freed = 0
        if self.db.queue_bytes() <= self.queue_max_bytes:
            return 0
        if self._evidence_store is None:
            return 0

        log.warning(
            "offline queue exceeds %d bytes; evicting evidence clips "
            "lowest-priority first (event records are retained)",
            self.queue_max_bytes,
        )
        for ev in self.db.eviction_candidates(limit=100):
            if self.db.queue_bytes() <= self.queue_max_bytes * 0.85:
                break
            if not ev.evidence:
                continue
            freed += self._evidence_store.evict_clip(ev.evidence)
            ev.evidence.clip_path = None
            ev.evidence.size_bytes = 0
            ev.sync_state = SyncState.EVIDENCE_EVICTED
            ev.detail["evidence_evicted_reason"] = (
                "clip discarded under storage pressure during an extended outage; "
                "event record, trigger frame and hashes retained"
            )
            self.db.update_event(ev)
        return freed

    # -- reporting -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        pending = self.db.count_events(SyncState.PENDING)
        failed = self.db.count_events(SyncState.FAILED)
        return {
            "link_mode": self.link_mode.value,
            "core_reachable": self.core_reachable,
            "core_url": self.core_url,
            "forced_offline": self.force_offline,
            "pending": pending,
            "failed": failed,
            "evicted": self.db.count_events(SyncState.EVIDENCE_EVICTED),
            "synced": self.db.count_events(SyncState.SYNCED),
            "queue_bytes": self.db.queue_bytes(),
            "queue_pressure": round(self.queue_pressure(), 3),
            "clock_trusted": self.clock_trusted,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_error": self.last_error,
            "bandwidth": self.meter.snapshot(),
        }

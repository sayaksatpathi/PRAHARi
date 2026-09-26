"""Prahari sector core - the aggregation point for a group of edge nodes.

The core deliberately does very little. It holds no cameras, runs no analytics
and makes no decisions that an edge node could not make alone. That asymmetry is
the architecture: an outpost that loses its uplink for three days must lose
*reporting*, not *sensing*, and the only way to guarantee that is to keep every
capability that matters on the edge side of the link.

What the core does own:

  * **Idempotent ingestion.** Events are keyed on the edge's own event_id, so a
    batch that half-succeeded before the link dropped can be retried in full
    without creating duplicates. This is what makes the retry path safe enough
    to be automatic.

  * **Clock-drift correction.** An edge node offline for days has no NTP and its
    wall clock drifts. Each batch carries the node's own idea of the time and a
    monotonic reading, so the core can compute the offset and record a corrected
    timestamp *alongside* the original rather than overwriting it. Both are kept:
    the original is what the node observed, the corrected one is what the sector
    timeline needs, and silently replacing one with the other would destroy the
    provenance the evidence chain exists to protect.

  * **Chain verification.** Events arrive with their ledger position and hash, so
    the core can confirm the backlog is internally consistent before accepting it.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from prahari.common.config import get_settings
from prahari.common.db import Database
from prahari.common.models import Event, SyncState
from prahari.core.notary import LedgerRewriteError, Notary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("prahari.core")

SETTINGS = get_settings()
CORE_DB_PATH = SETTINGS.data_dir / "prahari-core.db"
CORE_EVIDENCE_DIR = SETTINGS.data_dir / "core-evidence"

# A node whose clock differs from the core's by more than this is treated as
# drifted, and its events get a corrected timestamp recorded alongside.
CLOCK_DRIFT_TOLERANCE_S = 5.0


class CoreState:
    def __init__(self) -> None:
        self.db = Database(CORE_DB_PATH)
        CORE_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        self.nodes: dict[str, dict[str, Any]] = {}
        # The notary countersigns node chain heads with a core-only secret, so a
        # node cannot rewrite history the core has already witnessed.
        self.notary = Notary(self.db, SETTINGS.core_notary_secret)

    def close(self) -> None:
        self.db.close()


state = CoreState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("sector core ready (db=%s)", CORE_DB_PATH)
    try:
        yield
    finally:
        state.close()


app = FastAPI(
    title="Prahari Sector Core",
    description="Aggregation point for Prahari edge nodes.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "role": "sector-core",
            "nodes_seen": len(state.nodes)}


class IngestBatch(BaseModel):
    node_id: str
    sent_at: datetime
    monotonic_ns: int = 0
    clock_trusted: bool = True
    events: list[Event]


@app.post("/api/ingest/events", tags=["ingest"])
async def ingest_events(batch: IngestBatch):
    """Accept a batch of events from an edge node.

    Returns the ids actually accepted so the node can mark exactly those as
    synchronised. Anything omitted stays queued and is retried, which is what
    makes a half-delivered batch a non-event rather than a data-loss incident.
    """
    received_at = datetime.now(timezone.utc)
    drift = (received_at - batch.sent_at).total_seconds()
    drifted = abs(drift) > CLOCK_DRIFT_TOLERANCE_S or not batch.clock_trusted

    state.nodes[batch.node_id] = {
        "node_id": batch.node_id,
        "last_seen": received_at.isoformat(),
        "clock_offset_seconds": round(drift, 3),
        "clock_trusted": batch.clock_trusted,
        "events_received": state.nodes.get(batch.node_id, {}).get("events_received", 0)
                           + len(batch.events),
    }

    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[dict[str, Any]] = []

    for event in batch.events:
        existing = state.db.get_event(event.event_id)
        if existing is not None and existing.sync_state is SyncState.SYNCED:
            # Idempotent: the node is retrying a batch that partly landed.
            duplicates.append(event.event_id)
            accepted.append(event.event_id)
            continue

        # Notary rewrite guard: if the core has already countersigned this
        # ledger index, the node may not re-present it with different content.
        # Refuse the event rather than let INSERT OR REPLACE overwrite witnessed
        # history, and surface it as a tamper finding.
        if event.ledger_index:
            cp = state.db.checkpoint_at(batch.node_id, event.ledger_index)
            if cp is not None and cp["entry_hash"] != event.entry_hash:
                log.error("REWRITE REFUSED: node %s index %d — witnessed %s…, "
                          "presented %s…", batch.node_id, event.ledger_index,
                          cp["entry_hash"][:12], event.entry_hash[:12])
                rejected.append({
                    "event_id": event.event_id,
                    "ledger_index": event.ledger_index,
                    "reason": "ledger_rewrite_refused",
                    "witnessed": cp["entry_hash"],
                    "presented": event.entry_hash,
                })
                continue

        if drifted:
            # Record the correction; never overwrite what the node observed.
            corrected = event.timestamp + timedelta(seconds=drift)
            event.detail["clock_correction"] = {
                "node_reported": event.timestamp.isoformat(),
                "core_corrected": corrected.isoformat(),
                "offset_seconds": round(drift, 3),
                "reason": ("node clock differs from core beyond tolerance"
                           if batch.clock_trusted
                           else "node reported its clock as untrusted (no NTP "
                                "since boot or a prolonged outage)"),
            }
            event.clock_synced = False

        event.sync_state = SyncState.SYNCED
        event.synced_at = received_at
        state.db.insert_event(event)
        accepted.append(event.event_id)

    if accepted:
        log.info("ingested %d event(s) from %s (%d duplicate, clock offset %.2fs)",
                 len(accepted) - len(duplicates), batch.node_id,
                 len(duplicates), drift)

    # Advance the notary checkpoint over the node's (now-extended) chain head.
    # Rewrites were already refused above, so this cannot raise; the guard is
    # kept so a witnessing failure can never lose an otherwise-accepted batch.
    checkpoint = None
    try:
        entries = state.db.node_ledger_entries(batch.node_id)
        cp = state.notary.witness(batch.node_id, entries)
        if cp is not None:
            checkpoint = {"ledger_index": cp.ledger_index,
                          "entry_hash": cp.entry_hash,
                          "core_sig": cp.core_sig}
    except LedgerRewriteError as exc:
        log.error("notary witnessing refused for %s: %s", batch.node_id, exc)

    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "checkpoint": checkpoint,
        "clock_offset_seconds": round(drift, 3),
        "clock_corrected": drifted,
    }


@app.post("/api/ingest/evidence/{event_id}", tags=["ingest"])
async def ingest_evidence(
    event_id: str,
    request: Request,
    x_prahari_node: str = Header(default="unknown"),
    x_prahari_frame_sha256: str = Header(default=""),
):
    """Accept a thumbnail or frame for an already-ingested event."""
    blob = await request.body()
    if not blob:
        return JSONResponse({"error": "empty body"}, status_code=400)

    node_dir = CORE_EVIDENCE_DIR / x_prahari_node
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / f"{event_id}.jpg"
    path.write_bytes(blob)

    verified = None
    if x_prahari_frame_sha256:
        import hashlib

        verified = hashlib.sha256(blob).hexdigest() == x_prahari_frame_sha256

    return {"event_id": event_id, "bytes": len(blob),
            "hash_verified": verified,
            "note": ("hash mismatch: the evidence does not match what the node "
                     "committed to" if verified is False else None)}


@app.get("/api/events", tags=["events"])
async def list_events(limit: int = 100, node_id: str | None = None):
    events = state.db.list_events(limit=min(limit, 500))
    if node_id:
        events = [e for e in events if e.node_id == node_id]
    return {"events": [e.model_dump(mode="json") for e in events],
            "total": state.db.count_events()}


@app.get("/api/nodes", tags=["system"])
async def list_nodes():
    return {"nodes": list(state.nodes.values())}


@app.get("/api/ledger/verify", tags=["system"])
async def verify_ledger():
    """Confirm each node's chain is internally consistent.

    Verification is strictly per node. Every edge node maintains its own
    independent chain starting at index 1, so the core's event table interleaves
    several chains and walking it as one sequence would report a break on the
    first event from the second node - a false alarm about the integrity
    mechanism itself, which is worse than no check at all.
    """
    from prahari.edge.evidence import compute_entry_hash

    by_node: dict[str, list[Event]] = {}
    for event in state.db.list_events(limit=5000):
        if event.ledger_index:
            by_node.setdefault(event.node_id, []).append(event)

    results: dict[str, Any] = {}
    all_valid = True
    for node_id, events in by_node.items():
        events.sort(key=lambda e: e.ledger_index)
        prev_hash = ""
        verdict: dict[str, Any] = {"entries": len(events), "valid": True}
        for event in events:
            # A gap is expected: the core may legitimately not yet hold every
            # event a node has sealed. Only an actual mismatch is a failure.
            if event.prev_hash != prev_hash and event.ledger_index > 1:
                if not any(e.entry_hash == event.prev_hash for e in events):
                    verdict = {
                        "entries": len(events), "valid": False,
                        "broken_at": event.ledger_index,
                        "event_id": event.event_id,
                        "message": "predecessor hash refers to an entry the core "
                                   "has never received and cannot account for",
                    }
                    break
            if compute_entry_hash(event.ledger_index, event.prev_hash,
                                  event) != event.entry_hash:
                verdict = {
                    "entries": len(events), "valid": False,
                    "broken_at": event.ledger_index,
                    "event_id": event.event_id,
                    "message": "event content does not match the hash the node "
                               "committed to - it was altered in transit or at rest",
                }
                break
            prev_hash = event.entry_hash

        # Notary layer: confirm the core's own countersignature log is intact,
        # and that no event on record has drifted from what the core witnessed.
        # A rewrite that keeps the node's chain internally consistent (and so
        # passes the check above) is caught here, because it no longer matches
        # the head hash the core countersigned.
        entries = [(e.ledger_index, e.entry_hash) for e in events]
        chain = state.notary.verify_checkpoint_chain(node_id)
        audit = state.notary.audit_against_events(node_id, entries)
        verdict["notary"] = {
            "witnessed_up_to": chain.get("witnessed_up_to", 0),
            "checkpoints": chain.get("checkpoints", 0),
            "countersign_valid": chain["valid"],
            "no_rewrite": audit["valid"],
        }
        if not audit["valid"]:
            verdict["valid"] = False
            verdict["broken_at"] = audit["broken_at"]
            verdict["message"] = audit["message"]
        elif not chain["valid"]:
            verdict["valid"] = False
            verdict["message"] = chain["message"]

        results[node_id] = verdict
        all_valid = all_valid and verdict["valid"]

    return {"valid": all_valid, "nodes": results,
            "note": "each node keeps an independent chain from index 1; the core "
                    "countersigns chain heads so witnessed history cannot be rewritten"}


def main() -> None:
    import uvicorn

    port = int(SETTINGS.core_url.rsplit(":", 1)[-1].rstrip("/") or 9420)
    uvicorn.run("prahari.core.app:app", host="127.0.0.1", port=port,
                reload=False, log_level="info")


if __name__ == "__main__":
    main()

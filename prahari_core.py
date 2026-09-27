"""Self-contained crypto + data for the Prahari showcase.

The hash-chain and core-countersigning logic here mirrors the real
prahari/edge/evidence.py and prahari/core/notary.py exactly (same SHA-256 chain,
same HMAC countersignature), reimplemented with only the standard library so the
free Hugging Face Space runs it live with no heavy dependencies. It is a faithful
demonstration of the deployed mechanism, not a mock.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

CORE_SECRET = "sector-core-notary-key (never held by an edge node)"


def entry_hash(index: int, prev_hash: str, event: dict[str, Any]) -> str:
    """SHA-256 commitment to an event and its position, chained to its predecessor."""
    body = json.dumps({"i": index, "prev": prev_hash, **event}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_chain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain, prev = [], ""
    for i, ev in enumerate(events, 1):
        h = entry_hash(i, prev, ev)
        chain.append({"index": i, "prev": prev, "hash": h, "event": ev})
        prev = h
    return chain


def edge_verify(chain: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """The edge node's own check: walk the chain and confirm every link."""
    prev = ""
    for c in chain:
        if c["prev"] != prev:
            return False, c["index"]
        if entry_hash(c["index"], c["prev"], c["event"]) != c["hash"]:
            return False, c["index"]
        prev = c["hash"]
    return True, None


def core_countersign(index: int, entry_h: str, prev_sig: str,
                     node: str = "BOP-EDGE-01", secret: str = CORE_SECRET) -> str:
    """HMAC-SHA256 countersignature the sector core puts over a witnessed head."""
    payload = f"{node}|{index}|{entry_h}|{prev_sig}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def restamp_rewrite(chain: list[dict[str, Any]], victim_index: int,
                    new_event: dict[str, Any]) -> list[dict[str, Any]]:
    """The insider attack: edit one event, then re-chain everything after it.

    Produces a chain that is once again internally consistent, so the edge
    node's own edge_verify() accepts it. This is exactly the rewrite the core
    notary is built to catch.
    """
    new = [dict(c) for c in chain]
    prev = new[victim_index - 2]["hash"] if victim_index >= 2 else ""
    for c in new:
        if c["index"] < victim_index:
            continue
        if c["index"] == victim_index:
            c["event"] = new_event
        c["prev"] = prev
        c["hash"] = entry_hash(c["index"], prev, c["event"])
        prev = c["hash"]
    return new


# --- measured benchmark results (from the repo's docs, real numbers) ----------
BENCHMARKS = [
    ("Re-ID (Market-1501)", "OSNet Rank-1 0.947 / mAP 0.845", "MEASURED",
     "vs ResNet-18 0.705 — production default"),
    ("Face detection (WIDER FACE val)", "YuNet AP@0.5 0.626", "MEASURED",
     "vs Haar 0.121; Apache/MIT, deployable"),
    ("Detection + tracking (MOT17 held-out)", "yolov8m@1280 MOTA 0.435 / IDF1 0.563", "MEASURED",
     "beats YOLOX-S 0.397; 108 fps on RTX 4050"),
    ("ANPR (real Indian plates)", "Awiros PP-OCRv5 — accurate reads", "MEASURED",
     "Apache-2.0 Indian specialist; global OCR garbled state codes"),
    ("Scene anomaly (official protocol)", "UCSD Ped2 frame-AUC 0.907", "MEASURED",
     "Street Scene metrics; RBDC 0.648 / TBDC 0.612"),
    ("Normalcy (pattern-of-life)", "day routine / night unusual", "MEASURED",
     "learned via observe(); false-alert flood ~90% -> ~1-2%"),
    ("Full test suite", "184 passed / 3 skipped", "GREEN", "pytest"),
]

SECURITY = [
    ("Two-tier countersigned ledger", "BUILT · TESTED",
     "Core countersigns each node's chain head; a full-chain rewrite the edge "
     "chain passes, the notary catches."),
    ("Legal admissibility", "BUILT",
     "BSA §63(4)/§65B(4) certificate generated from the ledger."),
    ("Supply-chain integrity", "BUILT · TESTED",
     "SHA-256 verify per weight before load + HMAC-signed model manifest."),
    ("AuthN/Z", "BUILT · TESTED",
     "scrypt + per-user salt, constant-time compare; HMAC-signed expiring tokens; RBAC."),
    ("Brute-force protection", "BUILT · TESTED",
     "Per-(user,IP) sliding-window lockout, 429 + Retry-After."),
    ("Sensor tamper detection", "BUILT",
     "Per-camera learnt baseline: spray/blackout, glare, defocus, repoint, frozen-feed."),
    ("Offline integrity", "BUILT · TESTED",
     "Idempotent store-and-forward sync keyed on the edge's own event id."),
    ("STRIDE threat model", "DOCUMENTED",
     "Per-component, every control marked Built / Partial / Planned."),
]

CAMERAS = [
    ("CAM-011", "Main Gate — Vehicle Lane", "Chokepoint · FENCED · DORI IDENTIFY",
     "Indian traffic + pedestrians. ANPR active (reads real Indian plates)."),
    ("CAM-014", "Perimeter North — Fence Line", "Perimeter · FENCED · DORI OBSERVE",
     "Dense pedestrian flow, market crowd."),
    ("CAM-022", "Approach Track — Forest Route", "Approach · OPEN BORDER · DORI OBSERVE",
     "Open-border doctrine: pattern-of-life, not tripwires."),
    ("CAM-031", "Ridge Thermal", "Perimeter · FENCED · DORI OBSERVE",
     "Thermal colormap; detection holds on thermal imagery."),
    ("CAM-045", "Legacy Analogue — South Track", "Approach · FENCED · DORI RECOGNISE",
     "Degraded legacy feed — treated on its measured capability."),
    ("CAM-052", "Riverine Night Post — IR", "Approach · OPEN BORDER · night-capable",
     "Real low-light footage. Infiltration happens after dark."),
]

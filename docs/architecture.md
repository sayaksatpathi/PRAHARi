# Architecture

## The one decision everything else follows from

A border outpost loses its uplink. Not rarely — as a normal part of every week,
on VSAT or marginal 4G, metered and high-latency. Every structural choice in
Prahari follows from taking that seriously:

> **Loss of connectivity must degrade reporting, never sensing.**

That forces the capability split. Anything that must keep working during an
outage has to live on the edge side of the link, which turns out to be almost
everything that matters.

```
┌──────────────────── prahari-edge (at the border outpost) ─────────────────────┐
│                                                                               │
│  VideoSource ──▶ CameraProfiler ──▶ CapabilityCertificate                     │
│  (rtsp/file/sim)        │                    │                                │
│                         │                    │ gates what may run, and where  │
│                         ▼                    ▼                                │
│                    TamperDetector      Detector (ONNX | synthetic)            │
│                         │                    │                                │
│                         │                    ▼                                │
│                         │              ByteTracker                            │
│                         │                    │                                │
│                         │                    ▼                                │
│                         │              RuleEngine (fenced | open-border)      │
│                         │                    │                                │
│                         └──────────▶  score_event() ◀── NormalcyModel         │
│                                              │          (learnt per zone/hour)│
│                                    ┌─────────┴─────────┐                      │
│                                    ▼                   ▼                      │
│                             AlertGovernor       EvidenceStore                 │
│                            (rations alerts)    + EvidenceLedger (hash chain)  │
│                                    │                   │                      │
│                                    └────────┬──────────┘                      │
│                                             ▼                                 │
│                              SQLite (WAL) + local evidence disk               │
│                                             │                                 │
│                                      SyncManager                              │
└─────────────────────────────────────────────┼─────────────────────────────────┘
                                              │ metadata pushed, video pulled
                                              ▼
                              ┌──── prahari-core (sector) ────┐
                              │  idempotent ingestion         │
                              │  clock-drift correction       │
                              │  per-node chain verification  │
                              └───────────────────────────────┘
```

Two deployables. **The sync contract between them is the product.**

## Why the edge owns everything

| Concern | Lives at | Because |
|---|---|---|
| Cameras, inference, tracking | edge | must work with no link |
| Rules and scoring | edge | an event has to be *decided* locally or it cannot be queued |
| Evidence capture and sealing | edge | the video never leaves; only the record does |
| Queue and eviction policy | edge | the outage is the edge's problem to survive |
| Aggregation across outposts | core | the only thing that genuinely needs a wider view |
| Clock-drift correction | core | needs a reference the drifted node does not have |

The core holds no cameras and makes no decision the edge could not make alone. An
edge node with no core configured at all is a complete, working system.

## Pipeline order, and why it is that order

**Profiling gates everything downstream.** A camera starts in `profiling` and
runs person detection *only* — which is what it needs to observe pedestrians and
fit its ground plane. Until a certificate is issued, nothing else runs. This is
the difference between claiming camera-awareness and enforcing it: the gate is in
the execution path (`pipeline.py`, `allowed = certificate.granted()`), not in the
UI.

**Detection is decoupled from frame rate.** The detector runs every Nth frame
(`PRAHARI_INFERENCE_INTERVAL`); the tracker runs on every frame and coasts
between detections on a constant-velocity estimate. Running a detector on every
frame of every camera is how these deployments end up needing hardware nobody
budgeted for.

**Blocking work stays off the event loop.** Decode and inference run in a thread
executor. A camera on a wet PoE run — which is to say, routinely — must never be
able to freeze the dashboard or stall the sync queue.

**Recording and alerting are separate decisions.** Every event is written, sealed
and synchronised. The `AlertGovernor` decides only which of them interrupt a
human, against an hourly budget. This is the single most important behaviour in
the system and it is deliberately the last step, so nothing upstream can be
tempted to drop data to keep the alert rate down.

## Data flow for one event

1. `RuleEngine` produces an `EventCandidate` with a dedupe key. Hysteresis on
   zone membership and tripwire margins means detector jitter cannot manufacture
   one.
2. `NormalcyModel` is consulted **scoped to the zone** — a restricted zone is
   judged against that zone's own history, not the camera's.
3. `score_event()` produces a score plus the named factors that made it.
4. `AlertGovernor` decides `alerted` / `not alerted`, with a stated reason.
5. The trigger frame is written immediately; a `PendingClip` collects the
   post-roll, with the pre-roll already in hand from the ring buffer.
6. When the clip completes, the event is **sealed** into the hash chain. Until
   then it is recorded and alertable but not shippable, because the core cannot
   verify an unsealed record. A sweeper seals stragglers whose clip never
   finished.
7. `SyncManager` ships sealed events in priority order when the link allows.

## Storage

**SQLite, WAL, `synchronous=FULL` on the event path.** A single file, no server
process to supervise, crash-safe across power loss. The workload is a handful of
writes per second, so one connection behind a lock outperforms the complexity of
a pool. Raw `sqlite3` rather than an ORM because the durability semantics are the
point of the module and an ORM would hide exactly what needs controlling.

The same schema runs at the core and can be pointed at Postgres later; nothing
above `db.py` knows which.

**Evidence on local disk** under `YYYY/MM/DD/camera/event/`, with the frame,
thumbnail and clip. The ring buffer holds **JPEG-encoded** frames, not raw
arrays: raw 720p frames cost ~2.7 MB each, so a 3-second pre-roll across a dozen
cameras would consume over a gigabyte on a node that may have four.

## Bandwidth

Four postures, shown verbatim in the dashboard: `ONLINE`, `DEGRADED`,
`EVENT_ONLY`, `OFFLINE`, `SYNCING`.

The lever that matters is not codec choice. It is **push metadata, pull video**:
an event record is a few hundred bytes and goes immediately; a clip is megabytes
and stays at the edge until somebody asks. On a metered VSAT link that is the
difference between a viable deployment and an invoice.

Reported reduction is measured against the encoded size of frames this node
actually processed during this run — its own imagery at its own settings, not a
brochure figure.

## Degradation under pressure

Two policies, both stated as code rather than intention:

**Storage.** When the queue exceeds its budget, evidence *clips* are evicted
lowest-priority-and-oldest first. Event records are never evicted. A weakened
event is recoverable; a missing one is a hole in the account of the night.

**Attention.** When events exceed the hourly alert budget, the score threshold
rises until the rate fits — asymmetrically, rising fast so a flood is contained
in seconds and falling slowly so the threshold does not oscillate.

## Time

An edge node offline for days has no NTP and its clock drifts. Every event
carries `monotonic_ns` alongside wall-clock time. On reconnection the core
computes the offset and records a **corrected timestamp alongside the original**,
never overwriting it. Both are kept: the original is what the node observed, the
corrected one is what the sector timeline needs, and silently replacing one with
the other would destroy the provenance the evidence chain exists to protect.

## Where a real deployment would differ

- **Message bus.** In-process today, with NATS-shaped subjects and schemas. A
  JetStream deployment is a change to `bus.py` alone.
- **Core storage.** SQLite is fine for a sector demo; Postgres + TimescaleDB
  behind the same interface for a real one.
- **Object storage.** Local disk today; MinIO/S3 behind `EvidenceStore`.
- **Key material.** The chain is signed with a configured secret. Hardware-backed
  keys and core countersigning are what make it tamper-*resistant* rather than
  tamper-*evident*.
- **Edge identity.** mTLS with per-node certificates, edge dials out only — a BOP
  sits behind NAT and should expose no inbound port.

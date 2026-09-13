# Offline operation and store-and-forward

The design point, not a failure case. A border outpost on VSAT or marginal 4G
loses its uplink as a normal part of every week, and the link it does have is
metered and high-latency. One rule follows:

> **Loss of connectivity degrades reporting, never sensing.**

## What keeps running

Everything that produces intelligence. Frame capture, profiling, detection,
tracking, rule evaluation, scoring, alert governance, evidence capture and hash
sealing all run entirely at the edge with no dependency on the core being
reachable. `SyncManager` is the only component that cares about the link, and its
failure mode is a growing queue, not a stopped pipeline.

An edge node with no core configured at all is a complete, working system. The
operator interface is served by the node itself.

## Push metadata, pull video

The single largest bandwidth lever, and it matters more than any codec choice.

- An **event record** is a few hundred bytes. It goes immediately.
- A **thumbnail** is ~20 kB. It goes when the link posture allows, so an operator
  at the sector sees *something* within a second even on a poor link.
- A **clip** is megabytes. It stays at the edge until somebody asks for it.

On a metered VSAT link this is the difference between a viable deployment and an
invoice. Measured across development runs the reduction against continuous
streaming sits above 99% — and the figure is computed against the encoded size of
frames this node actually processed during that run, its own imagery at its own
settings, not a brochure number.

## Link postures

Shown verbatim in the dashboard so the operator always knows what the system is
doing:

| Posture | Meaning |
|---|---|
| `ONLINE` | Metadata and evidence both flowing |
| `DEGRADED` | Metadata and thumbnails; clips on request |
| `EVENT_ONLY` | Metadata only; evidence stays at the edge |
| `OFFLINE` | Nothing leaves; the local queue grows |
| `SYNCING` | Draining a backlog |

`EVENT_ONLY` is entered automatically under storage pressure — the node stops
spending disk on outbound evidence before it stops recording.

## The queue

SQLite, WAL, `synchronous=FULL` on the event write path. Events survive power
loss, which at an outpost on generator power is not a hypothetical.

Only **sealed** events are shippable. An event whose evidence clip is still being
captured is recorded and alertable but not yet in the hash chain, and shipping it
would hand the core a record it cannot verify. A sweeper seals stragglers whose
clip never completed, so a camera dropping out mid-capture cannot leave an event
permanently unsynced — a silent hole in the record is the one failure this system
must not have.

Drain order is **by priority, then age**. A three-day backlog should deliver the
significant events first, not the oldest ones.

## Degradation under storage pressure

The policy in one sentence: **drop clips, never records.**

When the queue exceeds `PRAHARI_QUEUE_MAX_BYTES`, evidence clips are evicted
lowest-priority-and-oldest first. The event record, its trigger frame and all its
hashes are retained, the event is marked `EVIDENCE_EVICTED`, and the reason is
written into the event itself:

> *clip discarded under storage pressure during an extended outage; event record,
> trigger frame and hashes retained*

A weakened event is recoverable. A missing one is a hole in the account of the
night, and no amount of disk pressure justifies creating one.

## Idempotent resync

Events are keyed on the edge's own `event_id`. A batch that half-landed before
the link dropped can be retried in full without creating duplicates — the core
reports which ids it accepted, and only those are marked synchronised locally.
Anything omitted stays queued and is retried. That is what makes the retry path
safe enough to be automatic.

## Clock drift

A node offline for days has no NTP and its wall clock drifts. Every event
therefore carries `monotonic_ns` alongside its timestamp, and every batch carries
the node's own idea of the time at the moment of sending.

On ingestion the core computes the offset and, if it exceeds tolerance or the
node has flagged its own clock as untrusted, records a **corrected timestamp
alongside the original**:

```json
"clock_correction": {
  "node_reported": "2026-09-13T02:14:31+00:00",
  "core_corrected": "2026-09-13T02:19:48+00:00",
  "offset_seconds": 317.2,
  "reason": "node clock differs from core beyond tolerance"
}
```

The original is never overwritten. It is what the node observed; the corrected one
is what the sector timeline needs. Silently replacing one with the other would
destroy exactly the provenance the evidence chain exists to protect.

## Verifying a backlog

A node that has been disconnected for three days is asking the core to accept a
large batch of events on trust. The hash chain is what turns that into something
checkable: each entry commits to the hash of the one before it, so altering or
removing any event breaks every link that follows.

The core verifies **per node**. Each edge node maintains an independent chain
starting at index 1, so the core's event table interleaves several chains, and
walking it as one sequence would report a break on the first event from the
second node — a false alarm about the integrity mechanism itself, which is worse
than no check at all.

`GET /api/ledger/verify` on the core returns a per-node verdict. `GET
/api/system/ledger/verify` on the edge walks that node's own chain.

## Testing it

The demonstration control *Cut the uplink* sets a flag that makes the node behave
as though the link is physically down, regardless of whether the core is actually
reachable. `scripts/smoke_e2e.py` drives the whole cycle and asserts that
detection continued, events queued, the queue drained, and timestamps survived.

## Limitations

- **Eviction is not yet bandwidth-aware.** It frees disk but does not reason
  about what is worth uploading when a narrow window opens.
- **No resumable chunked upload.** A clip transfer interrupted mid-way restarts.
  On a VSAT link with a short window that matters, and it is the next thing to
  build here.
- **The outage is simulated by a flag**, not by an unplugged cable. The queue,
  drain, idempotency and clock correction are all real; the disconnection itself
  is not.
- **Tamper-evident, not tamper-proof.** Anyone holding the node's key material
  could forge a consistent chain. Hardware-backed keys and core countersigning
  are what would make it resistant rather than merely evident.

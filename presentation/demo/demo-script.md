# Prahari Demo Script — 3 Scenarios (~5 minutes)

Spoken walkthrough for the SIH evaluation. Every control injects something into
the **simulated scene** and leaves the real pipeline to detect, track, rule on
and score it — nothing here fabricates an event. For the mechanical startup
steps and camera-by-camera tour, see [`../../docs/demo.md`](../../docs/demo.md).

## Start (before judges are watching)

```bash
.venv/Scripts/python.exe -m uvicorn prahari.core.app:app --port 9420   # terminal 1
.venv/Scripts/python.exe -m uvicorn prahari.edge.app:app  --port 8420   # terminal 2
```

Open http://127.0.0.1:8420, sign in with the password printed to terminal 2, and
**let it run ~2 minutes** so cameras leave the PROFILING state. Run the
pre-flight in [`demo-checklist.md`](demo-checklist.md) first.

## One-line framing (say this first)

> "Prahari doesn't replace border surveillance — it extracts more intelligence
> from CCTV that's *already installed*, at the edge, with evidence, and it keeps
> working when the uplink doesn't. Let me show three moments."

---

## Scenario 1 — Normal open-border movement (~90s)

**Point:** on an open border, crossing a line is not an intrusion. The system
must stay quiet for lawful traffic.

1. Open an **open-border camera** (e.g. CAM-022 approach).
2. Inject normal pedestrian and vehicle movement.
3. Narrate the pipeline: detected → tracked → normalcy/context check → **no
   unnecessary alarm**.
4. Show the Alert Governor suppression figure — the rule engine fired, the
   operator was not interrupted.

> "This is the failure mode that kills analytics deployments — the operator who
> stopped looking on night three because every lawful crossing pinged them.
> Prahari records everything and rations *alerting*."

**Screenshot:** `live-camera.png`, `alerts.png` (showing suppression).

---

## Scenario 2 — Suspicious deviation (~120s)

**Point:** the same scene, but an abnormal pattern produces a *governed* alert
with evidence.

1. Inject a person taking an off-route / wrong-direction path.
2. Watch: person detected → off-route / abnormal pattern → `SUSPICIOUS_ACTIVITY`
   → Alert Governor promotes it → evidence clip + frame + metadata.
3. Open the alert and show the **evidence package**: the clip, the still frame,
   the metadata, and the **hash-chain integrity** state.
4. Open **Patrol Deviation** to show a legitimate patrol being matched and
   suppressed — contrast with the flagged deviation.

> "An alert here isn't a red dot. It's a clip, a frame, structured metadata, and
> a tamper-evident hash chain — something an operator can act on and a reviewer
> can trust."

**Screenshots:** `patrol-deviation.png`, `evidence-integrity.png`,
`cross-camera.png`.

---

## Scenario 3 — Connectivity failure (~90s)

**Point:** this is the most persuasive moment — Prahari is not a dashboard
sitting on top of a stream.

1. With an alert pipeline active, **cut the edge→core network** (stop the core
   process / disconnect, per checklist).
2. Narrate: local inference continues → evidence queued locally → nothing is
   lost.
3. **Restore** the network.
4. Show store-and-forward synchronisation catching the core dashboard up, with
   the hash chain still intact.

> "The uplink went down. Detection kept running at the edge, evidence queued
> locally, and when the link came back it synchronised — with integrity
> preserved. That's the deployment reality on a border, not a lab."

**Screenshots:** `system-health.png` (before/after), `evidence-integrity.png`.

---

## Optional — experimental face-detector candidate (~30s, if asked)

Prahari's default face detector is the OpenCV Haar cascade. A modern **SCRFD-500M**
ONNX backend is included as an **experimental CANDIDATE** (not the release default),
enabled explicitly via `build_face_detector("scrfd")`. Show the measured
comparison from [`../../docs/benchmark-matrix.md`](../../docs/benchmark-matrix.md):

| Detector | AP@0.5 (WIDER FACE val) | Latency/img (CPU) |
|---|---|---|
| Haar (default) | 0.121 | 74.6 ms |
| SCRFD-500M (candidate) | 0.489 | 18.8 ms |

> "SCRFD is ~4× the accuracy *and* faster — but we keep it a candidate, not the
> default, until its research-only pretrained weights are cleared for deployment.
> We don't quietly ship a non-commercial weight as the release default."

Two honesty notes to say aloud: the numbers are **CPU** (ORT's CUDA provider
didn't load here, so no GPU claim), and it's the **val** split (test GT withheld).

## Close (say this last)

> "Detection, tracking and ANPR are solved problems — we didn't reinvent them.
> The contribution is the deployment architecture: measured camera capability,
> edge-first processing, governed alerts, tamper-evident evidence, and resilience
> across connectivity loss. Every number we've shown traces to a measured run in
> our benchmark matrix; the ones we haven't measured yet, we've marked as
> pending rather than claimed."

Point to [`../../docs/benchmark-matrix.md`](../../docs/benchmark-matrix.md) and
[`../../docs/limitations.md`](../../docs/limitations.md) as the honesty backstop.

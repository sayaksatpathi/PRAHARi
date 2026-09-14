# Prahari

**AI video-intelligence layer for existing CCTV infrastructure**
SIH26187 · Ministry of Home Affairs / Sashastra Seema Bal · Smart Automation

Prahari turns CCTV that is *already installed* into camera-aware, edge-processed,
evidence-backed border intelligence that keeps working when the uplink does not.

---

## What this is, and what it is not

Prahari does **not** replace CIBMS, and does not claim existing border
surveillance lacks AI. CIBMS is the broader integrated border-management
ecosystem — sensors, radars, thermal, networks, command and control. Prahari is a
narrower thing that sits underneath it:

> CIBMS integrates multiple border-surveillance technologies. Prahari focuses
> specifically on extracting additional operational intelligence from existing,
> heterogeneous CCTV, through measured camera-capability profiling, edge-first
> processing, evidence-backed events, and resilient operation across
> connectivity loss.

Object detection, tracking and ANPR are solved problems and Prahari did not
invent any of them. The contribution is the **deployment architecture**.

---

## The six things that are actually different

**1. Capability profiling is measured, not declared.**
Most systems ask an operator what a camera can do. Prahari measures: effective
resolution (as opposed to claimed), delivered frame rate, sensor noise floor,
compression damage — and then recovers the **ground plane automatically** from
the bounding boxes of people who happen to walk through the scene. No survey, no
calibration target, no operator drawing reference lines.

From that it computes pixels-on-target and issues a **Camera Capability
Certificate** against the IEC 62676-4 DORI bands. Analytics are granted **per
image region**, because a camera's near field routinely supports plate reading
while its far field barely supports noticing a person is present.

Verified against known truth: on clean ground truth the self-calibration recovers
each camera's mounting height exactly, and **against the live pipeline** — with a
noisy detector, class confusion and misclassified livestock in the samples — it
lands within **0.4–3.2 %** across the five-camera fleet. Getting there took four
separate fixes, each of which had produced a *confidently wrong* answer rather
than an obviously broken one; they are written up in
[docs/camera-profiling.md](docs/camera-profiling.md).

**2. Two doctrines, because India's borders are not one problem.**
Fenced sectors run tripwire and restricted-zone rules, where crossing the line
*is* the event. Open borders — Indo-Nepal and Indo-Bhutan are open by treaty,
with heavy lawful daily traffic — run pattern-of-life rules against a lawful
route instead, because a tripwire there fires thousands of times a day and
teaches the operator to ignore it. Selectable per camera.

**3. Alerting is rationed; recording is not.**
The failure mode that kills video analytics deployments is not inaccuracy, it is
the operator who stopped looking on night three. Prahari separates *recording an
event* (always, complete, sealed, auditable) from *raising an alert* (costs human
attention, budgeted). When events exceed the hourly budget the score threshold
rises until the rate fits, so the operator gets the most significant events
rather than the first ones. Nothing is discarded, and the suppression count and
current threshold are always on screen.

Measured effect during development: fixing duplicate suppression and zone-scoped
normalcy took a 90-second window from **1064 events to 138**, with the governor
recording 81 % of them without interrupting anyone.

**4. ANPR that refuses to guess.** Plate reading runs only where the certificate
grants it *and* only inside the image band that reaches 250 px/m. In a traced
approach the system declines to read a plainly visible, 118 px-wide plate, then
reads it three frames later once the vehicle crosses into the certified band.
Repeat-plate analysis across the camera set is the highest-value open-border
signal, and only dependable reads feed it. See [docs/anpr.md](docs/anpr.md).

**5. Evidence is sealed into a hash chain.**
Every event commits to the hash of the one before it, so altering or removing any
event breaks every link that follows. This exists specifically because of the
offline story: a node disconnected for three days is asking the sector core to
accept a backlog on trust, and the chain makes that checkable.
*Tamper-evident, not tamper-proof* — see [Limitations](#limitations).

**6. Offline is the design point, not a failure case.**
Detection never depends on the link. Metadata is pushed, video is pulled. Under
storage pressure clips are dropped, records never are. Clock drift during a long
outage is corrected at the core without overwriting what the node observed.

**7. Cameras are a corridor, not a list.**
A camera topology graph with learnt transition times links a track leaving one
camera to a track arriving at the next, so five independent cameras become one
sector with global identities. *Seen at CAM-014 heading north-east, arrived at
CAM-022 on time, never reached CAM-011* is an intelligence product; three
unrelated events are not. Matching is deliberately conservative — an arrival that
cannot be linked confidently becomes a new entity rather than inventing a journey
— and an entity that was demonstrably travelling the corridor and then goes
silent raises `CORRIDOR_DROPOUT`. Live on the node: 150 global entities, 2 linked
across cameras. See [docs/crosscam.md](docs/crosscam.md).

---

## Quick start

Requires Python 3.11+. No Docker, no Node toolchain, no build step.

```bash
python -m venv .venv --system-site-packages
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Run the sector core (optional — the edge node runs standalone without it):

```bash
.venv/Scripts/python.exe -m uvicorn prahari.core.app:app --port 9420
```

Run the edge node:

```bash
.venv/Scripts/python.exe -m uvicorn prahari.edge.app:app --port 8420
```

Open **http://127.0.0.1:8420**. The initial administrator password is printed
once to the node console on first start.

> **Windows note.** Hyper-V/WSL reserve large TCP ranges (commonly 7986–8185), and
> binding inside one fails with `WinError 10013`, which reads like a permissions
> problem. That is why the defaults are 8420/9420. Check yours with
> `netsh int ipv4 show excludedportrange protocol=tcp`.

### Verify it

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe scripts/smoke_profiling.py
.venv/Scripts/python.exe scripts/smoke_e2e.py
```

`smoke_profiling.py` checks the ground-plane self-calibration against the
simulator's known truth. `smoke_e2e.py` drives the three demonstration moments
against a running node and checks each actually happened.

### Evaluation report

```bash
python scripts/evaluate.py            # deterministic replay harness (simulator)
python scripts/evaluate.py --mot <dir>  # real footage, real GT (MOT format)
```

Writes `var/eval/evaluation.html`: intruder detection, latency, alert
suppression, profiling accuracy and per-class detection, scored against ground
truth the harness controls. It is explicit about which numbers are real and
which are plumbing checks — on the simulated fleet, detection accuracy
characterises the synthetic detector, while profiling error (0.2–2.2%) and alert
suppression (71–97%) are genuine. See [docs/evaluation.md](docs/evaluation.md).

### Ingesting real RTSP

```bash
pip install imageio-ffmpeg
python scripts/make_footage.py        # render recordings
python scripts/serve_rtsp.py          # serve them as RTSP via MediaMTX
python scripts/use_rtsp_cameras.py --only CAM-011,CAM-031
```

Frames then arrive over the wire through the same `StreamSource` that reads a
real camera. **Detection needs real footage as well as a real model** — see
[docs/rtsp.md](docs/rtsp.md), which explains why and how the system says so.

---

## The demonstration

Three moments, in the **Demonstration** tab. Every control injects something into
the simulated scene and then leaves the real pipeline to find it — nothing
fabricates an event.

**1. Cameras are not alike.** Open **Camera Capability**. Five cameras, five
different measured profiles. The gate camera is granted ANPR in the lower quarter
of its frame only; the 62° perimeter dome is refused it outright; the night approach
camera is refused it at 93 px/m against the 250 px/m the standard requires. Every
refusal states its measured reason.

**2. Intrusion to evidence.** *Approach & cross the line* on CAM-014. A subject
walks to the fence, crosses it, is detected, tracked, ruled on, scored with a
visible derivation, and gets an evidence clip that includes the seconds *before*
the trigger. Open the alert to see the score broken into named factors.

**3. The link dies.** *Cut the uplink*. Detection keeps running, events queue
locally with their evidence, the queue grows on screen. *Restore the uplink* and
the backlog drains with original timestamps preserved and the hash chain intact.

Also worth showing: **Evidence Integrity → Verify now**, and the sensor-attack
controls (cover the lens, blind it, replay a frozen feed).

---

## Architecture

```
existing CCTV ──RTSP/ONVIF──▶ ┌─────────────── prahari-edge (at the BOP) ──────────────┐
                              │                                                        │
                              │  capability profiling ──▶ Camera Capability Certificate│
                              │          │                                             │
                              │          ▼ (gates what may run, and where in frame)    │
                              │  detection ──▶ tracking ──▶ rule engine ──▶ scoring    │
                              │                                    │                   │
                              │                          ┌─────────┴────────┐          │
                              │                          ▼                  ▼          │
                              │                    alert governor      evidence +      │
                              │                   (rations attention)  hash chain      │
                              │                                            │           │
                              │  SQLite + local disk ◀─────────────────────┘           │
                              └────────────────────────┬───────────────────────────────┘
                                                       │ metadata pushed, video pulled
                                                       ▼
                                              prahari-core (sector)
```

Two deployables, and **the sync contract between them is the product**. The edge
owns cameras, inference, evidence and the queue, and runs fully standalone. The
core aggregates, corrects clock drift, verifies chains, and touches no camera.

| Layer | Choice | Why |
|---|---|---|
| ANPR | fast-alpr (MIT), ONNX | Plate detection and OCR both in ONNX, so it adds a model rather than a framework — no torch |
| Inference | ONNX Runtime | ~50 MB vs ~2.5 GB; one code path for CPU and CUDA; avoids the AGPL-3.0 licence attached to Ultralytics YOLO, which is a real procurement consideration for a government deployment |
| Edge storage | SQLite (WAL, `synchronous=FULL`) | Survives power loss with no server process to babysit — which is what an unattended outpost node needs |
| Message bus | In-process, NATS-shaped subjects | A broker is a process to install and fail, for no gain at edge scale. Subjects and schemas match JetStream, so the transport is one file to swap |
| Frontend | Zero-build vanilla JS | A node that needs npm to render its own interface cannot be recovered from a USB stick |
| Map | Hand-drawn SVG | A tile-serving map needs the internet; this console must render without it |

---

## API

Interactive documentation at `/docs` on a running node.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | Obtain a bearer token |
| GET | `/api/system/status` | Node status, link posture, sync, alerting |
| GET | `/api/system/bandwidth` | Measured uplink accounting |
| GET | `/api/system/ledger/verify` | Walk the evidence hash chain |
| GET | `/api/cameras` | Cameras with live runtime state |
| GET | `/api/cameras/{id}/certificate` | Measured capability certificate |
| POST | `/api/cameras/{id}/test` | Probe a real stream (reports CONNECTED only after a genuine frame read) |
| POST | `/api/cameras/{id}/profile` | Discard the certificate and re-measure |
| GET | `/api/events` | Event log, filterable |
| GET | `/api/events/{id}/evidence/{frame\|thumb\|clip}` | Evidence retrieval |
| POST | `/api/alerts/{id}/acknowledge` | Acknowledge, with true-positive / false-alarm feedback |
| GET | `/api/anpr/plates` | Plate reads and repeat-entity history |
| POST | `/api/cameras/{id}/test` | Probe a real RTSP stream |
| — | `scripts/evaluate.py` | Deterministic evaluation harness → HTML report |
| — | `scripts/benchmark_segment.py` | Segmentation latency / FPS / VRAM benchmark |
| POST | `/api/demo/action` | Drive the demonstration |
| WS | `/ws` | Live detections, events, status |

Roles: `admin` (configure), `operator` (acknowledge, give feedback), `viewer`.

---

## Configuration

Copy `.env.example` to `.env`. Nothing operational is hard-coded; the thresholds
that matter most:

```
PRAHARI_LOITERING_THRESHOLD_SECONDS=30
PRAHARI_EVENT_COOLDOWN_SECONDS=30     # one crossing = one event
PRAHARI_ALERT_BUDGET_PER_HOUR=20      # operator attention budget
PRAHARI_SEGMENT_BACKEND=auto          # event-triggered segmentation: auto|sam_onnx|grabcut|off
PRAHARI_EVENT_CLIP_BEFORE_SECONDS=3   # pre-roll: the approach, not just the trigger
PRAHARI_QUEUE_MAX_BYTES=2147483648    # before clip eviction begins
PRAHARI_INFERENCE_INTERVAL=2          # detect every Nth frame; track every frame
```

### Using real cameras

Set `source_kind` to `rtsp` and point `stream_url` at the camera or, better, at
the **existing NVR's re-stream**. Two field notes that matter:

- Most installed IP cameras cap simultaneous RTSP clients, and on a site with a
  recorder the budget is often already spent. Pulling the recorder's re-stream
  avoids fighting the existing system for the camera.
- Prefer the **sub-stream**. Analytics rarely need full resolution, and the
  decode saving is large.

RTSP is forced over TCP: UDP is the default and it is the wrong default here,
because a lossy backhaul produces torn frames that look like motion to a detector
and generate false events all night.

### Using a real model

Drop a YOLO-style ONNX export at `models/yolo.onnx` — see `models/README.md`.
With no model present the node falls back to a clearly-labelled synthetic
detector and badges it in the dashboard. For CUDA: `pip install onnxruntime-gpu`.

---

## Limitations

Stated plainly, because a system for this domain that hides them is worse than
one that has them.

- **The detector is simulated unless you install a model.** The fallback models
  miss rate, class confusion and noise-driven false positives so the pipeline is
  exercised honestly, but its confidences are synthetic and carry no accuracy
  claim.
- **No border-domain validation has been done.** Nothing here has been measured
  against real border imagery at night, at range, in fog or rain. COCO metrics say
  nothing about that, and no public dataset represents Indian border CCTV
  conditions. Field validation is required before any operational claim.
- **The Event Priority Score is a ranking aid, not a probability.** It is not
  calibrated against ground truth and must not be read as a likelihood of
  intrusion.
- **Tamper-evident, not tamper-proof.** Anyone holding the node's key material
  could forge a consistent chain. Hardware-backed keys and countersigning at the
  core are designed for and not built.
- **Replay detection catches the crude case only.** Byte-identical frames are
  caught; a competent attacker looping a long genuine recording is not. That needs
  a challenge the camera cannot precompute.
- **Ground-plane calibration assumes 1.7 m mean stature.** Errors there scale
  metric output linearly, which is why speeds are reported as estimates.
- **The MJPEG preview endpoint is unauthenticated.** Browsers cannot attach an
  Authorization header to an `<img>`, and tokens in query strings would write
  credentials into every access log. Short-lived signed stream URLs are the fix.
- **The real ANPR backend is not validated.** fast-alpr loads and runs, but a
  model trained on photographs finds nothing in synthetic imagery, so its
  accuracy is demonstrated by nothing in this repository. Plates shown in the
  demo come from the synthetic reader and are labelled as such.
- **Face recognition is deliberately absent.** Our own profiling shows almost no
  perimeter camera meets the pixels-on-target threshold for identification.
  Shipping it would be dishonest, and it carries legal and privacy requirements a
  video analytics layer cannot satisfy alone.
- **Docker files are untested.** Docker was not available on the development
  machine; the native path above is the supported one.

---

## Roadmap

The gap between this and something deployable, in priority order:

1. **Real-footage validation.** The evaluation harness and a real-footage
   adapter are built and tested: `prahari.eval.mot` ingests MOTChallenge
   sequences with per-frame ground truth, and `scripts/evaluate.py --mot` scores
   a real model's recall/precision against real annotations
   ([docs/evaluation.md](docs/evaluation.md)). What remains is running the
   multi-GB download on a normal connection — the build machine sustained only
   ~0.1 MB/s, so the real numbers must be produced elsewhere. Everything
   downstream of the download is done. VIRAT is set up but gated behind a signed
   Data Protection Agreement, which is the deploying party's to accept.
2. **Cross-camera re-identification.** Track handoff, global entities and
   corridor-dropout detection are built and running
   ([docs/crosscam.md](docs/crosscam.md)); the appearance cue is an HSV colour
   signature, which is genuinely weak between cameras with very different optics.
   A proper re-ID model is the honest fix, and topology is currently seeded
   rather than discovered from observed co-occurrence.
3. **Friendly-force suppression.** SSB patrols walk the same routes and trip every
   rule. Patrol-schedule ingestion is the single biggest remaining false-alarm
   source.
4. **SAM 2 GPU validation.** The event-triggered segmentation refinement is
   built and running (GrabCut backend, verified live; SAM 2 ONNX backend written
   behind the same interface). What remains is validating the SAM backend against
   real weights on a GPU — deferred for the same connection reason as MOT17. See
   [docs/segmentation.md](docs/segmentation.md).
5. **Thermal-specific models.** Real border night capability is thermal, and an
   RGB model on a thermal feed is a compromise.
6. **Replay/evaluation harness** producing precision, recall and false-alarm rate
   against annotated footage, so accuracy questions get numbers rather than
   adjectives.

---

## Layout

```
prahari/
├── prahari/
│   ├── common/        models, config, SQLite, bus, geometry
│   ├── edge/          the node: profiling, detection, tracking, rules,
│   │                  scoring, alerting, evidence, sync, API
│   └── core/          sector aggregator
├── web/               zero-build dashboard
├── scripts/           smoke tests and verification
├── tests/             101 unit tests
└── models/            ONNX models (not committed)
```

## Licence and attribution

See `LICENCE`. Third-party components are listed in `docs/attribution.md` with
their licences. No code was copied from existing NVR projects; where an approach
is borrowed (ByteTrack's two-stage association, IEC 62676-4 DORI bands) it is
cited in the module that uses it.

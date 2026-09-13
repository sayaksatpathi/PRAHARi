# Running the demonstration

A 4–5 minute walkthrough built around three moments. Nothing here fabricates an
event: every control injects something into the **simulated scene** and leaves
the real pipeline to detect, track, rule on and score it exactly as it would a
real camera's imagery.

## Before you start

```bash
.venv/Scripts/python.exe -m uvicorn prahari.core.app:app --port 9420   # terminal 1
.venv/Scripts/python.exe -m uvicorn prahari.edge.app:app  --port 8420   # terminal 2
```

Open http://127.0.0.1:8420 and sign in with the password printed once to
terminal 2.

**Give it about two minutes before presenting.** Cameras start in a PROFILING
state and refuse to certify themselves until they have observed enough
pedestrians to fit their ground plane. That wait is the feature, not a delay to
apologise for — but it does mean a cold start has nothing to show.

---

## Moment 1 — these cameras are not alike (90 seconds)

Open **Camera Capability**.

Five cameras, five measured profiles. Walk the panel for **CAM-011 (gate)** and
**CAM-014 (perimeter dome)** side by side:

- CAM-011 reaches DORI **identify** and is granted **ANPR** — but read its
  reason: *"250 px/m reached below image row 564 — 22% of the frame qualifies."*
  The grant is spatial. Plate reading is permitted in the lower fifth of the
  frame and nowhere else.
- CAM-014 is a 62° perimeter dome. It is refused ANPR outright.
- **CAM-022** (night approach) is refused it for a different reason: *"needs
  250 px/m; this camera peaks at 93 px/m."* Not policy — arithmetic.

Then scroll to any camera's **Refused** column and point at
**face_recognition**:

> *Face recognition is never granted automatically by Prahari. It is withheld as
> a matter of product policy, not capability.*

Say plainly: we excluded it because our own profiling shows almost no perimeter
camera meets the pixels-on-target threshold for identification, so claiming it
would be dishonest — and it carries legal and privacy obligations a video
analytics layer cannot satisfy alone.

Finish on the **self-calibrated geometry** block. Nobody surveyed these cameras
or held up a calibration target. The horizon row and the metre scale were
recovered from the bounding boxes of people who happened to walk past.

---

## Moment 2 — intrusion to evidence (90 seconds)

Go to **Demonstration**, select **CAM-014**, press **Approach & cross the line**.

Switch to **Live Cameras** and watch CAM-014. A subject walks in from beyond the
fence. Bounding box, track id, trajectory trail, and the border line drawn across
the frame are all rendered by the node, not the browser.

When it crosses, go to **Alerts** and open the event. In the modal:

- **The clip includes the seconds before the trigger.** By the time a tripwire
  fires, the interesting part — the approach — has already happened. A rolling
  buffer is why the evidence contains it.
- **The score is broken into named factors** with weights. Point at the caption:
  this is a ranking aid, not a calibrated probability, and it says so on screen.
- **Integrity panel**: ledger position, entry hash, frame SHA-256, clip SHA-256.

Then press **Confirm — genuine** or **False alarm**, and say what that does: the
feedback feeds the per-camera prior in the scorer, so a camera that keeps crying
wolf is damped automatically rather than the operator being asked to tolerate it.

---

## Moment 3 — the link dies (90 seconds)

This is the one to do slowly, because it is the differentiator.

Back on **Demonstration**, press **Cut the uplink**.

Point at the top bar: link posture flips to **OFFLINE**. Then point at the camera
grid — still running, still detecting, frame rates unchanged. Say it explicitly:
*losing the link degrades reporting, never sensing.*

Trigger another intrusion while offline. Watch **queued** climb in the top bar.

Wait ten or fifteen seconds, then press **Restore the uplink**. Posture moves to
**SYNCING**, the queue drains, and the events that were captured during the
outage appear at the core with their **original timestamps**.

Close on **Evidence Integrity → Verify now**: the whole chain walks and reports
intact. Then explain why that matters here specifically — a node that has been
disconnected for three days is asking the sector core to accept a backlog on
trust, and the chain is what turns that into something checkable.

---

## Optional, if there is time or a sceptical question

**Sensor attack.** On Demonstration, press **Cover the lens** on any camera. A
`camera_tamper` event is raised within a couple of seconds. An object detector
reports "no objects" on a painted-over camera with complete confidence; integrity
is monitored on its own signals. Try **Replay a frozen feed** too — byte-identical
consecutive frames cannot occur on a live sensor, because real sensor noise
guarantees otherwise.

**Alert governance.** On **Alerts**, tick *Include events recorded without
alerting*. The list grows substantially. Explain the split: everything is
recorded, sealed and synchronised; only some things interrupt a human. The
suppression count and the current threshold are both on screen, because an
operator who cannot see that the system is rationing will assume it is broken.

**Bandwidth.** On the Dashboard, the uplink panel shows imagery processed against
bytes actually transmitted. In development runs this sits above 99% reduction —
and the caption states the basis, which is this node's own encoded frames during
this run, not a projection.

---

## What is simulated, and what is not

Stated up front in any presentation, because a judge who discovers it themselves
will discount everything else.

| Simulated | Real |
|---|---|
| The camera imagery (synthetic scene renderer) | Camera profiling and all its measurements |
| Detections, if no ONNX model is installed | Ground-plane self-calibration |
| The injected intruder / vehicle / group | Tracking, rules, scoring, dedupe |
| Network outage (a flag, not an unplugged cable) | The offline queue, sync, and drain |
| | Evidence capture, hashing, chain verification |
| | Alert governance and operator feedback |

The detector fallback badges itself in the dashboard top bar as **SIMULATED
DETECTOR** and cannot be mistaken for a real model. Install one at
`models/yolo.onnx` and the badge changes to the model name and device.

If you want a stronger claim, run against real RTSP instead — see the
[MediaMTX](https://github.com/bluenviron/mediamtx) note in the roadmap. The
source layer is already an adapter; a real stream is a configuration change, not
a code change.

## Determinism

The simulator is fully seeded (`PRAHARI_DEMO_SEED`). The same seed produces the
same scene every run, so a rehearsed demo behaves the same on the day. Detection
noise, miss rate and class confusion are seeded too.

## If something goes wrong on the day

- **Cameras stuck in PROFILING** — they have not seen enough pedestrians yet.
  Wait, or press **Re-profile** and give it a minute. The progress line tells you
  how many more observations it needs.
- **No events at all** — check the top bar says cameras are online. If a camera
  shows TAMPERED, clear it from the Demonstration tab.
- **Port refuses to bind on Windows** — Hyper-V reserves ranges; see the README.
- **Nothing loads** — the dashboard is plain static files; a hard refresh
  (Ctrl+Shift+R) clears a stale cached script.

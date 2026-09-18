# Prahari Demo Checklist

Run this before presenting. A cold start has nothing to show — cameras begin in
PROFILING and refuse to certify until they've observed enough pedestrians.

## Pre-flight (T-10 minutes)

- [ ] `git status` clean; on the intended commit/tag.
- [ ] `.venv` active; `pip check` passes.
- [ ] Models present in `models/` (detector ONNX, Re-ID checkpoint).
- [ ] Demo clips present under `data/demo/` (see [`../../data/README.md`](../../data/README.md)).
- [ ] Core up: `uvicorn prahari.core.app:app --port 9420`.
- [ ] Edge up: `uvicorn prahari.edge.app:app --port 8420`.
- [ ] Signed in at http://127.0.0.1:8420 (password printed once to the edge terminal).
- [ ] Waited **~2 minutes** — cameras have left PROFILING and show live profiles.
- [ ] Camera Capability panel shows certified profiles for the cameras you'll demo.
- [ ] Screen resolution / zoom set so the dashboard is legible on the projector.
- [ ] Notifications / other windows silenced.

## Scenario rehearsal

- [ ] Scenario 1 (normal open-border) produces **no** operator interruption.
- [ ] Scenario 2 (deviation) produces a `SUSPICIOUS_ACTIVITY` alert with a clip,
      frame, metadata and intact hash chain.
- [ ] Patrol Deviation panel shows a matched patrol being suppressed.
- [ ] Scenario 3: you know exactly **how you cut the network** and how you restore
      it, and store-and-forward catches up cleanly.

## How to cut / restore connectivity (Scenario 3)

- [ ] Decide the cut method in advance (stop the core process, or block the port).
- [ ] Confirm edge keeps inferring after the cut (local queue grows).
- [ ] Confirm on restore the queue drains and the dashboard catches up.
- [ ] Confirm `scripts/verify_evidence.py` still validates the hash chain after sync.

## Failure recovery (if something breaks live)

- [ ] Camera stuck in PROFILING → give it more pedestrian traffic, or switch to a
      pre-warmed camera.
- [ ] No alert firing → check the camera's doctrine (fenced vs open-border) and zone config.
- [ ] Dashboard blank → hard refresh; confirm both uvicorn processes are alive.
- [ ] Total failure → fall back to the recorded screen capture (keep one ready).

## Post-demo

- [ ] Every quantitative claim you made maps to a MEASURED row in
      [`../../docs/benchmark-matrix.md`](../../docs/benchmark-matrix.md).
- [ ] You named at least one limitation from [`../../docs/limitations.md`](../../docs/limitations.md).

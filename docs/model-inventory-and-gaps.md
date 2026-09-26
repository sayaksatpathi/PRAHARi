# Prahari — Model Inventory & Production Gaps (per component)

*As of 2026-09-26.* For every component: the model/approach it runs **today**, its
**measured** number where one exists, and exactly **what it lacks to be
production-grade**. Grounded in the code and this cycle's measurements; nothing
here is aspirational unless labelled a gap.

> Legend for maturity: **PROD** (deployable now) · **DEMO** (works, not field-
> validated) · **CANDIDATE** (gated/unpromoted) · **RULE/GEOM/CRYPTO** (not an ML
> model).

## Summary table

| # | Component | Current model / approach | Measured | Maturity | Biggest production gap |
|---|-----------|--------------------------|----------|----------|------------------------|
| 1 | Object detection | YOLOX-S ONNX (`yolox_s.onnx`, 34 MB); accuracy cfg YOLOv8m@1280 (99 MB); synthetic fallback | MOT17 MOTA 0.397/IDF1 0.508 (YOLOX-S); 0.435/0.560 (v8m@1280); GPU 104 fps / 13 fps | DEMO | No border-domain training (night/range/fog); real-time proven on laptop GPU only |
| 2 | Multi-object tracking | ByteTrack (own impl), IoU + class-vote | folded into MOT17 IDF1 above | PROD-ish | IoU-only association → ID switches under occlusion; no motion/appearance model in-tracker |
| 3 | Cross-camera Re-ID | **OSNet x1_0** (`reid_osnet_market.onnx`, 8.3 MB, 512-d); HSV fallback | Market-1501 Rank-1 **0.947** / mAP 0.845 | PROD | Trained on Chinese campus, not border cams; no visible↔thermal; appearance drift |
| 4 | Face detection | YuNet 2023mar (`face_yunet_2023mar.onnx`, 0.2 MB); Haar fallback; SCRFD gated | WIDER FACE val AP **0.626** (YuNet) vs 0.121 Haar | PROD (detection) | Detection only; no recognition (by design); no thermal face |
| 5 | ANPR | fast-alpr (YOLO-v9-t plate det + global-plates MobileViT OCR); Awiros Indian specialist; synthetic for sim | real reads (`555ZBF` 0.95); Awiros 4/4 on Indian plates | DEMO | Indian model not wired as pipeline default; no field validation on border-cam plates |
| 6 | Segmentation | **SAM 2** hiera-tiny ONNX (encoder 128 MB + decoder 20 MB), event-triggered; GrabCut fallback | 25/25 masks IoU 0.975; real footage ~0.36 area, ~150 ms/mask GPU | DEMO | VRAM/throughput on target edge accelerator; event-triggered only |
| 7 | Camera capability profiling | Geometric (effective res/fps/noise + ground-plane from person bboxes), IEC 62676-4 DORI | mounting-height error 0.2–3.2 % | GEOM | Field calibration validation; needs walking people; assumes 1.7 m stature |
| 8 | Normalcy / pattern-of-life | Statistical per camera/zone/hour counts + decay | Street Scene protocol (UCSD Ped2) AUC 0.907 / RBDC 0.648 / TBDC 0.612 | DEMO | Needs weeks of real per-camera baseline; no learned/trajectory anomaly model |
| 9 | Patrol suppression | Rule-based (schedule × zone × direction × pace) | fleet 71–97 % suppression (sim) | RULE | No duty-roster feed; needs Re-ID to split person-with-patrol from patrol |
| 10 | Alert governor | Rule-based rate budget + adaptive threshold | 1064→138 events in a 90 s window (sim) | RULE | No learned operator-preference model; per-operator tuning |
| 11 | Rule engine (zones/tripwire/loiter/group/night/wrong-dir) | Deterministic geometry + thresholds | 7/7 border scenarios (sim) | RULE | Thresholds need field survey; no learned rules |
| 12 | Evidence integrity | SHA-256 append-only hash chain; Fernet credential encryption; §63(4) cert generator | chain verify PASS; cert on 796 records | CRYPTO | Tamper-**evident**, not tamper-**proof**: no hardware keys / core countersign; weak replay detection |
| 13 | Cross-camera topology / corridor | Graph, learnt transition times, **seeded** topology | 150 entities, 2 linked (live node) | DEMO | Topology seeded, not discovered from co-occurrence; still needs OSNet cue wired into handoff |
| 14 | Tamper / sensor-attack detection | CV heuristics (lens cover, blinding, frozen frame) | detects the crude cases | RULE | Byte-identical replay only; not robust to a looped genuine recording |
| 15 | Thermal / night IR | **None** — RGB model on thermal | generic thermal AP@0.5 0.29 (P 0.88 / R 0.27) | GAP | No thermal-specific model; no Indian thermal data |

---

## Per-component detail

### 1. Object detection — DEMO
- **Now:** ONNX Runtime (CPU+CUDA). Production weights YOLOX-S (`models/yolox_s.onnx`);
  accuracy config YOLOv8m@1280 (`yolov8m_1280.onnx`); speed option YOLOX-Tiny; when no
  model is installed it falls back to a **clearly-labelled synthetic detector**.
- **Measured:** MOT17-02/04 — YOLOX-S MOTA 0.397 / IDF1 0.508; YOLOv8m@1280 0.435 /
  0.560. GPU throughput (ORT-CUDA, RTX 4050): YOLOX-S ~104 fps, YOLOv8m@1280 ~13 fps.
  Real thermal AP@0.5 0.29; aerial/range AP@0.5 0.30.
- **Lacks for production:** trained only on COCO/MOT17 (daylight street) — **no
  border-domain fine-tune** (night, range, fog, fog-lensed domes); real-time proven
  on a **laptop** GPU, not the target edge accelerator (Jetson Orin) with the full
  pipeline; no in-field model-update/retraining pipeline; class set is COCO-derived
  (cattle mapped from livestock classes, not an India-specific taxonomy).

### 2. Multi-object tracking — PROD-ish
- **Now:** own ByteTrack (`bytetrack.py`) — two-stage IoU association + per-track
  class voting. `track_match_iou=0.30`, `track_timeout=5 s`.
- **Lacks:** association is **IoU/geometry only** (no embedded appearance or Kalman
  motion model), so identity switches under occlusion/crossing; no camera-motion
  compensation (fine for fixed CCTV, matters if a camera is bumped/PTZ).

### 3. Cross-camera Re-ID — PROD (this cycle)
- **Now:** OSNet x1_0 (torchreid Market-1501 checkpoint) exported to ONNX, 512-d,
  256×128, ImageNet-norm; **now the pipeline default** (falls back to ResNet-18 then
  HSV). 
- **Measured:** Market-1501 Rank-1 **0.947** / mAP 0.845 (ONNX-verified 0.948).
- **Lacks:** trained on Market-1501 (Chinese campus) — **no border-camera domain
  adaptation**; no **visible↔thermal** cross-modal model (day-cam → night-thermal-cam
  matching is unsolved here); appearance drift across lighting/time; gallery
  management not stress-tested at fleet scale; topology handoff still needs this cue
  wired in (item 13).

### 4. Face detection — PROD (detection only)
- **Now:** YuNet 2023mar via `cv2.FaceDetectorYN` (default, Apache/MIT); Haar
  fallback; SCRFD-500M is a **gated candidate** (research-only weights, hard-blocked
  from deployment).
- **Measured:** WIDER FACE val VOC AP@0.5 — YuNet 0.626, Haar 0.121, SCRFD 0.489.
- **Lacks:** this is **detection, not recognition** — face *recognition* is
  deliberately absent (perimeter cams rarely meet pixels-on-target, plus legal/
  privacy); no thermal-face capability (KKWETC harness ready, dataset gated).

### 5. ANPR — DEMO
- **Now:** fast-alpr (MIT) — YOLO-v9-t-384 plate detector + `global-plates-mobile-vit-v2`
  OCR — for **real** sources; **Awiros Indian specialist** (Apache-2.0) validated
  separately; synthetic reader for simulated frames; capability-gated per camera
  region + a min plate-width (px/m) gate.
- **Measured:** real reads on real CCTV (`555ZBF` @ 0.95, 36 unique on a gate clip);
  Awiros 4/4 exact on Indian plates with format post-processing.
- **Lacks:** the **Indian-specialist model is not the wired pipeline default** —
  fast-alpr's global OCR is (Awiros should be wired for an Indian deployment); **no
  field validation** on real border-camera plate captures (oblique angle, night, IR,
  speed blur); Indian multi-line / state-format handling and temporal-aggregation
  tuning need field data.

### 6. Segmentation (event-triggered) — DEMO
- **Now:** SAM 2 (sam2_hiera_tiny) ONNX encoder (128 MB) + decoder (20 MB), run
  **only on high-priority events** to refine the score and seal a mask into evidence;
  GrabCut CPU fallback; `segment_backend=auto`.
- **Measured:** 25/25 masks, IoU 0.975 vs reference; on real footage ~0.36 mask-area,
  ~150 ms/mask on GPU.
- **Lacks:** VRAM/latency/FPS on the **target** edge accelerator idle (measured on a
  laptop GPU, historically under contention); it is event-triggered by design, not a
  per-frame segmenter.

### 7. Camera capability profiling — GEOM (measured, not ML)
- **Now:** measures effective resolution vs claimed, delivered fps, sensor noise,
  compression damage; recovers the **ground plane from the bounding boxes of people
  who walk through**; issues a Camera Capability Certificate against IEC 62676-4 DORI;
  grants analytics **per image region**.
- **Measured:** mounting-height error 0.2–3.2 % across a 5-camera fleet.
- **Lacks:** validation against **surveyed** ground truth at real BOPs; needs enough
  walking people to fit the plane (sparse scenes profile slowly); assumes **1.7 m mean
  stature** (errors scale metric output linearly).

### 8. Normalcy / pattern-of-life — DEMO (statistical, not NN)
- **Now:** per-camera / per-zone / per-hour-of-week observation counts with decay;
  ratio of this-hour vs average-hour + a recent-rate check flags "unusual".
- **Measured:** Street Scene **protocol** on UCSD Ped2 — frame-AUC 0.907, RBDC 0.648,
  TBDC 0.612 (object-centric detector); normalcy false-alert load 34→0 (sim).
- **Lacks:** a **learned** scene-anomaly model; **weeks of real per-camera traffic**
  for a genuine baseline (currently short/seeded); trajectory/behaviour-level anomaly
  (only spatial-temporal counts today).

### 9–11. Reasoning: patrol suppression / alert governor / rule engine — RULE
- **Now:** deterministic. Patrol = schedule × zone × direction × pace gate; governor =
  hourly attention budget with an adaptive score threshold (records everything,
  rations alerts); rule engine = tripwire / restricted-zone / loiter / group / night /
  wrong-direction.
- **Measured (sim):** patrol 71–97 % suppression; governor 1064→138 in 90 s; 7/7
  border scenarios correct.
- **Lacks:** **duty-roster feed** for patrols (profiles entered by hand today); Re-ID
  to separate a person moving *with* a patrol from the patrol; thresholds/zones need a
  **field survey**; governor has no learned per-operator preference model.

### 12. Evidence integrity — CRYPTO (strong, but tamper-evident only)
- **Now:** append-only **SHA-256 hash chain** (each entry commits to the prior);
  **Fernet-encrypted** camera credentials; independent verify (`verify_evidence.py`,
  dashboard "Verify now"); **BSA §63(4)/§65B(4) certificate generator**.
- **Lacks:** **tamper-evident, not tamper-proof** — a holder of the node key could
  forge a consistent chain; needs **hardware-backed keys (TPM/HSM)** + **core
  countersignature**; replay detection catches only byte-identical frames; MJPEG
  preview endpoint is unauthenticated (documented); HTTPS/mTLS and signed OTA updates
  are P0 infra items (see [operational-gap-audit.md](operational-gap-audit.md)).

### 13. Cross-camera topology / corridor — DEMO
- **Now:** camera graph with learnt transition times; conservative handoff (unmatched
  arrival → new entity); `CORRIDOR_DROPOUT` when a travelling entity goes silent.
- **Lacks:** topology is **seeded, not discovered** from observed co-occurrence; the
  appearance cue for handoff should now use OSNet (item 3) instead of the weak HSV
  signature; multi-machine at scale unproven (single-host demo).

### 14. Tamper / sensor-attack — RULE
- **Now:** detects lens cover, blinding by light, and a frozen/replayed feed
  (byte-identical) with CV heuristics.
- **Lacks:** robust replay detection (a competent attacker looping a long genuine
  recording is not caught — needs a challenge the camera cannot precompute);
  adversarial robustness.

### 15. Thermal / night-IR — GAP
- **Now:** **no thermal-specific model** — the RGB detector is run on thermal as a
  measured lower bound (AP@0.5 0.29, precision 0.88, recall 0.27).
- **Lacks:** a **thermal-trained** detector and **Indian thermal data**; visible↔
  thermal Re-ID (harness ready, RegDB/SYSU gated). This is the real border-night gap.

---

## Cross-cutting production gaps (apply to the whole system)

1. **Real border-domain validation** — nothing measured on real Indian border CCTV
   (night/range/fog). #1 gap; harness ready ([field-validation-kit.md](field-validation-kit.md)).
2. **Target-accelerator benchmarking** — all GPU numbers are on an RTX 4050 laptop;
   Jetson Orin (or the chosen edge box) + full-pipeline multi-stream must be measured
   before cameras-per-node / cost is final ([bill-of-materials.md](bill-of-materials.md)).
3. **Model-update / MLOps pipeline** — no field mechanism to push, verify (manifest is
   built), roll back, and A/B new weights across a fleet.
4. **Security hardening (P0)** — HTTPS/mTLS, authenticated WebSocket, signed OTA
   updates, hardware-backed evidence keys ([operational-gap-audit.md](operational-gap-audit.md)).
5. **Fleet management** — node enrollment, health, config versioning across many BOPs.
6. **Operator acceptance at scale** — protocol executed (9/9 walkthrough) but the
   statistical SUS study needs real operators ([operator-testing.md](operator-testing.md)).
7. **Sensor fusion** — radar/PIDS/thermal have a defined hook, not an implementation
   ([sensor-fusion.md](sensor-fusion.md)).
8. **Legal/forensic sign-off** — admissibility self-assessment + certificate built;
   counsel opinion pending ([evidence-admissibility.md](evidence-admissibility.md)).

## One-line honest positioning
The **perception stack** (detection, tracking, Re-ID, face-detect, ANPR, SAM) runs
on real, permissively-licensed models with real measured numbers, and the
**architecture** (capability profiling, rationed alerting, evidence chain, offline
sync, cross-camera corridor) is the contribution. The gap to production is **domain
data + target-hardware validation + security/MLOps hardening**, not missing
capability — every component exists and runs; most need field-grade data and a
hardened deployment substrate rather than new algorithms.

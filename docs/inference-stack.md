# The inference stack, and the measurements behind it

This page records what Prahari runs, and *why that and not something else*. Every
choice below points at a number produced by a script in this repository, and the
numbers that are missing are named as missing rather than filled with estimates.

## Status

| Component | Choice | Status |
|---|---|---|
| Detector | **YOLOX-Tiny @ 640** | **candidate**, chosen on measured accuracy-per-compute |
| Tracker | ByteTrack (re-implemented) | in use |
| Capability gating | measured certificate, IEC 62676-4 DORI | in use |
| Segmentation | SAM 2 (ONNX), GrabCut fallback | SAM validated; GPU cost not yet characterised |
| ANPR | fast-alpr, gate cameras only | capability- and region-gated |

"Candidate" is deliberate: the detector choice rests on CPU measurements, and the
CUDA rows that might change it are outstanding. Nothing is locked until they are
in.

## Detector: the controlled comparison

`scripts/benchmark_detectors.py` on MOT17, two sequences, 1,650 frames. Harness,
sequences, tracker, thresholds and scoring identical across rows; only the model,
its input resolution and the execution provider change.

| | Configuration | P | R | F1 | MOTA | MOTP | IDF1 | IDsw | MT | ML | FPS | Latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | YOLOX-Tiny @416 | 0.801 | 0.323 | 0.460 | 0.255 | 0.804 | 0.384 | 19 | 14 | 79 | 35.8 | 27.9 ms |
| **B** | **YOLOX-Tiny @640** | 0.755 | **0.557** | **0.641** | **0.412** | 0.805 | 0.494 | 56 | 27 | 50 | 14.3 | 70.2 ms |
| C | YOLOX-S @640 | 0.768 | 0.509 | 0.612 | 0.403 | **0.825** | **0.500** | **29** | 23 | 57 | 9.4 | 106.1 ms |

CPU only. Row A reproduces the separately-committed evaluation run exactly
(0.801 / 0.323 / 0.255 / 0.384), which is what makes the three rows comparable.

### Why B and not C

B exists in this table to separate two effects that a two-row comparison would
confound. MOT17 is 1920×1080, so a 60-pixel pedestrian arrives at the network as
roughly 13 pixels at a 416 input. Resolution alone is therefore a plausible
explanation for the entire A→C difference, and it turns out to be very nearly the
whole of it:

- **Resolution** (A→B, identical weights): recall **+72% relative**, MOTA +0.157,
  IDF1 +0.110, mostly-lost tracks 79 → 50. Cost: 2.5×.
- **Capacity** (B→C, identical input): recall **−9%**, MOTA slightly down. Cost: a
  further 1.5×.

YOLOX-S does not find more people than YOLOX-Tiny at the same input resolution.
It costs 52% more compute to detect fewer of them.

What S *does* buy is worth stating plainly, because it is not nothing: **tighter
boxes** (MOTP 0.805 → 0.825) and **half the identity switches** (56 → 29). Those
are exactly the properties the identity-dependent features rely on — dwell time
for loitering, trajectory for patrol conformance, continuity for cross-camera
handoff. If GPU headroom makes the timing difference irrelevant, C becomes
arguable on tracking stability alone, and the CUDA rows are what will settle it.

### What this means beyond the detector

This is the pixels-on-target argument, measured rather than asserted. Prahari
gates analytics on measured px/m through the capability certificate
([camera-profiling.md](camera-profiling.md)); the same effect appears *inside* the
detector. What limits recall on 1080p footage is how many pixels reach the
network, not how large the network is.

The practical consequence for a border deployment is the opposite of the usual
instinct. Faced with poor detection, the reflex is a bigger model. These numbers
say to look first at how much of the sensor's resolution is actually reaching
inference — and, upstream of that, whether the camera resolves the target at all
at that range.

### Running YOLOX-Tiny at 640

The released `yolox_tiny.onnx` has a **static** `[1,3,416,416]` input, so this
configuration is not directly runnable from the published export. The model is
re-exported with dynamic spatial dimensions:

```bash
python -c "
import onnx
m = onnx.load('models/yolox_tiny.onnx')
d = m.graph.input[0].type.tensor_type.shape.dim
d[2].dim_param='height'; d[3].dim_param='width'
for o in m.graph.output:
    o.type.tensor_type.shape.dim[1].dim_param='anchors'
onnx.save(m, 'models/yolox_tiny_dyn.onnx')"
```

Verified rather than assumed: the head produces **3549 anchors at 416** and
**8400 at 640**, exactly (80² + 40² + 20²). That confirms the anchor/stride decode
is correct at the new size, not merely that inference did not crash.

`PRAHARI_DETECTOR_INPUT_SIZE` then selects the resolution, because a dynamic
export has no fixed size to read from the model.

## Detector input convention

Bound at load time by probing, not by assumption. Ultralytics YOLOv5/v8/v11
exports expect RGB normalised to 0..1; YOLOX's exports expect BGR at raw 0..255.
Measured on a MOT17 frame with yolox_tiny: **0.881 raw/BGR, 0.868 raw/RGB, 0.000
normalised either way.**

Getting this wrong raises nothing. It produces an empty detection list on every
frame — indistinguishable from "this model cannot see anything in this footage",
which is a conclusion one might plausibly reach about night-time border imagery
and write down. It was in fact reached about MOT17, where the right answer was 63
confident people in the first frame tried. The probe runs once, on the first real
frame, and reports its choice in `describe()`.

## Segmentation

Event-triggered only, never on the per-frame path — see
[segmentation.md](segmentation.md).

| Backend | Device | Latency | Mask IoU (synthetic) | Status |
|---|---|---|---|---|
| GrabCut | CPU | 43 ms @ 640×480, **207 ms @ 1080p** | 1.000 | tested fallback |
| SAM 2 tiny | CUDA | **not yet characterised** | 0.975 | validated, 25/25 masks |

Backend **agreement on real MOT17 frames is 0.395**, against 0.975 on the
synthetic scene. That gap is the finding: the synthetic scene is a flat-coloured
foreground on a flat background, which GrabCut's colour model solves exactly, so
it cannot discriminate between the two backends. Real cluttered pedestrian frames
can, and they disagree substantially.

Neither backend's *accuracy* on real imagery is measured, because MOT17 has no
mask ground truth. Agreement says how far apart they are, not which is right.

**SAM 2's GPU latency and VRAM remain unmeasured.** Every attempt so far was taken
while an unrelated training job held the device at 100%, which measures contention
rather than the model. `scripts/benchmark_segment.py` detects this and marks the
affected rows rather than printing a number that would be read as a property of
SAM 2.

## Open measurements

1. **CUDA detector rows** — YOLOX-Tiny @640 and YOLOX-S @640 on the RTX 4050, with
   VRAM. These decide whether C's tracking-stability advantage is affordable.
2. **Clean SAM 2 benchmark** — FPS, latency and VRAM on an idle GPU.

Both are blocked on the same occupied device, and both are queued to run
automatically when it frees. Until they exist, the detector choice is a candidate
and the segmentation cost is unknown, and this page says so.

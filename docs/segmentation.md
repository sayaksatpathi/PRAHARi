# Segmentation — event-triggered precision refinement

Segmentation is a **second stage**, not the detector. The detector and tracker
run continuously and cheaply on every frame and decide *what is worth looking
at*; segmentation runs only on the few events that clear the priority bar and
looks at them precisely, prompted by the box those stages already produced.

```
detector → tracker → event candidate → scored → priority ≥ threshold?
                                                       │ yes
                                                       ▼
                                        segmenter (box prompt) → mask
                                                       │
                                    ┌──────────────────┼──────────────────┐
                                    ▼                  ▼                  ▼
                          mask-in-zone fraction   ground contact     evidence
                          (score adjustment)      (refined foot)     overlay
```

Running segmentation on every frame of every camera would need hardware nobody
budgeted for. Running it on the handful of events that matter costs almost
nothing and buys real precision. That is the whole design.

## What a mask buys over a box

- **Mask-in-zone fraction.** A box either overlaps a restricted polygon or it
  does not. A mask says *how much* of the real object is inside it. A person
  whose box clips a zone corner while their body is entirely outside is a
  different event from one standing squarely inside — and only the mask separates
  them. This is verified both ways in `tests/test_segment.py`: an object inside
  the zone strengthens the score, a clipped-corner box weakens it.
- **Refined ground contact.** Zone membership is decided on where an object meets
  the ground. The lowest point of the silhouette is a better estimate of that
  than the box bottom, which is loose or wrong under partial occlusion.
- **Evidence.** The object is outlined precisely on the actual trigger frame,
  with the ground-contact point marked — far easier for an operator to verify
  than a rectangle that also contains fence, ground and sky.

The refinement **annotates, it does not veto.** The first stage already decided
the event is worth attention; a second-stage segmenter that could cancel an alert
would be a single point of failure for missing a real intrusion. So the mask
adjusts the priority score within a bounded range and records why, and it never
suppresses an event on its own.

## Backends

Selected by `PRAHARI_SEGMENT_BACKEND` (`auto` | `sam_onnx` | `grabcut` | `off`).

| Backend | What it is | Status |
|---|---|---|
| **GrabCut** | OpenCV box-prompted foreground segmentation, CPU, no download | **real and tested** — ~12–40 ms/object, the verified default |
| **SAM 2 ONNX** | Segment Anything 2 via ONNX Runtime (no torch), CPU or CUDA | written, **unvalidated against real weights on this machine** |
| off | no segmentation; events keep box-based behaviour | — |

`auto` uses SAM 2 when its weights are present and otherwise GrabCut, so a node
with the weights gets the good backend and a node without one still gets working,
tested refinement rather than nothing.

### GrabCut is a real backend, not a placeholder

OpenCV's GrabCut is genuine box-prompted segmentation — it models foreground and
background colour distributions and cuts the object out with a graph min-cut. It
produces a real silhouette, real ground contact and a real zone-intersection
fraction, on the CPU, with nothing to download. Verified live: on an intrusion it
outlined the subject and marked the true foot point (see the mask overlay in the
v0.5 notes). It is blunter than SAM and can bleed into similar-coloured
background, so it is marked `approximate` and the report says so. On a fanless
edge box with no accelerator, a promptable classical segmenter invoked only on
important events is a defensible choice in its own right.

### SAM 2 — why ONNX, and the honest caveat

SAM 2 is used through an **ONNX export**, not its official PyTorch build, for the
same reason the detector is: adding torch and torchvision would put ~2.5 GB of
framework on an edge node whose premise is staying small enough to update over a
VSAT link. Pre-exported SAM 2.1 encoder+decoder ONNX models exist
([vietanhdev/segment-anything-2.1-onnx-models](https://huggingface.co/vietanhdev/segment-anything-2.1-onnx-models)),
so segmentation becomes one more `onnxruntime` session — no torch, same CPU/CUDA
selection as everything else.

**The caveat, stated prominently because it matters.** The SAM ONNX backend is
written against the documented export interface but has **not been run against
real SAM weights on the build machine**, because the weights and `onnxruntime-gpu`
could not be downloaded on a ~0.1 MB/s connection (the same limit that blocked
MOT17). Its pre/post-processing follows the published SAM 2 convention and is
defensive about output layout, but it must be validated the first time it runs
against real weights. Until then GrabCut is the verified path and SAM is the
upgrade that slots in behind the same interface. Shipping elaborate, untested
model glue as if it were verified is exactly the failure this project avoids.

**EdgeTAM** (Apache-2.0, an on-device SAM 2 variant, 22× faster) fits the same
interface. Its documented export path is CoreML rather than ONNX today, so it is
noted as a future backend rather than implemented.

## Installing SAM 2 weights (on a machine that can download them)

```bash
# encoder + decoder ONNX, into models/
#   models/sam2_encoder.onnx
#   models/sam2_decoder.onnx
# then:
export PRAHARI_SEGMENT_BACKEND=auto     # picks SAM when the weights are present
pip install onnxruntime-gpu             # for CUDA on the RTX 4050
python scripts/benchmark_segment.py --backend sam_onnx
```

`scripts/benchmark_segment.py` reports latency, throughput and — when a CUDA
provider and GPU are actually present — the GPU memory the backend uses, so you
can confirm the configuration fits in the 6 GB before wiring it into a live node.
It measures honestly: on a CPU-only install it reports "not measured" for VRAM
rather than inventing a figure.

## Cost

Invoked only for events at or above `PRAHARI_SEGMENT_MIN_PRIORITY` (default
`high`), and run off the event loop in a thread so a slow segmentation never
stalls detection or the sync queue. GrabCut measured at 24.6 fps (40 ms mean, 45
ms p95) on a 640×480 frame — negligible for the few events per minute that reach
the threshold. SAM on the GPU is expected to be faster per call; benchmark it on
the target box.

## Configuration

```
PRAHARI_SEGMENT_BACKEND=auto        # auto | sam_onnx | grabcut | off
PRAHARI_SEGMENT_MIN_PRIORITY=high   # only refine events at/above this
PRAHARI_SAM_ENCODER_PATH=./models/sam2_encoder.onnx
PRAHARI_SAM_DECODER_PATH=./models/sam2_decoder.onnx
```

## Limitations

- The SAM ONNX backend is unvalidated against real weights here (above).
- GrabCut bleeds into similar-coloured background and is weaker in low contrast —
  a night-IR or thermal frame with a faint subject gives a rougher mask. Marked
  `approximate`.
- The mask-in-zone refinement applies to POLYGON zones (restricted areas). LINE
  tripwires and ROUTE corridors have no area to intersect, so those events get a
  mask and refined ground contact but no zone fraction, and the refinement says
  "no zone to refine against" plainly.
- Segmentation is single-object per event (the track that raised it). Group
  events are not yet segmented per member.

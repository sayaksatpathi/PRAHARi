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
| **SAM 2 ONNX** | Segment Anything 2 via ONNX Runtime (no torch), CPU or CUDA | **validated** (v0.8) — sam2_hiera_tiny on CUDA, RTX 4050, 25/25 masks, IoU 0.975 |
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

### SAM 2 — why ONNX, and what validation actually caught

SAM 2 is used through an **ONNX export**, not its official PyTorch build, for the
same reason the detector is: adding torch and torchvision would put ~2.5 GB of
framework on an edge node whose premise is staying small enough to update over a
VSAT link. Pre-exported SAM 2.1 encoder+decoder ONNX models exist
([vietanhdev/segment-anything-2.1-onnx-models](https://huggingface.co/vietanhdev/segment-anything-2.1-onnx-models)),
so segmentation becomes one more `onnxruntime` session — no torch, same CPU/CUDA
selection as everything else.

**Validated in v0.8**, against `sam2_hiera_tiny` encoder + decoder on the CUDA
execution provider, RTX 4050 6 GB. Until then this section carried a prominent
caveat that the backend had been *written* against the documented export
interface and never run — and the caveat was earned. It was wrong in three ways,
every one of which reads as correct code:

| What was wrong | Why it was invisible |
|---|---|
| `"mask_input" in name` also matches `has_mask_input`, so the boolean flag was fed a `(1, 1, 256, 256)` tensor | Substring tests over input names look reasonable until two names nest. The decoder rejected the rank and *every* segmentation failed — loudly, at least. |
| The encoder emits `(high_res_feats_0, high_res_feats_1, image_embed)`, so `enc_out[0]` passed a 32×256×256 feature map where a 256×64×64 embedding belonged | Positional binding that happens to suit one export silently corrupts another. |
| The decoder returns a mask over the **padded** square, and it was resized straight back to frame size | This one produces no error at all. On a 16:9 frame the object is stretched vertically by the padding ratio — a **44% error** — and the output still looks like a plausible mask. |

Inputs and outputs are now bound by their exact names taken from the loaded
graph, a decoder whose signature does not match raises rather than guessing, and
the letterbox is cropped before the resize.

That sequence is the argument for the caveat having been there. The first two
failures were noisy; the third would have shipped. Elaborate model glue that has
never been executed should be labelled as such, and this is what it costs to find
out otherwise.

**EdgeTAM** (Apache-2.0, an on-device SAM 2 variant, 22× faster) fits the same
interface. Its documented export path is CoreML rather than ONNX today, so it is
noted as a future backend rather than implemented.

### Measured

`scripts/benchmark_segment.py`, 640×480 frame, box prompt, mask IoU scored
against the benchmark scene's known foreground:

| Backend | Device | Mean | p95 | Mask IoU | Succeeded |
|---|---|---|---|---|---|
| GrabCut | CPU | 43 ms | 51 ms | 1.000 | 25/25 |
| SAM 2 tiny | CUDA | *see below* | | 0.975 | 25/25 |

Two honest qualifications on that table, because without them it reads as
"GrabCut wins", which is not what it shows.

**The GPU latency is not quotable yet.** Every SAM timing on this machine so far
was taken while an unrelated training job held the GPU at 100% utilisation, so it
measures contention for the device rather than the model. The benchmark now
detects other CUDA compute processes and says so in its output rather than
printing a number that would be read as a property of SAM 2. Re-run on an idle
GPU for a figure worth citing.

**The IoU comparison flatters GrabCut.** The benchmark scene is a flat-coloured
foreground on a flat background — precisely the case GrabCut's colour model
solves exactly. It is the right scene for checking that a backend *works*, and
the wrong one for choosing between them. SAM's advantage is on cluttered, low
contrast, partially-occluded subjects, which is to say on real border imagery,
and it is not evidenced here. Treat 1.000 vs 0.975 as "both produce a correct
silhouette on an easy shape", nothing more.

## Installing SAM 2 weights

```bash
# 134 MB encoder + 21 MB decoder, the smallest practical configuration
B=https://huggingface.co/vietanhdev/segment-anything-2-onnx-models/resolve/main
curl -L -C - -o models/sam2_encoder.onnx $B/sam2_hiera_tiny.encoder.onnx
curl -L -C - -o models/sam2_decoder.onnx $B/sam2_hiera_tiny.decoder.onnx

pip install onnxruntime-gpu             # CUDA; see the note below
export PRAHARI_SEGMENT_BACKEND=auto     # picks SAM when the weights are present
python scripts/benchmark_segment.py --backend sam_onnx
```

### Getting CUDA to actually engage

`onnxruntime-gpu` advertises `CUDAExecutionProvider` whether or not the CUDA
runtime can be loaded, and a session that cannot load it **falls back to the CPU
without raising**. Inference is then an order of magnitude slower with no error
anywhere, which is easy to mistake for "the GPU is not very fast".

`prahari.common.cuda` resolves this explicitly: it locates a CUDA 12 + cuDNN 9
runtime, registers it, and only then offers the CUDA provider, so the provider
list reflects what will really run. Point `PRAHARI_CUDA_DLL_DIR` at a directory
holding `cudart64_12.dll`, `cublas64_12.dll` and `cudnn64_9.dll` if yours is
somewhere unusual; otherwise the usual locations are searched.

On Windows both `os.add_dll_directory` **and** `PATH` are needed, and getting only
the first right looks like success. `add_dll_directory` covers DLLs the loader
resolves for us, but ORT opens `onnxruntime_providers_cuda.dll` itself by absolute
path, and *its* imports are resolved through the default search order — which
includes `PATH` and does not include added DLL directories. That is the entire
content of the otherwise unhelpful `LoadLibrary failed with error 126`.

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

- **SAM 2's GPU latency on this hardware is not yet characterised** — every
  measurement so far was taken under contention from an unrelated job (above).
- **Neither backend's mask accuracy has been measured on real border imagery.**
  The IoU figures come from a synthetic scene chosen to be unambiguous, which is
  the right test for correctness and the wrong one for ranking.
- The encoder runs on the **full frame** for every segmentation, so the cost is
  per-event, not per-object: two objects in one event pay for two encodes. Caching
  the embedding per frame is the obvious optimisation and is not done.
- GrabCut bleeds into similar-coloured background and is weaker in low contrast —
  a night-IR or thermal frame with a faint subject gives a rougher mask. Marked
  `approximate`.
- The mask-in-zone refinement applies to POLYGON zones (restricted areas). LINE
  tripwires and ROUTE corridors have no area to intersect, so those events get a
  mask and refined ground contact but no zone fraction, and the refinement says
  "no zone to refine against" plainly.
- Segmentation is single-object per event (the track that raised it). Group
  events are not yet segmented per member.

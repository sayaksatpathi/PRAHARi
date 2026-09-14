# Prahari Edge — BOP deployment profile

What a Border Outpost deployment of Prahari actually requires, separated into
three categories that are not interchangeable:

- **Specification** — a design property of the system. True by construction.
- **Measured (prototype)** — a real number from this build, on the simulated
  fleet or on MOT17. Real, and not a field figure.
- **Requires field measurement** — genuinely unknown until it is deployed.
  Stated as unknown rather than estimated.

> **Prahari is designed to degrade gracefully under constrained connectivity.
> Exact power and bandwidth requirements require deployment-specific field
> measurements.**

That sentence is the honest position and it is not a hedge. A BOP's power budget
depends on the camera count, the PoE load, the backhaul radio, the ambient
temperature and the enclosure — none of which this build has seen. A number
invented here would be quoted in a procurement document later.

## Deployment profile

| Item | Specification |
|---|---|
| Cameras | Existing IP cameras / NVR feeds — no camera replacement |
| Video processing | Edge-first; inference runs at the outpost |
| Normal network mode | Event metadata + required evidence |
| Degraded mode | Reduced/sub-stream or event-only |
| Offline mode | Local inference + local evidence queue |
| Recovery | Store-and-forward synchronisation, original timestamps preserved |
| Evidence storage | Local disk with retention policy and eviction under pressure |
| Compute (prototype) | RTX 4050-class 6 GB GPU; CPU-only path also functional |
| Core connectivity | **Edge dials out.** No inbound exposure of cameras or node |
| Failure tolerance | Camera or network interruption does not stop local analytics |
| Power | **Requires field measurement** before any watt/UPS figure is claimed |
| Network | **Requires field measurement** before any Mbps/GB-per-day figure |

## Measured on the prototype

Real numbers from this build. They characterise the *software's* demands, not a
field installation.

### Evidence and metadata

Measured over an evidence store of 294 artefacts from demo runs:

| Artefact | Count | Mean size |
|---|---|---|
| Event metadata (JSON, full record) | 543 events | **2.0–2.5 KB** |
| Trigger frame (JPEG) | 170 | **62 KiB** |
| Evidence clip (MP4, pre+post roll) | 59 | **14.0 MiB** |
| Segmentation mask overlay (PNG) | 65 | 1.5 MiB |

The metadata-to-evidence ratio is the whole basis of the link strategy: a
complete, sealed, court-usable event *record* is ~2.5 KB, while its video is
three to four orders of magnitude larger. `EVENT_ONLY` mode ships the former and
leaves the latter at the edge for retrieval on demand — which is why the measured
bandwidth reduction is 99.56–99.86% rather than a rounding.

The 1.5 MiB mask overlays are full-frame PNGs and are larger than they need to
be; storing the mask polygon rather than a rendered overlay is an obvious
saving and is not yet done.

### Compute cost per stream

Measured on MOT17 (1920×1080), CPU only, the full detect → track path:

| Configuration | Per frame | Sustained |
|---|---|---|
| YOLOX-Tiny @416 | 27.9 ms | 35.8 fps |
| YOLOX-Tiny @640 (candidate) | 70.2 ms | 14.3 fps |
| YOLOX-S @640 | 106.1 ms | 9.4 fps |

Prahari does not run detection on every frame — `inference_interval` decouples
detection from frame rate, and the tracker coasts between detections. A camera at
12 fps with detection every 2nd frame needs ~6 inferences/second, so the
candidate configuration costs roughly **0.42 s of CPU per camera-second**. That
is the figure that determines how many cameras one node carries, and it is
measured rather than assumed.

GPU figures are **not yet measured** — see [inference-stack.md](inference-stack.md).

### Compute power draw

The GPU reports its own limits and draw through `nvidia-smi`:

| | |
|---|---|
| Device | RTX 4050 Laptop, 6 GB |
| Maximum power limit | 50 W |
| Enforced power limit | 35 W |

Those are **hardware specifications**, not a measurement of Prahari. A measured
draw under Prahari inference requires an idle GPU, which has not been available.
Even then it would cover only the accelerator — not the CPU, the storage, the
PoE budget for the cameras, or the backhaul radio, which together dominate a
real outpost's consumption.

## What is deliberately not claimed

**A BOP power budget.** Not measured, not estimated. Establishing one needs a
defined camera count, PoE class, enclosure and radio at a specific site, then an
inline meter. Anything else is arithmetic on guesses.

**A solar/battery sizing.** Follows from the above and is equally unavailable.

**A Mbps or GB/day figure.** The *ratio* is measured and the modes are
specified; the absolute number depends on event rate at the site, which is
exactly the thing the pattern-of-life model is there to learn and which varies by
sector and season.

**Thermal/environmental envelope.** Untested. A sealed roadside cabinet in a
Terai summer is a real engineering constraint and this build has not addressed
it.

## Why the connectivity design is a specification and not a hope

Three properties hold by construction, and each is tested:

**The edge dials out.** The node initiates every connection to the core. No
inbound port is opened toward cameras or the node, so the deployment does not
require the sector network to expose an outpost.

**Detection never depends on the link.** Analytics, scoring, evidence capture and
the hash chain all run locally. The link affects only what has been *shipped*,
never what has been *seen* — demonstrated by the network-cut action in the demo.

**Records survive storage pressure; clips do not.** Under disk pressure, clips
are evicted and event records are kept, with the eviction recorded. The failure
mode is a degraded evidence package, never a missing event.

See [offline-mode.md](offline-mode.md) for the mechanism and its tests.

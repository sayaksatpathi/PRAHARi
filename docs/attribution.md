# Attribution and third-party components

Prahari uses open-source components and borrows published techniques. Both are
listed here, because SIH requires that verified open-source components be
acknowledged, and because a government-facing project should be able to answer
"what is in this and under what licence" without an audit.

**No code was copied from any existing NVR or video-analytics project.** Where an
approach is borrowed, it is cited in the module that uses it.

## Runtime dependencies

| Component | Licence | Used for |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | HTTP API and WebSocket |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | ASGI framework beneath FastAPI |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI server |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | Domain model, validation, serialisation |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | MIT | Environment configuration |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause | Array maths throughout |
| [OpenCV](https://github.com/opencv/opencv-python) | Apache-2.0 | Decode, image measurement, rendering, clip writing |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | Model inference, CPU and CUDA |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Edge-to-core synchronisation |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | Tests |

Python standard library only for: SQLite storage, password hashing (`scrypt`),
bearer tokens (`hmac`), hashing (`hashlib`), the message bus.

The frontend has **no dependencies at all** — no framework, no CDN, no build
step. That is deliberate: an edge node that needs a Node toolchain to render its
own interface cannot be recovered from a USB stick at an outpost.

## Detection models

**No model weights are committed to this repository.** Model licences vary and
bundling them silently is how a licence obligation ends up unmet.

Notably, **Ultralytics YOLO is AGPL-3.0**, which is a real procurement
consideration for a government deployment. Prahari therefore runs ONNX graphs
through ONNX Runtime (MIT) and documents licence-clean alternatives — RT-DETR,
YOLOX, D-FINE, all Apache-2.0 — in `models/README.md`. Whichever model is
installed, *its own licence applies* and the deployment is expected to record it.

## Techniques and standards

| Source | Used for |
|---|---|
| **IEC 62676-4** — Video surveillance systems, application guidelines | The DORI pixels-on-target bands (12/25/62/125/250 px/m) that capability grants are decided against |
| **ByteTrack** (Zhang et al., 2022, [arXiv:2110.06864](https://arxiv.org/abs/2110.06864)) | The two-stage association idea: a second matching pass against low-confidence detections. Re-implemented from the published method, not from the authors' code |
| Classic single-view metrology | Ground-plane recovery from pedestrian bounding boxes and a stature prior |
| Standard JPEG blockiness metrics | Block-artifact severity on the 8×8 grid |
| Variance of Laplacian | Focus/sharpness measurement |

## Things considered and deliberately not used

Recorded because the reasoning is part of the design, and because "why didn't you
just fork X" is a fair question.

| Project | Licence | Why not |
|---|---|---|
| [Frigate](https://github.com/blakeblackshear/frigate) | MIT | An excellent local NVR, and forkable. But it assumes a healthy LAN, an always-reachable server and continuous local recording. Capability profiling, evidence sealing, bandwidth-adaptive transmission and multi-day offline operation would each be invasive changes to its core loops, not plugins. None of Prahari's contribution lives in it |
| [Viseron](https://github.com/roflcoopter/viseron) | MIT | Same shape, same reasoning |
| [Kerberos Agent](https://github.com/kerberos-io/agent) | MIT | Same shape, same reasoning |
| [ZoneMinder](https://github.com/ZoneMinder/zoneminder) | GPL-2.0 | Mature but architecturally distant from an edge-first design |

## Planned, not yet integrated

Listed so the roadmap's licence position is clear in advance:

| Component | Licence | For |
|---|---|---|
| [fast-alpr](https://github.com/ankandrew/fast-alpr) | MIT | ANPR — ONNX plate detection and OCR, no torch dependency |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | MIT | Serving recorded footage as genuine RTSP for demonstration and testing |

## Datasets

No dataset is bundled or redistributed. For component-level benchmarking, the
public datasets worth using are MOT17 (tracking), VIRAT (surveillance activity),
UFPR-ALPR and Indian plate datasets (ANPR), AI City Challenge (multi-camera
vehicles), and Anti-UAV (small aerial targets) — each under its own terms, which
must be read before use.

**None of them represents Indian border CCTV conditions.** Component benchmarks
on these do not transfer to night, range, fog or a decade-old fog-lensed dome,
and Prahari makes no accuracy claim on their basis. Domain validation would need
authorised footage from the sector concerned, which is a matter for the
sponsoring organisation and not something to be improvised.

No sensitive or private surveillance footage has been used in developing this
project. All imagery in the demonstration is synthetic and generated by
`prahari/edge/sources/simulator.py`.

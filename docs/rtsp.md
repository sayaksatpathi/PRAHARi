# Real RTSP ingestion

v0.2. Frames arrive over the wire instead of being produced in-process, decoded
by the same `StreamSource` that reads a camera on a pole. Nothing above the
source layer changes — which was the point of having a source layer.

## Running it

```bash
pip install imageio-ffmpeg                       # ffmpeg, via pip
python scripts/make_footage.py                   # render recordings
python scripts/serve_rtsp.py                     # terminal 1: MediaMTX
python scripts/use_rtsp_cameras.py --only CAM-011,CAM-031   # terminal 2
```

`--revert` puts the cameras back on simulated sources.

MediaMTX itself is not vendored in git. Fetch the release for your platform from
[bluenviron/mediamtx](https://github.com/bluenviron/mediamtx/releases) and unzip
it into `vendor/mediamtx/`. Verified here with **v1.21.0 windows_amd64**,
sha256 `8a58a9b8c25ee99a96c23dc0a17f39ace3072c01d2e148329073c64ddf83493d`.

## Why MediaMTX

It is the component you would actually deploy in front of real cameras. Its
documented role is proxying and re-streaming existing RTSP sources, which is
precisely the retrofit topology Prahari targets: point it at the cameras, or at
the site's existing recorder, and Prahari consumes one well-behaved endpoint per
camera instead of competing for the camera's limited client slots.

Streams start **on demand** — ffmpeg is launched only when something connects,
and stops when the last reader disconnects. Cheaper, and a closer match to how a
real proxy behaves.

`-re` paces each file at its true frame rate. Without it the pipeline would
receive an hour of footage in seconds and every temporal measurement — frame
rate, dwell time, speed — would be wrong.

## Verified

- MediaMTX serves five paths; its API confirms readiness before anything connects.
- OpenCV opens `rtsp://127.0.0.1:8554/cam-011` and reads 36/36 frames at 1280×720.
- Prahari ingests over RTSP at 10.3 and 7.6 fps alongside simulated cameras in
  the same fleet — a mixed deployment, which is what a phased retrofit looks
  like.
- Camera health, reconnection and the RTSP-over-TCP transport are all exercised
  by the real code path rather than a simulator shortcut.

## The honest limitation, which is the important part of this page

**Detection does not work over RTSP with the shipped defaults, and it cannot.**

Prahari has two detectors. The real ONNX model works on photographic video. The
synthetic detector works from simulator ground truth. Serving *synthetic footage*
over RTSP defeats both at once:

| | Synthetic footage over RTSP | Real footage over RTSP |
|---|---|---|
| **Real ONNX model** | sees nothing — measured | works |
| **Synthetic detector** | no ground truth over the wire | not applicable |

This was measured, not assumed. Running YOLOX-tiny (Apache-2.0) against a
rendered frame gives a peak score of **0.33, classified "bird"**, with control
probes behaving correctly — flat grey scores 0.0002, random noise 0.03. The
decode is right; the model simply does not recognise flat-shaded geometric
figures as people. The same is true of fast-alpr and synthetic number plates.

So the value of RTSP ingestion is only fully realised with **real footage**, and
that is the next piece of work rather than something this page can claim.

### How the system behaves about it

Loudly, rather than silently:

- The node logs an error naming the camera, the source kind and the fix.
- `GET /api/system/status` reports `detector_blind: true` with an explanation.
- The camera card in the dashboard shows **"Detecting nothing"** in red.

A camera reporting a healthy frame rate while detecting nothing forever is the
most dangerous failure mode in this system, so it is stated in three places
rather than left to be discovered during a demonstration.

## Using real footage

Drop your own recordings into `footage/` — nothing downstream cares where the
pixels came from — and install a real model:

```bash
# a licence-clean, directly downloadable option
curl -L -o models/yolo.onnx \
  https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx
```

YOLOX is Apache-2.0. Its released ONNX exports emit **undecoded** boxes in grid
units, which `onnx_yolo.py` detects from the data (box magnitudes far below the
input resolution) and decodes with the anchor-point and stride grid. That
detection is automatic and requires no configuration.

Record what you installed in `models/README.md` — model licences vary and a
government deployment needs to be able to answer the question.

## Using an actual camera

```bash
curl -X POST localhost:8420/api/cameras/CAM-011/test -H "Authorization: Bearer $TOKEN"
```

Reports `CONNECTED` only after a frame has genuinely been read. A misconfigured
RTSP path frequently opens and then delivers nothing, and reporting that as
connected is a lie the operator discovers at the worst moment.

Two field notes that matter more than they sound:

- **Prefer the sub-stream.** Analytics rarely need full resolution and the decode
  saving is large.
- **Consider pulling from the recorder, not the camera.** Most installed IP
  cameras cap simultaneous RTSP clients, and on a site with an NVR that budget is
  often already spent.

RTSP is forced over TCP. UDP is the default and it is the wrong default here: a
lossy backhaul produces torn frames that look like motion to a detector and
generate false events all night.

## Bugs this work surfaced

- A camera marked `OFFLINE` during RTSP connection setup stayed offline for the
  rest of its life while happily delivering video — health was never restored on
  recovery.
- A Windows path inside a YAML scalar starts a double-quoted string, and YAML
  then reads `\U` and `\S` as escape sequences and refuses to parse the config at
  all. Paths are now emitted with forward slashes inside single-quoted scalars.
- MediaMTX's output was being written to a pipe nobody read, which both risks
  deadlocking the child when its buffer fills and hid the startup error that
  explained why it had exited.

# Detection models

Prahari loads a YOLO-style ONNX graph from `models/yolo.onnx`. **No model is
committed to this repository**, because model weights carry their own licences
and bundling them silently is how a licence obligation ends up unmet in a
government deployment.

With no model present the node falls back to a clearly-labelled synthetic
detector, badges it in the dashboard, and says so in the logs.

## Why ONNX Runtime rather than PyTorch + Ultralytics

Three reasons, all operational rather than academic:

**Footprint.** ~50 MB against ~2.5 GB. That is the difference between an edge
node that can be updated over a VSAT link and one that cannot.

**One code path.** The same file runs CPU-only on a fanless box at an outpost and
CUDA on a workstation, selecting the execution provider at load time.

**Licensing.** Ultralytics YOLO is **AGPL-3.0**. An AGPL dependency is a real
procurement consideration for a government deployment, and it is a question worth
removing from the path rather than discovering late. ONNX Runtime is MIT, and an
exported ONNX graph is just a graph — but *the exported model's own licence still
applies*, so record it below for whatever you install.

## Getting a model

Any detector that exports to ONNX with a YOLO-style output head works; the output
layout is detected at load time rather than assumed. Licence-clean options worth
preferring over AGPL weights:

| Model | Licence | Notes |
|---|---|---|
| RT-DETR | Apache-2.0 | Transformer detector, strong at range |
| YOLOX | Apache-2.0 | Well-established, easy ONNX export |
| D-FINE | Apache-2.0 | Recent, competitive |
| YOLOv8/v11 (Ultralytics) | **AGPL-3.0** | Excellent, but read the licence first |

Export to ONNX, place the file at `models/yolo.onnx`, and restart the node. To
use a different path set `PRAHARI_MODEL_PATH`.

For GPU inference:

```bash
pip uninstall onnxruntime
pip install onnxruntime-gpu
```

## Classes

The COCO indices Prahari maps into its own object classes are in
`prahari/edge/detect/onnx_yolo.py`. Note that COCO's `cow`, `sheep` and `horse`
all map to a single `cattle` class: at an Indian border livestock is both the
dominant false-alarm source and, on some sectors, the smuggled commodity, so it
gets first-class treatment rather than being filtered out as noise.

A model trained only on COCO will not detect boats well at range, will not detect
small UAVs at all usefully, and has never seen a thermal image. Those are real
gaps, not configuration problems.

## A necessary caveat on accuracy

A pretrained COCO model's published metrics say **nothing** about performance on
border imagery at night, at range, through fog, rain or dust, on a decade-old
fog-lensed dome. No public dataset represents Indian border CCTV conditions, and
component-level benchmarks on MOT17, VIRAT or AI City do not transfer.

Any operational accuracy claim requires validation on real domain footage from
the sector in question. Until that exists, Prahari reports detector confidence as
a detector confidence and nothing more.

## Record what you installed

Fill this in when you add a model, so the deployment can answer the question
later:

```
Model file      :
Architecture    :
Trained on      :
Source URL      :
Licence         :
Date installed  :
Validated on    : (domain footage, or "not validated")
```

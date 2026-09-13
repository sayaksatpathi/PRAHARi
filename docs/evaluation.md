# Evaluation

The deterministic replay harness. It answers "how accurate is it?" with a report
instead of an adjective — and, just as importantly, it is honest about which of
its numbers are claims and which are plumbing checks.

```bash
python scripts/evaluate.py                    # whole simulated fleet
python scripts/evaluate.py --only CAM-011
python scripts/evaluate.py --footage footage/ # file sources
```

Writes `var/eval/evaluation.json` and a self-contained `evaluation.html`.

## What it measures, and what each number is worth

The harness drives the real pipeline components — profiling, detection, tracking,
rules, scoring, the alert governor — synchronously and deterministically against
a source, and scores the result against ground truth it controls.

| Metric | Meaningful when… | What it is |
|---|---|---|
| **Intruder detected** | always | did the scripted incident produce an attributable event |
| **Detection latency** | always | time from the intruder entering frame to the first attributable event |
| **Alert suppression** | always | fraction of rule events the governor recorded without interrupting an operator |
| **False alarms raw → alerted** | always | the rule-engine flood, then what reached the operator |
| **Profiling error** | simulated source | recovered vs true camera mounting height |
| **Detection recall / precision** | **real model + real footage** | on the simulated fleet these characterise the *synthetic detector's modelled behaviour*, not a trained network |
| **Throughput** | always | per-frame processing cost of the analytics chain |

## The result on the simulated fleet

From a representative run (55 s profiling, 45 s evaluation per camera, seed
20260913):

| Camera | Intruder | Latency | Suppression | Raw→alerted | Det. recall | Profiling err |
|---|---|---|---|---|---|---|
| CAM-011 gate | yes | 3.7 s | 92% | 380 → 28 | 0.99 | 0.2% |
| CAM-014 perimeter | yes | 2.7 s | 93% | 371 → 28 | 0.56 | 0.3% |
| CAM-022 approach (open) | yes | 0.6 s | 71% | 97 → 29 | 0.92 | 2.2% |
| CAM-031 thermal | yes | 2.8 s | 94% | 493 → 26 | 0.53 | 0.4% |
| CAM-045 legacy | yes | 5.5 s | 97% | 1181 → 26 | 0.64 | 0.5% |

Read it the way the report tells you to.

**Profiling error 0.2–2.2%** is a genuine result. The ground plane is recovered
from observed pedestrians and checked against the height the simulator was
configured with; the harness never sees that height. This is the same
measurement `scripts/smoke_profiling.py` makes, now across the whole fleet under
the live detector's noise.

**Alert suppression 71–97%** is genuine and is the number that matters most. It
is the governor holding a 380–1181-event raw flood down to ~26–29 alerts. The
one lower figure, CAM-022 at 71%, is not a worse result — it is a camera that
produced only 97 raw events, so the governor's hourly budget was never exceeded
and it had little to suppress. The metric behaves correctly; it is not a single
number to rank cameras by.

**Detection recall tracks camera quality** exactly as it should: 0.99 on the
crisp gate camera, 0.53–0.64 on the degraded perimeter and legacy cameras. But
these numbers describe the *synthetic detector's modelled miss rate*, which rises
with image degradation by design. They are not the accuracy of a trained network
and the report says so in bold. To get real detection accuracy, run the same
harness with a real model against real footage.

## What is deliberately not reported

**A per-hour false-alarm rate.** Extrapolating one from a sub-minute run is
dominated by the governor's start-up transient — its budget is hourly, so in the
first 45 seconds it has not yet begun rationing. The suppression *fraction* is
robust to run length; a per-hour *rate* from a short run is not, and inventing
one would be exactly the kind of number this project exists to avoid. The live
node additionally applies a learnt pattern of life, not present in the harness,
which damps the rate further — so the harness's alerted counts are an upper
bound.

## Running against real footage (MOT format)

This is the work that turns the labelled numbers into claims, and the path is
built and tested. `prahari.eval.mot` ingests a MOTChallenge sequence — numbered
frames plus a `gt/gt.txt` of per-frame boxes — and the harness scores a real
detector's recall and precision against those real annotations through the same
code that scores the simulator.

```bash
# 1. a real, licence-clean model (Apache-2.0)
curl -L -o models/yolo.onnx   https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx

# 2. a real annotated dataset (see the connection note below on where this runs)
python scripts/fetch_datasets.py --download mot17

# 3. the same harness, now measuring a real model on real imagery
python scripts/evaluate.py --mot footage/real/MOT17/train --max-sequences 2
```

`--mot` runs a **detection-only** evaluation: real detection recall/precision per
class, tracking counts, ground-plane self-calibration on real pedestrians, and
throughput. The border-scenario metrics (intruder, latency, false-alarm
suppression) do not apply to a street-pedestrian dataset with no fence, and are
omitted rather than faked.

The path is validated end to end by `tests/test_mot.py` against a self-made MOT
fixture with known ground truth: a perfect detector scores recall 1.0, a blind
one scores 0.0, and the real YOLOX model has been run through the adapter to
confirm the integration (it reports ~18–20 fps and, correctly, finds nothing in
the synthetic fixture frames). Only real annotated imagery is missing, and that
needs the download.

### The honest constraint on getting the data

**The build machine's connection could not download these datasets.** It
sustained roughly 0.1 MB/s to both GitHub and MOTChallenge, which makes MOT17's
~5.9 GB a multi-hour-at-best proposition that repeatedly stalled. So the real
numbers must be produced on a machine with a normal connection.
`fetch_datasets.py` downloads with resume support — re-run to continue an
interrupted transfer — and everything downstream of the download is already built
and tested. This is an infrastructure limit, stated plainly, not an unfinished
feature.

`scripts/fetch_datasets.py` lists the legitimate public datasets — MOT17, VIRAT,
UFPR-ALPR, AI City, Anti-UAV — with their licences and access terms. It downloads
nothing by default, and only the genuinely open ones (MOT17) on explicit request.

### VIRAT

VIRAT is gated behind a **signed Data Protection Agreement.** Accepting that is a
legal commitment for the deploying party to make, not something this tooling does
on anyone's behalf, so `fetch_datasets.py` will not fetch it — it points you at
the agreement and the terms. Once obtained under those terms, VIRAT's videos drop
into `footage/` and its activity annotations map onto the event-level metrics;
the ingestion follows the same source-layer contract as everything else.

**The standing caveat, unchanged:** no public dataset represents Indian border
CCTV — night, range, fog, a decade-old fog-lensed dome. Component benchmarks on
these do not transfer, and Prahari makes no field-accuracy claim on their basis.
Certifying field performance needs authorised footage from the sector concerned,
which is the sponsoring organisation's to provide.

## Why a harness rather than a benchmark number

Because the metric this system lives or dies on is not detection accuracy. A
detector that never misses is worthless if its operator muted the console on
night three. The two columns beside detection — did the right alarm fire, and how
many wrong ones fired with it — are what decide whether a deployment survives
contact with a real operator, and they depend on the whole pipeline, not the
detector alone. The harness measures those, deterministically, so the answer to
"how accurate is it" is a report anyone can regenerate and read.

Every number is derived from ~40 lines of readable code in
`prahari/eval/metrics.py`, against ground truth the harness controls. Nothing is
a self-reported model claim.

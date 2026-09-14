# Current limitations

Clearly bounded capabilities with a defined validation path.

This page exists because it is the same principle the system itself runs on. A
camera that cannot resolve a plate does not get granted ANPR — it refuses the
analytic and says why, in a certificate anyone can audit. A patrol that matches
on schedule but breaks its expected direction is escalated rather than
suppressed. A detector that has never been run against real weights is labelled
as such until it has been.

A project built that way does not get to hide its own boundaries. Everything
below is a known limit with a stated route to closing it, not a defect
discovered under questioning.

---

## Validation

**No authorised Indian border-footage validation.** This is the largest gap and
the one that cannot be closed by writing code. Every accuracy figure Prahari
reports comes from MOT17 — daylight street pedestrians in European and American
cities — or from the simulator. Nothing about night, range, fog, monsoon, or a
decade-old fog-lensed dome is characterised.

*Route:* the evaluation harness, the MOTChallenge adapter, the detection and
CLEAR-MOT/IDF1 scoring all exist and produce real numbers today. Point them at
annotated footage from the sector concerned and they produce field figures on the
first run. Obtaining that footage under the appropriate terms is the sponsoring
organisation's to do, not this tooling's.

**Real benchmark results are MOT17, not SSB border footage.** Stated wherever the
numbers appear:

| Configuration | Precision | Recall | MOTA | IDF1 | FPS (CPU) |
|---|---|---|---|---|---|
| YOLOX-Tiny @640 | 0.755 | 0.557 | 0.412 | 0.494 | 14.3 |

Real, measured, reproducible — and domain-limited. COCO-pretrained detection
metrics do not transfer to border conditions, and no operational claim rests on
them.

**VIRAT is gated behind a signed Data Protection Agreement.** Accepting it is a
legal commitment for the deploying party. The tooling points at the agreement and
refuses to fetch the data on anyone's behalf.

---

## Sensing

**Thermal ingestion is supported; thermal-specific models are not validated.**
`SensorType.THERMAL` and `IR_ILLUMINATED` are first-class, low-light conditions
are detected and fed to scoring, and the tamper detector learns a per-camera
baseline specifically so a thermal or night-IR feed is not falsely flagged as a
covered lens. What is missing is a detector *trained on thermal imagery*. An RGB
COCO model on a thermal feed is a compromise, and the capability certificate will
restrict such a camera accordingly rather than pretend otherwise.

*Route:* the detector is an ONNX graph behind an interface. A thermal-trained
export drops in with no pipeline change.

**UAV detection exists in the architecture, not in a validated model.**
`ObjectClass.UAV`, `Capability.UAV_DETECTION`, priority weighting and simulator
support with altitude are all present. No trained UAV model has been validated,
and small aerial objects at range are a genuinely hard detection problem that a
general COCO model does not solve.

*Route:* same as thermal — a small-object aerial model behind the same interface.

**Crawling and low-profile movement is a known limitation.** Standard detectors
are trained overwhelmingly on upright humans, and a crawling or deliberately
low-profile subject is substantially harder to detect.

This is worse for Prahari than for a generic detector, and the reason is worth
being explicit about: **ground-plane self-calibration uses a 1.70 m stature
prior.** A crawling subject does not merely evade detection — if it were accepted
as a calibration sample it would corrupt the camera's geometry estimate. The
profiler already guards against this in part (observed-only samples, Theil–Sen
regression that resists outliers, a leverage test that refuses to fit on
insufficient depth spread), but the prior itself remains an assumption about
posture.

*Route:* pose-aware detection for low-profile movement, and a posture check
before a sample is admitted to calibration. Neither is built. It is listed as a
named limitation rather than claimed as a capability.

---

## Identity and correlation

**Cross-camera re-identification across substantially different modalities
remains limited.** Handoff, global identities and corridor-dropout detection are
built and tested, but the appearance cue is an HSV colour signature. A subject on
a night-IR camera and the same subject on a daylight gate camera will not produce
similar colour histograms.

This is a real limitation, not a tuning problem. The matcher is conservative
about it by design: with no usable appearance signature it falls back to timing
and object class with a *tighter* bar, and an arrival it cannot link confidently
becomes a new entity rather than being forced onto a weak candidate.

*Route:* a re-identification model producing modality-robust embeddings.

**Topology is configured, not discovered.** Camera adjacency is declared; only
the transition *times* are learned from observed handoffs.

---

## Deployment

**Power and connectivity figures require field measurement.** No watt, UPS,
solar, Mbps or GB/day figure is claimed. The architecture's degradation
behaviour is specified and tested; the absolute numbers depend on camera count,
PoE load, backhaul radio, enclosure and site event rate.

What *is* measured on the prototype: event metadata at 2.0–2.5 KB against
evidence clips at ~14 MiB, giving the 99.56–99.86% bandwidth reduction in
event-only mode; and ~0.42 s of CPU per camera-second for the candidate detector
configuration. See [deployment-profile.md](deployment-profile.md).

**Sensor fusion is an integration hook, not a capability.** Prahari is the video
layer. Radar and PIDS integration has a defined attachment point and contract; it
is not implemented and has not been demonstrated. See
[sensor-fusion.md](sensor-fusion.md).

**GPU inference cost is not characterised.** SAM 2 works against real weights
(25/25 masks, IoU 0.975), but every latency measurement so far was taken while an
unrelated job held the GPU, which measures contention rather than the model. The
benchmark detects this and refuses to present those figures as model properties.

**Docker files are untested.** Docker was unavailable on the development machine;
the native path is the supported one.

---

## Scoring and alerting

**The Event Priority Score is a ranking function, not a probability.** It has not
been calibrated against border ground truth and is never presented as a
likelihood. Every contribution is a named factor with visible arithmetic, which
is the property that matters for an operator deciding whether to trust it.

**Patrol suppression cannot tell two people apart.** Someone moving with a patrol,
at patrol pace, in the patrol's direction, inside its window is inside every cue
the layer has. The per-window suppression budget bounds the damage — a follower
gets one downgrade, not an evening of immunity — but it does not detect them.

**Face recognition is never granted**, on any camera, at any measured image
quality. That is a product policy, not a capability gap. See
[privacy.md](privacy.md).

---

## How to read this page

Every item above is either measured, specified, or explicitly unknown. None of it
is estimated and presented as measured.

That distinction is the point of the project. A border video system that
overstates what it can see teaches its operators to ignore it by about night
three, and an operator who has stopped looking makes the system's accuracy
irrelevant. The same discipline that makes Prahari refuse an analytic a camera
cannot support requires it to refuse a claim the evidence does not support.

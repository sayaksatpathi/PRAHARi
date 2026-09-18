# Prahari — SIH Presentation Package

The evidence package for the SIH26187 evaluation. The goal is one continuous
story a judge can follow, backed by measured numbers and a demo that survives a
network cut.

Binary assets (`.pptx`, `.png`) are **not committed** (see `.gitignore`); this
folder tracks the scripts, checklists and capture specs that let anyone
regenerate them. Capture the screenshots and export the deck from a live run,
then drop them next to these files locally.

## Contents

```text
presentation/
├── README.md                  # this file
├── SIH_Prahari_Final.pptx     # (local, not committed) exported deck
├── diagrams/
│   └── README.md              # what each diagram must show + source
│       architecture.png
│       deployment-topology.png
│       data-pipeline.png
│       alert-lifecycle.png
│       benchmark-results.png
├── screenshots/
│   └── README.md              # exact screens + capture state for each shot
│       login.png  live-camera.png  alerts.png  cross-camera.png
│       patrol-deviation.png  camera-capability.png
│       evidence-integrity.png  system-health.png
└── demo/
    ├── demo-script.md         # the 3-scenario spoken walkthrough
    └── demo-checklist.md      # pre-flight + failure-recovery checklist
```

## The one story to tell

```text
Existing CCTV → RTSP/ONVIF → Camera Capability Assessment → Edge AI →
Detection + Tracking → Context/Normalcy/Patrol Rules → Alert Governor →
Evidence + Hash Chain → Core Dashboard → Operator Response
```

Then **three concrete scenarios** — not every feature — in `demo/demo-script.md`:
1. Normal open-border movement → no unnecessary alarm.
2. Suspicious deviation → governed alert with evidence.
3. Connectivity failure → local inference + store-and-forward.

## The five judge questions the deck must answer up front

1. What problem does Prahari solve?
2. Why can it work with existing CCTV?
3. What happens when connectivity fails?
4. What evidence does an alert produce?
5. How was the system actually evaluated? → point at
   [`../docs/benchmark-matrix.md`](../docs/benchmark-matrix.md).

## Evidence discipline

Every quantitative claim on a slide must trace to a MEASURED row in
[`../docs/benchmark-matrix.md`](../docs/benchmark-matrix.md). If a number is
`PENDING` there, it does not go on a slide as a result.

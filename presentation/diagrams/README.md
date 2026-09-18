# Diagram Spec

Each diagram is exported to a `.png` (not committed) for the deck. Author them
from the descriptions below — keep them faithful to
[`../../docs/architecture.md`](../../docs/architecture.md). Mermaid sources are
included so the diagrams regenerate identically.

## architecture.png — end-to-end pipeline

```mermaid
flowchart LR
  A[Existing CCTV] --> B[RTSP / ONVIF]
  B --> C[Camera Capability Assessment]
  C --> D[Edge AI]
  D --> E[Detection + Tracking]
  E --> F[Context / Normalcy / Patrol Rules]
  F --> G[Alert Governor]
  G --> H[Evidence + Hash Chain]
  H --> I[Core Dashboard]
  I --> J[Operator Response]
```

## data-pipeline.png — data provenance flow

```mermaid
flowchart LR
  DS[Public datasets<br/>MOT17/BDD100K/VIRAT/...] --> PROV[provision_datasets.ps1]
  PROV --> TR[data/training]
  PROV --> TE[data/testing]
  BORDER[Prahari Border Scenario Set<br/>synthetic/controlled] --> TE
  TR --> EVAL[evaluate*.py / benchmark_*.py]
  TE --> EVAL
  EVAL --> BM[docs/benchmark-matrix.md<br/>MEASURED rows only]
```

## alert-lifecycle.png — from detection to governed alert

```mermaid
flowchart TD
  DET[Person/vehicle detected] --> TRK[Tracked]
  TRK --> RULE{Normalcy / patrol / doctrine}
  RULE -->|lawful pattern| SUP[Recorded, not alerted]
  RULE -->|abnormal pattern| SUS[SUSPICIOUS_ACTIVITY]
  SUS --> GOV[Alert Governor]
  GOV --> EV[Evidence clip + frame + metadata]
  EV --> HASH[Hash chain]
  HASH --> DASH[Dashboard alert -> operator]
```

## deployment-topology.png — edge + core + connectivity failure

```mermaid
flowchart LR
  CAM[Cameras] --> EDGE[Edge node<br/>local inference + queue]
  EDGE -->|link up| CORE[Core dashboard + store]
  EDGE -. link down .-> X((X))
  EDGE --> Q[Local evidence queue]
  Q -->|link restored| CORE
```

## benchmark-results.png — measured evidence

Render this **only** from the MEASURED rows of
[`../../docs/benchmark-matrix.md`](../../docs/benchmark-matrix.md). Do not include
PENDING capabilities as if they were results. Suggested bars: MOT17 throughput
(12.6 fps), MOTA 0.403, IDF1 0.500, Re-ID Rank-1 0.705 / mAP 0.485, alert
suppression 71–97%, profiling error 0.2–2.2%.

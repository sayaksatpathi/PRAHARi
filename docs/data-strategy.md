# Prahari Data Strategy & Evaluation Sets

Because no single public dataset covers the complete Indian open-border surveillance problem, Prahari relies on a combined dataset stack to defensibly evaluate its sub-capabilities (Detection, Tracking, Suspicious Activity, Normalcy, ANPR, Face Detection, and Multi-Camera Reasoning). 

This document outlines the canonical datasets mapped to each pipeline component.

## 1. Core Dataset Matrix

| Dataset | Best Use in Prahari | Target Module | Split Availability | Video/Image | License Note |
|---------|---------------------|---------------|--------------------|-------------|--------------|
| **MOT17** | Pedestrian detection + MOT | `tracker.py` | Train/Test | Video | Non-commercial research |
| **BDD100K** | Vehicles, diverse weather/time | `yolox.py` / `tracker.py` | Train/Val/Test | Video | Custom / Non-commercial |
| **VIRAT** | Surveillance, human-vehicle interaction | `pipeline.py` (Events) | Train/Val | Video | Public Domain / CC0 |
| **MEVA/ActEV**| Multi-camera, IR, EO | `sync.py` / `crosscam.py` | Train/Dev/Eval | Video | Public / NIST |
| **Street Scene**| Scene normalcy / Anomalies | `normalcy.py` | Train/Test | Video | CC-BY-SA-4.0 |
| **UCF-Crime** | Suspicious/Abnormal activity | `rules/engine.py` | Train/Test | Video | Academic research only |
| **WIDER FACE**| Face Detection | `detector.py` | Train/Val/Test | Image | Academic research only |
| **CCPD** | License Plate Detection | `anpr.py` | Train/Val/Test | Image | Academic / Chinese Plates |
| **VisDrone** | UAV / Aerial Extension | `sources.py` | Train/Val/Test | Video | Non-commercial research |

## 2. Directory Structure Convention

To standardise benchmarking and training, datasets should be organised locally as follows:

```text
data/
├── training/
│   ├── detection/
│   │   ├── BDD100K/
│   │   ├── MIO-TCD/
│   │   └── VisDrone/
│   ├── tracking/
│   │   ├── MOT17/
│   │   └── BDD100K-MOT/
│   ├── activity/
│   │   ├── VIRAT/
│   │   ├── MEVA/
│   │   └── UCF-Crime/
│   ├── face/
│   │   └── WIDER-FACE/
│   └── anpr/
│       ├── CCPD/
│       └── UFPR-ALPR/
├── testing/
│   ├── mot17_holdout/
│   ├── street_scene/
│   ├── virat_holdout/
│   ├── night/
│   ├── cctv/
│   └── real_world/
└── demo/
    ├── normal_day.mp4
    ├── normal_night.mp4
    ├── patrol.mp4
    ├── wrong_direction.mp4
    ├── suspicious_activity.mp4
    ├── vehicle_checkpoint.mp4
    └── camera_outage.mp4
```

## 3. The Missing Gap: Prahari Border Validation Set

Public datasets lack the specific pattern-of-life for India-Nepal / India-Bhutan open borders. To supplement this, a small controlled validation set (the **Prahari Border Scenario Video Set**) must be constructed and annotated with the following labels to ensure the system handles legitimate movements and avoids false-alert flooding:

* `NORMAL_OPEN_BORDER`: Lawful pedestrian and vehicle cross-border traffic.
* `PATROL_MATCHED`: SSB Patrol following the assigned geo-corridor.
* `PATROL_DEVIATION`: Patrol vectoring off the assigned route.
* `OFF_ROUTE`: Unidentified person deviating from the standard crossing vector.
* `NIGHT_MOVEMENT`: Temporal activity violating twilight threshold rules.
* `GROUP_ACTIVITY`: Loitering or localized aggregation of unidentified entities.
* `RESTRICTED_VEHICLE`: Vehicle approaching a No-Go zone.
* `CAMERA_TAMPER` / `STREAM_REPLAY_SUSPECTED`: Hardware disruption detection.

## 4. Acquisition Log

Provenance for datasets as they are actually provisioned. A row is recorded when
the dataset is *verified at source*, and its download/checksum state is noted
explicitly so an un-fetched dataset is never mistaken for a local one.

### Street Scene (activity / scene-normalcy)

| Field | Value |
|---|---|
| Source URL (official) | https://www.merl.com/research/downloads/StreetScene |
| Canonical host | Zenodo — https://doi.org/10.5281/zenodo.10870472 |
| Direct file | `StreetScene.zip` (single archive) |
| Verified at source | 2026-09-18 |
| Download date | **NOT DOWNLOADED** — see note |
| License / usage | **CC-BY-SA-4.0** (share-alike, attribution; derivatives must carry the same license) |
| Archive size | **48.98 GB** (single zip) |
| Checksum (source, MD5) | `a74c51e99881c860d97e1ebb51ee1f2c` (from Zenodo record) |
| Local checksum (verify on download) | — |
| Publication date | 2024-03-25 |
| Sequences | 46 training + 35 testing (strictly separated) |
| Frames | 203,257 total (56,847 train / 146,410 test); 1280×720 @ 15 fps |
| Test annotations | 205 anomalous events across 17 anomaly types (bbox + track id) |
| Intended split | Train → `data/training/activity/StreetScene/`; Test → `data/testing/street_scene/` |
| Target module | `normalcy.py` (train on normal, detect anomalous) |

> **Status (updated 2026-09-24): disk blocker cleared, download deferred.** The
> project now lives on a 931 GB SSD (`E:`) with ~330 GB free, so the 48.98 GB
> archive + extraction now fits. The remaining blocker is **download time** — at
> typical throughput the 49 GB pull is a multi-hour job, impractical to complete
> and benchmark in an interactive session. Provenance is verified from the Zenodo
> record (md5 `a74c51e…`, CC-BY-SA-4.0); fetch it as a standalone/overnight job
> (`curl -C - https://zenodo.org/api/records/10870472/files/StreetScene.zip/content`)
> then benchmark the normalcy/anomaly path. Do not treat Street Scene as locally
> present until `Download date` and `Local checksum` are
> filled in.

### WIDER FACE (face detection)

| Field | Value |
|---|---|
| Source URL (official) | http://shuoyang1213.me/WIDERFACE/ |
| Download mirror | Hugging Face `CUHK-CSE/wider_face` (official CUHK mirror) |
| Download date | **2026-09-18** (DOWNLOADED, verified) |
| License / usage | **Academic / research only** (non-commercial); face **detection** benchmark, not identity recognition |
| Downloaded size | ~3.68 GB archives → ~3.6 GB extracted (1.8 GB train+val, 1.8 GB test) |
| Files (SHA-256 verified) | `WIDER_train.zip` e23b7612…, `WIDER_val.zip` f9efbd09…, `WIDER_test.zip` 3b0313e1…, `wider_face_split.zip` c7561e4f… |
| Images | Train 12,880 · Val 3,226 · Test 16,097 = **32,203** (matches benchmark; 393,703 faces) |
| Annotations | `wider_face_train_bbx_gt.txt`, `wider_face_val_bbx_gt.txt` present; **test has filelist only, no GT** |
| Split placement | Train + val + annotations → `data/training/face/WIDER-FACE/`; Test → `data/testing/wider_face/` (held separate) |
| Local eval caveat | Test ground truth is withheld — **no local test-accuracy claims**; official eval server only. Report on the **val** split. |
| Target module | `detector.py` (face-detection component evaluation) |
| Training started? | **No** — acquisition only, per instruction |
| Validation evaluation | **Done (2026-09-18)** — `scripts/evaluate_widerface.py`, VOC-style AP@0.5 on official val split (3,226 imgs, 39,112 faces). **Haar (baseline): AP 0.121** (P 0.664, R 0.131, 74.6 ms, CPU). **SCRFD-500M (candidate): AP 0.489** (P 0.749, R 0.506, 18.8 ms, CPU). SCRFD not promoted. Reports: `var/widerface_val_haar.json`, `var/widerface_val_scrfd_cpu.json`. See [benchmark-matrix.md](benchmark-matrix.md), [model-provenance.md](model-provenance.md). |


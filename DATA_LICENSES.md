# Dataset Provenance & Licenses

Provenance record for every dataset Prahari trains or evaluates against. This is
a **technical provenance log, not a legal procurement conclusion** — the same
caveat as [`docs/model-provenance.md`](docs/model-provenance.md). Items marked
research-only or non-commercial must be cleared by the relevant compliance team
before any public or commercial deployment.

Fill in `Download date` and `Checksum` when you actually fetch each archive
(`Get-FileHash -Algorithm SHA256 <file>`). Until a dataset is downloaded, leave
those fields as `—`.

---

## MOT17
- **Source URL:** https://motchallenge.net/data/MOT17/
- **Download date:** —
- **License:** Non-commercial research (MOTChallenge terms)
- **Permitted use:** Academic benchmarking of detection/tracking
- **Restrictions:** No redistribution; cite the MOTChallenge benchmark
- **Used for:** Person detection + multi-object tracking (primary benchmark)
- **Train/Test split:** Official train sequences; MOT17-02 / MOT17-04 held out for testing
- **Checksum (SHA-256):** —

## BDD100K
- **Source URL:** https://github.com/bdd100k/bdd100k
- **Download date:** —
- **License:** BDD100K custom license (see repo `doc/source/license.rst`) — non-commercial terms apply
- **Permitted use:** Detection/tracking research on driving scenes
- **Restrictions:** Follow the dataset's custom license; keep license in provenance record
- **Used for:** Vehicle detection (car/truck/bus/motorcycle), day/night variation, MOTS tracking
- **Train/Test split:** Official train/val/test; MOTS subset = 223 annotated videos
- **Checksum (SHA-256):** —

## VIRAT
- **Source URL:** https://viratdata.org/
- **Download date:** —
- **License:** Public release (US Government funded); attribution expected
- **Permitted use:** Surveillance activity/event research
- **Restrictions:** Follow VIRAT release terms
- **Used for:** Activity/event engine testing (people/vehicle interactions, static CCTV)
- **Train/Test split:** Train/validation annotations; held-out clips in testing/virat_holdout
- **Checksum (SHA-256):** —

## MEVA / ActEV
- **Source URL:** https://actev.nist.gov/ (data: https://mevadata.org/)
- **Download date:** —
- **License:** Public training/development resources (NIST); sequestered eval separate
- **Permitted use:** Multi-camera activity detection research (EO + IR)
- **Restrictions:** Evaluation partitions are sequestered; use only public dev resources locally
- **Used for:** Multi-camera + IR activity pipeline testing
- **Train/Test split:** Public train/dev; eval sequestered by NIST
- **Checksum (SHA-256):** —

## VisDrone
- **Source URL:** https://github.com/VisDrone/VisDrone-Dataset
- **Download date:** —
- **License:** Non-commercial research
- **Permitted use:** UAV/aerial detection & tracking research
- **Restrictions:** No commercial use
- **Used for:** UAV extension / aerial detection hook (not primary fixed-CCTV benchmark)
- **Train/Test split:** Official train/val/test (288 clips / 261,908 frames + static images)
- **Checksum (SHA-256):** —

## Street Scene
- **Source URL:** https://www.merl.com/research/downloads/StreetScene (mirror: https://zenodo.org/records/10870472)
- **Download date:** —
- **License:** CC-BY-SA-4.0
- **Permitted use:** Anomaly/normalcy research; share-alike with attribution
- **Restrictions:** Derivatives must carry CC-BY-SA-4.0
- **Used for:** Scene normalcy engine (train on normal, detect anomalous)
- **Train/Test split:** 46 train + 35 test sequences (203k+ frames)
- **Checksum (SHA-256):** —

## UCF-Crime
- **Source URL:** https://www.crcv.ucf.edu/projects/real-world/
- **Download date:** —
- **License:** Academic research only
- **Permitted use:** Suspicious/abnormal event research and evaluation
- **Restrictions:** Non-commercial; not representative of an India–Nepal/Bhutan border — do not present as such
- **Used for:** Research/evaluation of suspicious-event recognition (13 anomaly categories)
- **Train/Test split:** Official train/test partitions
- **Checksum (SHA-256):** —

## WIDER FACE
- **Source URL:** http://shuoyang1213.me/WIDERFACE/ (official) via mirror https://huggingface.co/datasets/CUHK-CSE/wider_face
- **Download date:** 2026-09-18
- **License:** Academic research only (CUHK MMLab / WIDER FACE benchmark terms)
- **Permitted use:** Face **detection** evaluation
- **Restrictions:** Non-commercial; not an identity/recognition dataset. Test set has NO public ground truth — do not claim local test accuracy (official eval server only).
- **Used for:** Evaluating the face-detection component only (not recognition)
- **Train/Test split:** 40% train / 10% val / 50% test (32,203 images total, 393,703 faces). Downloaded: train 12,880 + val 3,226 (→ data/training/face/WIDER-FACE/), test 16,097 (→ data/testing/wider_face/, held separate)
- **Checksum (SHA-256):**
  - `WIDER_train.zip` (1.47 GB): `e23b76129c825cafae8be944f65310b2e1ba1c76885afe732f179c41e5ed6d59`
  - `WIDER_val.zip` (363 MB): `f9efbd09f28c5d2d884be8c0eaef3967158c866a593fc36ab0413e4b2a58a17a`
  - `WIDER_test.zip` (1.84 GB): `3b0313e11ea292ec58894b47ac4c0503b230e12540330845d70a7798241f88d3`
  - `wider_face_split.zip` (annotations, 3.6 MB): `c7561e4f5e7a118c249e0a5c5c902b0de90bbf120d7da9fa28d99041f68a8a5c`

## CCPD
- **Source URL:** https://github.com/detectRecog/CCPD
- **Download date:** —
- **License:** Academic research
- **Permitted use:** Plate localization/recognition research
- **Restrictions:** **Chinese** license-plate imagery — must NOT back any Indian ANPR accuracy claim
- **Used for:** ANPR plate detection/recognition training (300k+ images)
- **Train/Test split:** Official train/val + challenge subsets (blur/rotate/tilt/challenge)
- **Checksum (SHA-256):** —

## UFPR-ALPR
- **Source URL:** https://web.inf.ufpr.br/vri/databases/ufpr-alpr/
- **Download date:** —
- **License:** Academic research only / non-commercial (signed access agreement)
- **Permitted use:** ALPR research validation only
- **Restrictions:** Requires access agreement; **research validation only** in provenance
- **Used for:** ANPR video validation (research only)
- **Train/Test split:** Official split per access agreement
- **Checksum (SHA-256):** —

## MIO-TCD
- **Source URL:** https://tcd.miovision.com/challenge/dataset.html
- **Download date:** —
- **License:** Miovision challenge terms (research)
- **Permitted use:** Vehicle classification/localization research
- **Restrictions:** Follow challenge terms; image-based — does not replace video testing
- **Used for:** Vehicle-class mapping (786,702 images)
- **Train/Test split:** Official classification + localization challenge splits
- **Checksum (SHA-256):** —

## Market-1501
- **Source URL:** https://zheng-lab.cecs.anu.edu.au/Project/project_reid.html
- **Download date:** —
- **License:** Academic research only
- **Permitted use:** Person re-identification research
- **Restrictions:** Non-commercial; retrain on a commercially clear Re-ID set before deployment (see model-provenance.md)
- **Used for:** Cross-camera Re-ID evaluation (ResNet embeddings)
- **Train/Test split:** Official Market-1501 train/test
- **Checksum (SHA-256):** —

---

## Demo / stock footage

Clips used purely to **demonstrate** the software (not as scientific benchmarks).
Verify each individual clip's license before embedding it in any public SIH
presentation.

| Source | URL | License note | Used for |
|--------|-----|--------------|----------|
| Mixkit | https://mixkit.co/free-stock-video/ | Mixkit Free / Restricted licenses vary per clip — check each | CCTV / night-traffic demo clips |
| Pexels | https://www.pexels.com/videos/ | Pexels license (free personal & commercial, no attribution required, subject to restrictions) | Surveillance / people / night clips |

### Real-footage detection test clips (downloaded 2026-09-18)

Downloaded to `data/testing/real_world/` (gitignored). Real detector test only —
generic street/transit scenes, **NOT border footage**. Results in
`var/real_clip_test.json`.

| File | Source URL | ID | License | Size | Used for |
|------|-----------|----|---------|------|----------|
| `pexels_13258882_people_cars_street.mp4` | https://www.pexels.com/video/people-and-cars-on-street-13258882/ | 13258882 | Pexels (free, commercial OK, no attribution required) | 21 MB (HD 1080) | Real YOLO detection test (people+vehicles) |
| `pexels_3700915_subway_pedestrians.mp4` | https://www.pexels.com/video/escalator-in-a-subway-3700915/ | 3700915 | Pexels (free, commercial OK, no attribution required) | 2.7 MB (HD 1080) | Real YOLO detection test (pedestrians) |
| `pexels_3552510_street_people_walking.mp4` | https://www.pexels.com/video/people-walking-on-the-street-3552510/ | 3552510 | Pexels (free, commercial OK, no attribution required) | 17.3 MB (HD 1080) | Real YOLO detection test — **ground-level** street (best match) |
| `pexels_8126410_pedestrian_crossing.mp4` | https://www.pexels.com/video/men-are-crossing-on-the-pedestrian-lane-8126410/ | 8126410 | Pexels (free, commercial OK, no attribution required) | 6.2 MB (HD 1080) | Real YOLO detection test — ground-level crossing |

> Stock footage is **demo footage, not a benchmark.** Do not cite it as evidence
> of measured accuracy.

### Real Indian ANPR test images (downloaded 2026-09-24)

`data/testing/anpr_india/` (gitignored) — 13 real Indian vehicle images for ANPR
validation, from the public GitHub repo `sid0312/ANPR`
(https://github.com/sid0312/ANPR, `darknet/data/obj/car_*.jpeg`). Used only to
test fast-alpr on **Indian** plates (vs the Chinese CCPD set). Results:
`var/anpr_india_results.json`, annotated in `var/anpr_india/`.

# Prahari Data Directory

This directory is the **local, reproducible** home for every dataset Prahari
trains and evaluates against. The payloads themselves are **never committed** —
only this README, the per-dataset provenance in
[`../DATA_LICENSES.md`](../DATA_LICENSES.md), and the provisioning script
[`../scripts/provision_datasets.ps1`](../scripts/provision_datasets.ps1) are
tracked. Anyone cloning the repository regenerates the tree from those.

> Rationale: the datasets total tens of gigabytes and several of them carry
> registration or non-commercial licenses that forbid redistribution. The repo
> stays small, and provenance stays auditable. See
> [`../docs/data-strategy.md`](../docs/data-strategy.md) for why this particular
> dataset stack was chosen per capability.

## Reproduce the structure

```powershell
pwsh scripts/provision_datasets.ps1
```

This creates the directory tree below and downloads the freely available
MOT17 sample. Every other dataset requires you to accept its license first —
follow the links in [`../DATA_LICENSES.md`](../DATA_LICENSES.md).

## Expected structure

```text
data/
├── README.md                  # this file (tracked)
├── training/
│   ├── detection/
│   │   ├── BDD100K/           # vehicles + road scenes, day/night
│   │   ├── MIO-TCD/           # vehicle classification/localization (image)
│   │   └── VisDrone/          # UAV/aerial people + vehicles
│   ├── tracking/
│   │   ├── MOT17/             # primary pedestrian MOT benchmark
│   │   └── BDD100K-MOT/       # driving multi-object tracking
│   ├── activity/
│   │   ├── VIRAT/             # surveillance activities, static CCTV
│   │   ├── MEVA/              # multi-camera EO+IR activity detection
│   │   └── UCF-Crime/         # suspicious/abnormal activity
│   ├── face/
│   │   └── WIDER-FACE/        # face detection (image)
│   └── anpr/
│       ├── CCPD/              # plate detection/recognition (Chinese plates)
│       └── UFPR-ALPR/         # plate video (research-only)
├── testing/
│   ├── mot17_holdout/         # held-out MOT17-02 / MOT17-04 sequences
│   ├── street_scene/          # normal-vs-anomalous scene normalcy
│   ├── virat_holdout/         # held-out VIRAT activity clips
│   ├── night/                 # night-movement clips for NIGHT_MOVEMENT rule
│   ├── cctv/                  # legally-usable stock CCTV demo footage
│   └── real_world/            # miscellaneous real-world validation clips
└── demo/                      # curated clips for the SIH walkthrough
    ├── normal_day.mp4
    ├── normal_night.mp4
    ├── patrol.mp4
    ├── wrong_direction.mp4
    ├── suspicious_activity.mp4
    ├── vehicle_checkpoint.mp4
    └── camera_outage.mp4
```

## Rules for this directory

1. **Never commit payloads.** `.gitignore` excludes `data/training/**`,
   `data/testing/**`, `data/demo/**` and all `*.zip`, `*.onnx`, `*.pt`, `*.mp4`,
   `*.avi` files. Only READMEs and `.gitkeep` markers are tracked.
2. **Record provenance on download.** Add a row to
   [`../DATA_LICENSES.md`](../DATA_LICENSES.md) for every dataset you fetch,
   including the SHA-256 checksum of the archive.
3. **Respect research-only licenses.** UCF-Crime, WIDER FACE, UFPR-ALPR and
   others are academic/non-commercial. They may inform evaluation but must not
   back a claim of production performance. CCPD is Chinese plate imagery — do
   **not** use it to claim Indian ANPR accuracy.
4. **Keep the held-out split held out.** MOT17-02 / MOT17-04 stay in
   `testing/mot17_holdout/` and are never mixed into training.

## Checksums

After downloading an archive, record its checksum so a later re-download can be
verified:

```powershell
Get-FileHash -Algorithm SHA256 data/training/tracking/MOT17.zip
```

Paste the resulting hash into the matching row of
[`../DATA_LICENSES.md`](../DATA_LICENSES.md).

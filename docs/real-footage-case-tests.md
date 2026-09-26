# Prahari — Real-Footage Per-Case Module Tests (CCTV perspective)

**Why:** Prahari is a video-intelligence layer over **existing fixed CCTV**. Real
border CCTV is sensitive and unavailable, so each border *case* is exercised on a
**separate real public clip shot from a fixed, elevated, wide CCTV-style angle**
(royalty-free, Mixkit) — not drones, not cinematic close-ups — and every perception
module is run on it and reported. These are public CCTV-perspective proxies, **not
real Indian border footage**, but they are real pixels from the viewpoint Prahari
actually deploys on.

> Deployment note: Prahari runs on mounted CCTV only. Drone/aerial footage is used
> **only** as the separate labelled "range" proxy in the field-validation matrix
> ([field-validation-kit.md](field-validation-kit.md) §0), never as a claim that
> Prahari runs on drones.

Reproduce:
```bash
python scripts/fetch_border_case_clips.py     # fixed-CCTV-angle clips (gitignored)
python scripts/test_border_cases.py --all     # all modules on every clip
```
Modules per clip: **detection** · **tracking** (ByteTrack) · **ANPR** (fast-alpr) ·
**segmentation / SAM 2** · **face** (YuNet). Output: `var/border_cases_results.json`.

## Results (RTX 4050, GPU, ~120 frames/clip)

| Case (CCTV clip) | Detection | Tracking (person) | ANPR (fast-alpr) | SAM 2 masks | Face |
|------------------|-----------|-------------------|------------------|-------------|------|
| **Gate / chokepoint** — close plates (CAM-011 case) | plate close-ups | — | **80 reads, 36 unique — `555ZBF` 0.95, `1RQT648` 0.94, `1ESR563` 0.93** | — | — |
| **Line crossing** — high-angle junction | person ×2549, car ×270, bus ×33, truck ×32, bike ×22, moto ×18 | **335 tracks (178 concurrent)** | 52 reads, 51 unique (partial) | 23 (~149 ms) | 56 |
| **Crowd / group** — high-angle busy street | person ×1396, car ×732, bus ×36, truck ×34 | 183 tracks (107 concurrent) | 58 reads, 42 unique | 16 (~143 ms) | 21 |
| **Night movement** — low-light street | person ×1651, bike ×94, moto ×31, car ×3 | 211 tracks (98 concurrent) | 1 read (small/distant) | 18 (~142 ms) | 38 |
| **Vehicle / traffic** — high-angle road | car ×38, person ×32 | 3 tracks | 1 read (small/distant) | 4 (~165 ms) | 0 |
| **Animal / livestock** — herd | **cattle ×120 (0 person)** | — | 0 (correct) | — | — |

## Honest reading, per module (on CCTV-perspective footage)

- **Detection** works across all CCTV cases and is multi-class (person, car, bus,
  truck, bicycle, motorcycle). It **classifies livestock as `cattle`, not
  `person`** — the open-border cattle-vs-person confusion concern, handled.
- **Tracking** sustains heavy real CCTV load — **335 identities (178 concurrent)**
  on the junction, 211 at night — not a simulator artifact.
- **Segmentation / SAM 2** produces plausible person-shaped masks on real CCTV
  pixels (~0.32–0.44 area fraction, ~140–165 ms/mask on GPU). This is the first
  real-footage sanity check of the SAM 2 ONNX backend (previously UNVALIDATED).
- **Face (YuNet)** fires on people in the fixed-camera scenes (56 / 38 / 21 frames)
  and correctly returns nothing on the animal clip.
- **ANPR now reads real plates on real footage.** On the **gate/chokepoint** clip
  (the CAM-011 "Main Gate - Vehicle Lane" case) fast-alpr returns **36 unique plates,
  top `555ZBF` at 0.95 confidence**; on the wide junction/crowd clips it still reads
  partial plates off passing cars (lower confidence, as expected at distance). The
  per-frame variants (`555ZBF`/`5557BF`, `1ESR563`/`IESR563`) are OCR jitter that
  Prahari's `RepeatPlateTracker` temporal aggregation converges.
  - **Bug fixed in the process (important):** `prahari/edge/anpr.py` did
    `float(ocr.confidence)`, but this fast-alpr version returns the OCR confidence as
    a **per-character list** → `TypeError` → **every real plate read was silently
    dropped**, in this harness *and in the live pipeline*. Added `_conf_scalar()` to
    average list/array confidences; ANPR now returns reads instead of nothing.
- **Note on unsuitable footage:** an earlier night top-view clip was a **light-trail
  timelapse** (vehicles as streaks) and yielded almost nothing — a useful reminder
  that Prahari needs real-time CCTV frames, not timelapse. It was replaced with a
  fixed real-time high-angle traffic clip.

## What this establishes / does not

- **Establishes:** every perception module runs on **real CCTV-perspective footage**
  across the border cases with sane, honest numbers; failure modes (ANPR distance
  gating, small/distant vehicles) are visible and explained.
- **Does not:** establish real *Indian border* CCTV performance (night/fog/range,
  fog-lensed domes, actual plates). These are public CCTV-perspective proxies, one
  per case — the standing external ask in
  [field-validation-kit.md](field-validation-kit.md).

Clips are royalty-free (Mixkit, no attribution required) and are **not committed**
(gitignored); `scripts/fetch_border_case_clips.py` re-fetches them.

Related: [field-validation-kit.md](field-validation-kit.md),
[segmentation.md](segmentation.md), [anpr.md](anpr.md),
[benchmark-matrix.md](benchmark-matrix.md).

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

| Case (CCTV clip) | Detection | Tracking (person) | ANPR | SAM 2 masks | Face |
|------------------|-----------|-------------------|------|-------------|------|
| **Line crossing** — high-angle junction | person ×2549, car ×270, bus ×33, truck ×32, bike ×22, moto ×18 | **335 tracks (178 concurrent)** | 0 | 23 (~149 ms) | 56 |
| **Crowd / group** — high-angle busy street | person ×1396, car ×732, bus ×36, truck ×34 | 183 tracks (107 concurrent) | 0 | 16 (~143 ms) | 21 |
| **Night movement** — low-light street | person ×1651, bike ×94, moto ×31, car ×3 | 211 tracks (98 concurrent) | 0 | 18 (~142 ms) | 38 |
| **Vehicle / traffic** — high-angle road | car ×38, person ×32 | 3 tracks | 0 | 4 (~165 ms) | 0 |
| **Animal / livestock** — herd | **cattle ×120 (0 person)** | — | — | — | — |

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
- **ANPR reads 0** on every clip: a fixed wide CCTV view has **no close, frontal,
  legible plate**, which is exactly when Prahari's capability certificate
  **declines** to read — the design working, not a failure. The real ANPR proof is
  the close-plate **Awiros Indian-plate 4/4** (see benchmark-matrix). Point this
  same harness at a gate/chokepoint clip with a legible plate and it reports the read.
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

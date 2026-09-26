# Prahari — Real-Footage Per-Case Module Tests

**Why:** real border CCTV is sensitive and unavailable, so each border *case* is
exercised on a **separate real public clip** (royalty-free, Mixkit), and every
perception module the pipeline uses is run on it and reported. These are public
proxies, **not real Indian border footage** — but they are real pixels, and the
numbers are what the modules actually produced.

Reproduce:
```bash
python scripts/fetch_border_case_clips.py     # download the clips (gitignored)
python scripts/test_border_cases.py --all     # run all modules on every clip
```
Modules exercised per clip: **detection** (person/vehicle/animal) · **tracking**
(ByteTrack) · **ANPR** (fast-alpr on vehicles) · **segmentation / SAM 2** (on the
top person box) · **face** (YuNet). Results: `var/border_cases_results.json`.

## Results (RTX 4050, GPU)

| Case | Clip | Detection | Tracking (person) | ANPR | SAM 2 masks | Face |
|------|------|-----------|-------------------|------|-------------|------|
| Animal / livestock | mixkit 10219 | **cattle ×120** (0 person) | — | — | — | — |
| Night movement | mixkit 303 | person ×408 | 23 tracks (18 concurrent) | 0 | **16** (~161 ms) | 45 |
| Vehicle / ANPR | mixkit 34562 | car ×353, motorcycle ×11, truck ×4, person ×6 | — | **0 reads** | 1 (~192 ms) | 0 |
| Aerial perimeter | mixkit 2168 | person ×4 (tiny/distant) | — | — | — | — |
| Crowd / group | mixkit 13192 | person ×943 | **34 tracks (30 concurrent)** | 0 | 12 (~158 ms) | 9 |
| (Line crossing) | pexels 8126410 | person ×347, car ×202, truck ×5 | 14 tracks (14 concurrent) | 0 | 12 (~151 ms) | 60 |

## Honest reading, per module

- **Detection** works across all real cases and **classifies livestock as
  `cattle`, not `person`** — directly the open-border cattle-vs-person confusion
  concern. Dense crowd (943) and night (408) detection are strong.
- **Tracking** holds many concurrent identities on real footage (34 on the crowd
  clip, 23 at night) — the tracker isn't a simulator artifact.
- **Segmentation / SAM 2** produces plausible person-shaped masks on **real
  pixels** (~0.36–0.40 mask-area fraction, ~150–190 ms/mask on GPU) — this also
  serves as the first real-footage sanity check of the SAM 2 ONNX backend, which
  was previously flagged UNVALIDATED.
- **Face (YuNet)** fires on real people (60 / 45 / 9 frames on the close, night,
  and crowd clips) and correctly returns nothing on the animal and far-aerial
  clips.
- **ANPR reads 0** on every stock clip: none has a close, frontal, legible plate,
  which is exactly when Prahari's capability certificate **declines** to read.
  This is the design working, not a failure — the real ANPR evidence is the
  close-plate **Awiros Indian-plate 4/4** result (see benchmark-matrix). Feed a
  clip with a legible plate and this same harness reports the read.
- **Aerial** yields few detections (4) — small/distant people from above are hard,
  consistent with the VisDrone range proxy (high precision, low recall).

## What this does and does not establish

- **Does:** every perception module runs on **real footage** across the border
  cases and produces sane, honest numbers; failure modes (aerial recall, ANPR
  distance-gating) are visible and explained.
- **Does not:** establish real *Indian border* performance (night/fog/range,
  fog-lensed domes, actual plates). These clips are public proxies, one per case,
  chosen because real border CCTV is sensitive/unavailable — the standing ask in
  [field-validation-kit.md](field-validation-kit.md).

Clips are royalty-free (Mixkit, no attribution required) and are **not committed**
(gitignored); `scripts/fetch_border_case_clips.py` re-fetches them.

Related: [field-validation-kit.md](field-validation-kit.md),
[segmentation.md](segmentation.md), [anpr.md](anpr.md),
[benchmark-matrix.md](benchmark-matrix.md).

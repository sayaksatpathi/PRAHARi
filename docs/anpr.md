# ANPR — plate reading, gated by measured capability

Plate reading is commodity. Every video analytics product offers it. What almost
none of them will tell you is **where in a given camera's frame it can actually
be trusted** — and that is the only part of this module worth defending.

## Two gates, both ahead of any model call

**1. Capability.** The camera's certificate must grant ANPR. It is refused for
distinct, stated reasons:

| Camera | Verdict | Reason |
|---|---|---|
| Gate, 28° FOV, tilted down | **granted** | 250 px/m reached below image row 525 — 27% of the frame qualifies |
| Perimeter dome, 62° FOV | refused | not applicable to a perimeter camera; this role's analytics set does not include it |
| Night approach camera | refused | needs 250 px/m (identify); this camera peaks at 93 px/m (observe) |
| Thermal perimeter | refused | thermal sensor: renders no printed detail regardless of pixel density |
| Legacy analogue | refused | effective resolution below the floor for fine-detail analytics |

**2. Region.** The grant carries the image band where the *measured* scale
reaches 250 px/m — the IEC 62676-4 *identify* threshold. A vehicle outside that
band is not read.

This second gate is the one that matters, and it is not the same as running the
model everywhere and filtering low-confidence results afterwards. OCR confidence
on a too-small plate is **not calibrated**, so the bad reads do not reliably
announce themselves. Refusing to look is more honest than looking and hoping the
confidence number saves you.

### What it looks like in practice

A traced approach on the certified gate camera, one row per few frames:

```
  z(m)  foot_y  in_region  plate_w  read
  15.9  0.416   False           80  -
  13.4  0.542   False           96  -
  11.7  0.656   False          109  -
  10.9  0.726   False          118  -
  10.2  0.790   True           126  WB 24 AB 1234   (conf 0.95)
```

Note the fourth row. The plate is plainly visible and **118 px wide**, and the
system still refuses to read it, because the vehicle has not yet crossed into the
band where the camera's measured geometry supports a dependable read. Three
frames later it does, and the read succeeds.

That refusal is the feature.

## Backends

Same pattern as the object detectors: a real ONNX implementation, and a clearly
labelled synthetic one, selected by **what kind of imagery the source produces**
rather than by a global setting.

| Backend | Used for | Licence |
|---|---|---|
| [fast-alpr](https://github.com/ankandrew/fast-alpr) | real camera and file sources | MIT |
| `synthetic-anpr (SIMULATED)` | simulated sources | — |

fast-alpr is ONNX end to end — plate detection and OCR both — so it adds a model
rather than a framework. No torch, and the same execution-provider selection as
the object detector. Models are ~12 MB and fetched on first use.

### Why the source decides, and not a setting

Verified during integration: **fast-alpr loads and runs on simulated frames and
returns no detection.** That is correct behaviour, not a bug. A model trained on
photographs finds nothing in a flat white rectangle with vector-drawn glyphs,
because that is not a photograph of a number plate.

So preferring the real backend on synthetic imagery would produce a *silently
dead analytic that looks correctly configured* — the worst possible outcome for a
demonstration. Synthetic imagery therefore gets the synthetic reader, and real
imagery gets the real model. Both are gated identically, so the architecture on
show is the one that would run in the field.

**The direct consequence:** the real ANPR backend's accuracy is *not*
demonstrated by anything in this repository. It is wired, it loads, it runs at
~115 ms per vehicle crop on CPU, and its correctness can only be shown on real
footage. That is a reason to prioritise real-footage validation, not a reason to
claim more than has been shown.

### The synthetic reader does not cheat

It refuses to read a plate the frame does not physically render legibly. The
simulator only draws plate glyphs above a pixel width; below that the reader
returns nothing, exactly as real OCR would fail. A test asserts this.

A synthetic reader that returned the correct plate regardless of legibility would
make every claim about capability gating untestable theatre.

## Normalisation

Reads are canonicalised against the Indian civilian format (`WB 24 AB 1234`),
deliberately permissive about spacing and the number of series letters, which
vary by state and vintage. A read that does not fit the format is **kept but
flagged invalid** rather than discarded or silently reshaped into something that
looks right.

## Repeat-entity intelligence

The highest-value signal on an open border, and the one analytic here that
genuinely needs plate *reading* rather than mere vehicle detection.

On a treaty-open frontier a single crossing is unremarkable. The same vehicle
crossing repeatedly at 02:00, or appearing at several separate unofficial routes
within a week, is a logistics pattern rather than a commuter. `RepeatPlateTracker`
flags both, and emits a `REPEAT_ENTITY` event.

Two deliberate restrictions:

- **Only dependable reads feed the pattern.** A read below the pixel threshold,
  or one that failed format validation, is excluded. A miscorrected plate would
  otherwise create a phantom vehicle and — far worse — could attach a movement
  pattern to a real registration that never made those crossings.
- **Counts and timestamps only.** The tracker records that a registration was
  seen by a camera at a time. It holds no imagery, no trajectory and no identity,
  and it is not a movement profile of a person. See [privacy.md](privacy.md).

Sightings outside the configured window are forgotten rather than accumulating
indefinitely.

## Cost

Plate reading is the most expensive model on the node, so:

- it runs only on tracks classified as vehicles;
- only inside the certified region;
- at most once every `ANPR_ATTEMPT_INTERVAL` frames per track, because a plate
  does not change between frames;
- and it stops attempting a track once a good read is obtained.

Models are loaded lazily, on the first camera that actually earns the grant.
Loading them on a node where no camera is certified would spend memory and
startup time on an analytic that can never legally run.

## Configuration

```
PRAHARI_ANPR_BACKEND=auto          # auto | fast_alpr | synthetic
PRAHARI_ANPR_REPEAT_WINDOW_HOURS=168
PRAHARI_ANPR_REPEAT_MIN_SIGHTINGS=3
```

`GET /api/anpr/plates` returns the recent reads and the plate history held on
this node.

## Limitations

- **Not validated on real plates.** See above — this is the significant one.
- The format normaliser is Indian-civilian only. Military, diplomatic, BH-series
  and commercial formats are not handled, and a valid plate in those formats will
  be reported as format-invalid and excluded from the repeat-entity signal.
- No plate is matched against any watchlist, and no such capability exists here.
  That is a deliberate boundary, not an unimplemented feature.
- Angle is not compensated. A plate seen sharply off-axis will read poorly, and
  the pixel-width gate does not account for foreshortening.
- Plates read on simulated imagery are **synthetic demonstration data and are not
  real vehicle registrations**, and are labelled as such in every API response.

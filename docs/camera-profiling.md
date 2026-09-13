# Camera capability profiling

The claim this module has to earn: *Prahari treats cameras differently because it
knows what they can actually do.* A configuration form where an operator types in
"1080p" does not earn it. This does.

## The problem

A border outpost's cameras were installed over a decade by different people to
different standards. A ten-year-old dome that reports 1920×1080 over ONVIF may,
after lens fog, focus drift, a failing IR illuminator and aggressive
re-encoding onto a saturated backhaul, deliver the usable detail of a 480p
camera pointed at the wrong thing.

Assigning that camera face analytics because its datasheet says 1080p is how
video analytics projects lose operator trust in the first week.

## What is measured

All of it from the pixels, in `profiling/measure.py`.

**Effective resolution.** Halve the image and scale it back up; whatever the
round trip destroyed was genuine high-frequency detail. A truly sharp frame loses
a lot; an already-soft frame merely *stored* at 1080p loses almost nothing,
because the detail was never there. The ratio of residual to total energy
estimates effective — as opposed to claimed — resolution.

Measured on a lightly median-filtered copy. Without that, a noisy night camera
scores *higher* than a crisp daylight one, because sensor noise is
high-frequency content too. Grain is not resolution, and a profiler that cannot
tell the difference will hand fine-detail analytics to the worst camera on site.
(This was a real bug, found by the profiling smoke test.)

**Delivered frame rate**, from actual inter-frame timestamps rather than what the
camera claims. A 25 fps camera delivering 6 fps is a different camera.

**Sensor noise floor.** Difference consecutive frames — removing the static scene
— and take a low percentile of the magnitude, discarding moving objects. What
remains is the noise floor, which is what rises sharply when a camera ramps gain
at night.

**Block-artifact severity.** Compare mean absolute gradient across the 8-pixel
block grid against the gradient everywhere else. Heavy recompression makes
boundary gradients stand out; clean video shows no preference for the grid.

**Scene geometry** — the interesting one.

## Ground-plane self-calibration

No survey. No calibration target. No operator drawing reference lines. The
geometry is recovered from people who happen to walk through the scene.

For a level camera at height *h*, a person of height *H* standing at depth *Z*
projects with pixel height `ph = f·H/Z`, and their feet land at
`v_foot = cy + f·h/Z`. Eliminating *Z*:

```
ph = (H / h) · (v_foot − cy)
```

A straight line through the horizon. Fitting it over many observed people
recovers **both** the horizon row (the x-intercept) and the camera height (from
the slope, given a stature prior). Pixels per metre at any row then follows as
`(v − cy) / h`.

The fit uses **Theil-Sen** — the median of all pairwise slopes — rather than
least squares, because the contaminating samples here are not mild noise. They
are misclassified livestock, which a squared-error fit chases hard. Boxes clipped
by the frame edge are rejected before they reach the fit (their apparent height
is wrong), as are boxes the detector did not actually observe this frame.

**Verified two ways.** `scripts/smoke_profiling.py` feeds clean ground truth and
recovers every camera's mounting height to **0.0%**. More usefully, the same
comparison against the *live* pipeline — with a noisy detector, class confusion
and misclassified livestock in the samples — gives:

| Camera | True height | Recovered | Error |
|---|---|---|---|
| Gate, narrow FOV | 3.0 m | 3.02 m | 0.5% |
| Perimeter dome, aged | 6.0 m | 6.09 m | 1.4% |
| Approach, night IR | 5.0 m | 5.02 m | 0.4% |
| Thermal perimeter | 6.0 m | 6.13 m | 2.2% |
| Legacy analogue, degraded | 4.5 m | 4.64 m | 3.2% |

Getting from the first number to the second took four separate fixes, each of
which produced a *confidently wrong* answer rather than an obviously broken one —
which is the failure mode worth guarding against here:

1. **Least squares chased misclassified cattle.** On the thermal camera, where a
   person and a bullock are both a warm white blob, it reported an implied
   mounting height of 14.5 m against a true 6.0 m. Replaced with **Theil-Sen**
   (median of pairwise slopes), which tolerates ~29% contamination.
2. **A tight aspect-ratio pre-filter made things worse, not better.** Box aspect
   is perturbed by the same jitter that perturbs box height, so filtering on it
   preferentially kept the samples jitter had made taller — a selection bias that
   under-estimated height by 20-50%. The bounds are now loose enough to reject
   only shapes no upright human can produce.
3. **Samples clustered at one depth.** The fit extrapolates a line back to the
   horizon, so it needs depth spread; 198 tightly-clustered samples produced an
   implied height of 37 m against a true 5 m. There is now a leverage guard, on
   percentiles rather than min/max, that **refuses to fit** and says why.
4. **Predicted boxes were being used as observations.** A track the detector
   missed is coasted forward on a constant-velocity estimate; feeding those into
   a fit that reads box height as a distance measurement injects the tracker's
   drift into the camera's geometry. Calibration now uses only boxes actually
   observed this frame.

**Caveat, stated in the code and here.** The only assumption is a 1.70 m mean
stature. Errors there scale the metric output linearly, which is why speeds are
reported as estimates rather than measurements.

## From metres to permissions

Pixels per metre is exactly the quantity **IEC 62676-4**'s DORI bands are defined
against:

| Band | px/m | Means |
|---|---|---|
| Monitor | 12 | something is there |
| Detect | 25 | a person is present |
| Observe | 62 | characteristic details |
| Recognise | 125 | a known individual, perhaps |
| Identify | 250 | courtroom-grade identification |

Each analytic declares the band it needs (`profiling/certificate.py`). ANPR sits
at **identify**, because a ~500 mm Indian plate needs on the order of 120 px
across for dependable OCR, which is 250 px/m almost exactly. Granting ANPR at
*recognise* would produce reads that look confident and are wrong — worse than no
read at all.

## Grants are spatial

The point most systems miss. Scale grows linearly down the frame from the
horizon, so a camera's near field routinely supports plate reading while its far
field barely supports noticing a person is present. Granting or refusing an
analytic for the *whole camera* throws that away.

Each grant therefore carries the image region where it actually holds:

> `anpr` — **granted**: *250 px/m reached below image row 564 — 22% of the frame
> qualifies.*

## Quality gates beyond pixel density

Pixels on target are necessary but not sufficient. A large, sharp-looking but
block-damaged plate still will not OCR. So:

- Effective-resolution floors, **separately for coarse and fine analytics**.
  Noticing a human silhouette survives a remarkably soft image — that is the
  premise of the DETECT band sitting at 25 px/m. Reading six characters does not.
  One shared floor refused person detection on a serviceable perimeter dome,
  which would have disabled the most important analytic on the most common camera
  at the site. (Also found by the smoke test.)
- Blockiness and noise ceilings for fine-detail analytics only.
- A minimum frame rate before speed estimation is offered.
- Thermal sensors are refused ANPR and face detection outright, at any pixel
  density: thermal renders no printed or facial detail.

## Role relevance

Before quality is considered at all, a role filter. A perimeter camera watching a
ridge line has no business attempting ANPR however good its optics are; a gate
camera does not need boat detection. Refusals for this reason say so plainly
rather than inventing a technical excuse.

## Face recognition

Never granted automatically, under any measurement, on any camera. The refusal
reason states that it is product policy rather than capability:

> Identity recognition carries legal, privacy and authorisation requirements that
> a video analytics layer cannot satisfy on its own.

This is a deliberate product decision, and our own profiling supports it: almost
no perimeter camera reaches the identify band anywhere in frame.

## The certificate

A versioned, digest-sealed document per camera: the measurements, every grant and
refusal with its reason, the achieved DORI band, and a SHA-256 over the body. An
auditor can confirm the capability statement a camera was operating under has not
been edited after the fact — and a test proves the digest catches a quietly
flipped grant.

Re-profiling (`POST /api/cameras/{id}/profile`) discards the certificate and
returns the camera to `profiling`, suspending everything but person detection
until a new one is issued.

## Known limitations

- The tilt model is first-order. It holds well for the moderate downtilts typical
  of fixed border cameras and would need a full rotation matrix for steeply
  tilted or rolled installations.
- Lens distortion is not modelled. A strongly barrel-distorted wide-angle camera
  will have its far-field scale underestimated.
- The fit needs pedestrians. A camera watching an empty stretch of fence will
  eventually be certified without geometry, honestly labelled, running nothing
  but person detection — which is the correct outcome but a limited one.
- PTZ cameras are not handled: a camera that moves invalidates its own
  calibration, and re-profiling on every preset change is designed for, not built.

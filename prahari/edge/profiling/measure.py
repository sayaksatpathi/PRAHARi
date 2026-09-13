"""Measured camera profiling.

The difference between a configuration form and a capability profile is that
this module does not ask the camera what it can do - it measures. A ten-year-old
dome that reports 1080p over ONVIF may, after lens fog, focus drift and
aggressive re-encoding, deliver the usable detail of a 480p camera. Assigning it
face analytics because its datasheet says 1080p is how video analytics projects
lose operator trust.

Three things get measured here:

1.  Optical quality  - sharpness, effective resolution, sensor noise floor and
    compression damage, from the pixels themselves.
2.  Temporal quality - the frame rate actually delivered, versus claimed.
3.  Scene geometry   - the ground plane, recovered automatically from the
    bounding boxes of people walking through the scene. No survey, no manual
    calibration, no operator drawing reference lines.

The geometry step is what converts image pixels into pixels-per-metre, which is
the quantity IEC 62676-4's DORI bands are defined against, and therefore the
quantity that decides which analytics a camera has any business running.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from prahari.common.models import CameraMeasurement

log = logging.getLogger("prahari.profiling")

# Anthropometric prior. Mean adult standing height is the only assumption the
# self-calibration needs, and errors here scale the metric output linearly -
# which is why speed estimates are reported as estimates, not measurements.
ASSUMED_PERSON_HEIGHT_M = 1.70

# Height/width bounds for a bounding box to be accepted as an upright person for
# calibration. A standing adult is roughly 2.5-3.5x taller than wide; cattle sit
# near 0.7, and a misclassified animal corrupts the fit far more than it helps.
# Deliberately generous at the upper end to keep genuinely narrow distant boxes.
# Deliberately loose. A tight window around the person mean (~3.0) looked more
# precise and was actively harmful: box aspect is perturbed by the same detector
# jitter that perturbs box height, so rejecting on aspect preferentially keeps
# the samples jitter made taller, biasing the fitted slope and *under*-estimating
# camera height by 20-50%. Filtering on a quantity correlated with the noise in
# the fitted variable is a selection bias, not a filter. These bounds reject only
# shapes no upright human can produce - a bullock at 0.7 - and leave the rest to
# the robust estimator below.
PERSON_ASPECT_MIN = 1.15
PERSON_ASPECT_MAX = 9.0


def _theil_sen(x: np.ndarray, y: np.ndarray,
               max_pairs: int = 20000) -> tuple[float, float]:
    """Median-of-pairwise-slopes regression. Robust to heavy contamination."""
    n = len(x)
    if n < 2:
        return 0.0, 0.0

    i, j = np.triu_indices(n, k=1)
    if len(i) > max_pairs:
        # Subsample pairs on a fixed seed so profiling stays reproducible.
        rng = np.random.default_rng(97)
        pick = rng.choice(len(i), size=max_pairs, replace=False)
        i, j = i[pick], j[pick]

    dx = x[j] - x[i]
    usable = np.abs(dx) > 1e-6
    if not usable.any():
        return 0.0, 0.0
    slopes = (y[j][usable] - y[i][usable]) / dx[usable]
    slope = float(np.median(slopes))
    intercept = float(np.median(y - slope * x))
    return slope, intercept


@dataclass
class _GroundSample:
    foot_v: float        # image row where the person meets the ground
    px_height: float     # their height in pixels


@dataclass
class ProfilingAccumulator:
    """Rolling state for one camera's profiling run."""
    frames: int = 0
    lumas: list[float] = field(default_factory=list)
    sharpness: list[float] = field(default_factory=list)
    eff_res: list[float] = field(default_factory=list)
    blockiness: list[float] = field(default_factory=list)
    noise: list[float] = field(default_factory=list)
    frame_times: list[float] = field(default_factory=list)
    ground: list[_GroundSample] = field(default_factory=list)
    width: int = 0
    height: int = 0
    _prev_grey: np.ndarray | None = None


def variance_of_laplacian(grey: np.ndarray) -> float:
    """Classic focus measure. Higher means crisper edges."""
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def effective_resolution_factor(grey: np.ndarray) -> float:
    """How much real detail survives, as a fraction of the nominal pixel count.

    Method: halve the image and scale it back up. Whatever the original had that
    the round trip destroyed was genuine high-frequency detail. A truly sharp
    1080p frame loses a lot in that round trip; an already-soft frame that is
    merely *stored* at 1080p loses almost nothing, because the detail was never
    there. The ratio of residual energy to total energy is therefore a direct
    estimate of effective, as opposed to claimed, resolution.

    The measurement is taken on a lightly median-filtered copy. Without that, a
    noisy night camera scores *higher* than a crisp daylight one, because sensor
    noise is high-frequency content and the round trip destroys it just as
    readily as real detail. Grain is not resolution, and a profiler that cannot
    tell the difference will hand fine-detail analytics to the worst camera on
    the site.
    """
    h, w = grey.shape[:2]
    if h < 16 or w < 16:
        return 0.0
    denoised = cv2.medianBlur(grey, 3)
    small = cv2.resize(denoised, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    back = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    residual = cv2.absdiff(denoised, back).astype(np.float32)
    denom = float(denoised.astype(np.float32).std()) + 1e-6
    return float(np.clip(residual.std() / denom, 0.0, 1.0))


def blockiness_score(grey: np.ndarray) -> float:
    """JPEG/H.264 block-artifact severity on the 8x8 grid.

    Compares the mean absolute gradient across 8-pixel block boundaries with the
    gradient everywhere else. Heavy recompression makes boundary gradients stand
    out; clean video shows no preference for the grid.
    """
    g = grey.astype(np.float32)
    if g.shape[0] < 24 or g.shape[1] < 24:
        return 0.0
    dh = np.abs(np.diff(g, axis=1))
    cols = np.arange(dh.shape[1])
    on_grid = dh[:, (cols % 8) == 7]
    off_grid = dh[:, (cols % 8) != 7]
    if off_grid.size == 0 or on_grid.size == 0:
        return 0.0
    ratio = float(on_grid.mean()) / (float(off_grid.mean()) + 1e-6)
    # ratio 1.0 means no grid preference at all. Heavy recompression pushes it
    # well past 2, so the excess is divided by 3 before clipping - clipping at
    # (ratio - 1) saturated every damaged camera at exactly 1.000 and threw away
    # the ordering between "compressed" and "destroyed".
    return float(np.clip((ratio - 1.0) / 3.0, 0.0, 1.0))


def temporal_noise_sigma(prev_grey: np.ndarray, grey: np.ndarray) -> float:
    """Sensor noise floor, estimated from the quietest part of the frame.

    Differencing consecutive frames removes the static scene and leaves noise
    plus motion. Taking a low percentile of the difference magnitude discards
    the moving objects, leaving an estimate of the noise floor - which is what
    rises sharply when a camera ramps sensor gain at night.
    """
    if prev_grey is None or prev_grey.shape != grey.shape:
        return 0.0
    diff = cv2.absdiff(grey, prev_grey).astype(np.float32)
    quiet = np.percentile(diff, 60)
    # Differencing two independent noisy frames inflates sigma by sqrt(2).
    return float(quiet / math.sqrt(2.0))


class CameraProfiler:
    """Accumulates observations and emits a CameraMeasurement."""

    def __init__(
        self,
        camera_id: str,
        min_frames: int = 30,
        min_ground_samples: int = 28,
        max_frames: int | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.min_frames = min_frames
        self.min_ground_samples = min_ground_samples
        # Upper bound on how long to hold out for foot traffic before issuing a
        # limited certificate anyway. A camera watching an empty stretch of fence
        # may legitimately see nobody for a long time, and refusing to certify it
        # at all would leave it running no analytics whatsoever - worse than
        # certifying it honestly as geometry-unknown.
        self.max_frames = max_frames or min_frames * 15
        self.acc = ProfilingAccumulator()

    # -- ingest ---------------------------------------------------------
    def observe_frame(self, image: np.ndarray, timestamp_s: float) -> None:
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        a = self.acc
        a.frames += 1
        a.height, a.width = grey.shape[:2]
        a.lumas.append(float(grey.mean()))
        a.sharpness.append(variance_of_laplacian(grey))
        a.eff_res.append(effective_resolution_factor(grey))
        a.blockiness.append(blockiness_score(grey))
        a.frame_times.append(timestamp_s)
        if a._prev_grey is not None:
            a.noise.append(temporal_noise_sigma(a._prev_grey, grey))
        a._prev_grey = grey

    def observe_person(self, foot_v: float, px_height: float,
                       px_width: float | None = None) -> None:
        """Feed one observed person bounding box into the ground-plane fit.

        Boxes clipped by the frame edge are useless here because their apparent
        height is wrong; the caller filters those.

        The aspect check is not a nicety. The fit assumes every sample is a
        1.7 m upright human, and a detector will sometimes hand it a cow -
        especially on a thermal camera, where a person and a bullock are both
        just a warm white blob. Livestock is short and wide, so those samples
        drag the fitted slope down and inflate the implied camera height. In
        testing, unfiltered cattle on the thermal perimeter camera produced an
        implied mounting height of 14.5 m against a true 6.0 m - a confidently
        reported, badly wrong number, which is worse than no number, because
        every pixels-on-target figure downstream inherits it.
        """
        if px_height < 8.0:
            return
        if px_width is not None and px_width > 0:
            aspect = px_height / px_width
            if not (PERSON_ASPECT_MIN <= aspect <= PERSON_ASPECT_MAX):
                return
        self.acc.ground.append(_GroundSample(foot_v=foot_v, px_height=px_height))

    @property
    def ready(self) -> bool:
        """Ready only once the geometry can actually be fitted.

        Frame count alone is the wrong test. Optical measurements converge in a
        second or two, but the ground-plane fit needs people to walk through the
        scene, and certifying on frame count alone produced certificates with no
        geometry and therefore no granted analytics at all - a camera that had
        been "profiled" into uselessness.
        """
        if self.acc.frames < self.min_frames:
            return False
        if len(self.acc.ground) >= self.min_ground_samples and self._has_leverage():
            return True
        # Deadline: certify even without a usable fit rather than run no analytics
        # at all. The measurement will honestly report that geometry is unknown.
        return self.acc.frames >= self.max_frames

    def _has_leverage(self) -> bool:
        """Do the accumulated samples span enough depth to fit a horizon?

        Sample count alone is the wrong readiness test. A camera with heavy foot
        traffic accumulates dozens of observations in the first few seconds, but
        if they are all at similar depths they all land at nearly the same image
        row and the same apparent size, and extrapolating a horizon from that
        magnifies small errors enormously. Declaring "ready" on count then handed
        the fit a cluster it correctly rejected, and the camera was certified with
        no geometry. Readiness must wait for spread, not just for numbers. The
        thresholds mirror the leverage guard in the fit itself, so the two never
        disagree.
        """
        ground = self.acc.ground
        if len(ground) < self.min_ground_samples:
            return False
        v = np.array([s.foot_v for s in ground], dtype=np.float64)
        ph = np.array([s.px_height for s in ground], dtype=np.float64)
        v_lo, v_hi = np.percentile(v, [5, 95])
        ph_lo, ph_hi = np.percentile(ph, [5, 95])
        img_h = float(self.acc.height or 1)
        return (v_hi - v_lo) >= 0.10 * img_h and (ph_hi / max(ph_lo, 1e-6)) >= 1.6

    @property
    def progress(self) -> dict[str, float]:
        return {
            "frames": self.acc.frames,
            "frames_required": self.min_frames,
            "ground_samples": len(self.acc.ground),
            "ground_samples_required": self.min_ground_samples,
            "deadline_frames": self.max_frames,
        }

    # -- geometry -------------------------------------------------------
    def _fit_ground_plane(self) -> tuple[bool, float | None, float, float, list[str]]:
        """Recover the horizon and the pixels-per-metre scale from observed people.

        For a level camera at height h, a person of height H standing at depth Z
        projects with pixel height  ph = f*H/Z, while their feet land at
        v_foot = cy + f*h/Z. Eliminating Z gives

            ph = (H/h) * (v_foot - cy)

        which is a straight line through the horizon. Fitting that line over many
        observed people therefore recovers both the horizon row (the x-intercept)
        and the camera height (from the slope, given the height prior) without
        anyone surveying anything. Pixels per metre at row v then follows as
        (v - cy) / h.
        """
        notes: list[str] = []
        samples = self.acc.ground
        if len(samples) < max(8, self.min_ground_samples // 3):
            notes.append(
                f"ground plane not estimated: {len(samples)} person observations "
                f"(need 8); metric speed and DORI banding unavailable"
            )
            return False, None, 0.0, 0.0, notes

        # Fit quality is reported, not just fit success. A line through a dozen
        # points can be fitted perfectly and still be wrong by a third, and every
        # pixels-on-target figure downstream inherits that error - so the
        # certificate says how much evidence it rests on.

        v = np.array([s.foot_v for s in samples], dtype=np.float64)
        ph = np.array([s.px_height for s in samples], dtype=np.float64)

        # Leverage check. The fit extrapolates a line back to the horizon, so it
        # needs observations spread over depth. People seen only within a narrow
        # band all land at nearly the same image row and the same apparent size,
        # and extrapolating a horizon from that magnifies small errors enormously
        # - in testing a camera with 198 tightly-clustered samples reported an
        # implied mounting height of 37 m against a true 5 m, with no indication
        # anything was wrong. Sample count is not evidence; spread is.
        # Percentiles, not min/max. A handful of outliers at unusual depths will
        # stretch a min/max span past any threshold while the bulk of the samples
        # stay tightly clustered - so the guard passes and the fit is still
        # determined by a cluster with no leverage. The central 90% is what the
        # regression actually rests on.
        v_lo, v_hi = np.percentile(v, [5, 95])
        ph_lo, ph_hi = np.percentile(ph, [5, 95])
        v_span = float(v_hi - v_lo)
        size_ratio = float(ph_hi / max(ph_lo, 1e-6))
        img_h = float(self.acc.height or 1)
        if v_span < 0.10 * img_h or size_ratio < 1.6:
            notes.append(
                f"ground plane not estimated: {len(samples)} observations span only "
                f"{v_span:.0f} image rows ({v_span / img_h * 100:.0f}% of frame) with a "
                f"{size_ratio:.2f}x size range - too little depth variation to place "
                f"the horizon. Metric output would be unreliable, so none is offered"
            )
            return False, None, 0.0, 0.0, notes

        # Theil-Sen: the median of all pairwise slopes, then the median
        # intercept. Chosen over least squares because the contaminating samples
        # here are not mild noise - they are misclassified livestock, which a
        # squared-error fit chases hard. On the thermal camera, where a person
        # and a bullock are both a warm white blob, least squares reported an
        # implied mounting height of 14.5 m against a true 6.0 m. Theil-Sen
        # tolerates roughly 29% contamination before breaking down and needs no
        # threshold to be tuned.
        slope, intercept = _theil_sen(v, ph)

        if slope <= 1e-6:
            notes.append("ground plane fit rejected: non-physical slope "
                         "(camera may be inverted, tilted or looking at a wall)")
            return False, None, 0.0, 0.0, notes

        horizon_y = -intercept / slope
        camera_height_m = ASSUMED_PERSON_HEIGHT_M / slope

        if not (0.8 <= camera_height_m <= 40.0):
            notes.append(
                f"ground plane fit rejected: implied camera height "
                f"{camera_height_m:.1f} m is outside the plausible range"
            )
            return False, None, 0.0, 0.0, notes

        # px/m at the bottom row (nearest ground visible) and at 15% down from
        # the horizon (a representative far-field row).
        h_img = self.acc.height or 1
        def ppm(row: float) -> float:
            return max(0.0, (row - horizon_y) / camera_height_m)

        near = ppm(h_img - 1)
        far_row = horizon_y + 0.15 * (h_img - horizon_y)
        far = ppm(far_row)

        confidence = ("well constrained" if len(samples) >= 60
                      else "moderately constrained" if len(samples) >= 25
                      else "weakly constrained - treat metric output as indicative")
        notes.append(
            f"ground plane self-calibrated from {len(samples)} person observations "
            f"({confidence}): horizon row {horizon_y:.0f}, implied camera height "
            f"{camera_height_m:.1f} m (assumes {ASSUMED_PERSON_HEIGHT_M} m mean stature)"
        )
        return True, horizon_y, near, far, notes

    # -- output ---------------------------------------------------------
    def build(self, claimed_fps: float) -> CameraMeasurement:
        a = self.acc
        notes: list[str] = []

        measured_fps = 0.0
        if len(a.frame_times) >= 3:
            span = a.frame_times[-1] - a.frame_times[0]
            if span > 1e-6:
                measured_fps = (len(a.frame_times) - 1) / span

        mean_luma = float(np.mean(a.lumas)) if a.lumas else 0.0
        sharp = float(np.median(a.sharpness)) if a.sharpness else 0.0
        eff_res = float(np.median(a.eff_res)) if a.eff_res else 0.0
        block = float(np.median(a.blockiness)) if a.blockiness else 0.0
        noise = float(np.median(a.noise)) if a.noise else 0.0

        # 55/255 rather than 70: a normally-exposed daylight scene under a dull
        # sky sits in the high 60s, and flagging that as night made every camera
        # on site permanently "low light", which drains the flag of meaning.
        is_low_light = mean_luma < 55.0

        if claimed_fps > 0 and measured_fps > 0:
            deficit = 1.0 - (measured_fps / claimed_fps)
            if deficit > 0.25:
                notes.append(
                    f"delivers {measured_fps:.1f} fps against {claimed_fps:.0f} fps "
                    f"claimed ({deficit * 100:.0f}% short) - check backhaul or "
                    f"encoder load before trusting speed estimates"
                )
        if eff_res < 0.06:
            notes.append(
                f"effective resolution factor {eff_res:.3f}: the image carries far "
                f"less detail than its {a.width}x{a.height} frame size implies - "
                f"likely defocus, lens contamination or upscaling"
            )
        if block > 0.35:
            notes.append(
                f"heavy block artifacts (score {block:.2f}): bitrate is too low for "
                f"this scene; fine-detail analytics will be unreliable"
            )
        if noise > 8.0:
            notes.append(
                f"high sensor noise floor (sigma {noise:.1f}): typical of night gain; "
                f"expect elevated false positives without thermal or better lighting"
            )
        if is_low_light:
            notes.append(f"low-light scene (mean luma {mean_luma:.0f}/255)")

        ok, horizon, near, far, gnotes = self._fit_ground_plane()
        notes.extend(gnotes)

        return CameraMeasurement(
            frames_sampled=a.frames,
            width=a.width,
            height=a.height,
            claimed_fps=claimed_fps,
            measured_fps=round(measured_fps, 2),
            sharpness=round(sharp, 2),
            effective_resolution_factor=round(eff_res, 4),
            noise_sigma=round(noise, 2),
            compression_artifact_score=round(block, 3),
            mean_luma=round(mean_luma, 1),
            is_low_light=is_low_light,
            ground_plane_estimated=ok,
            px_per_metre_near=round(near, 2),
            px_per_metre_far=round(far, 2),
            horizon_y=round(horizon, 1) if horizon is not None else None,
            notes=notes,
        )

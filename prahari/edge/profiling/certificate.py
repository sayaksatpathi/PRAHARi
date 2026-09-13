"""Camera Capability Certificate issuance.

This module answers the question Prahari exists to answer: *given what we
measured about this camera, which analytics is it entitled to run, and where in
its frame?*

Two properties make this more than a configuration screen:

1.  Grants are derived from measurements, not from a datasheet or an operator's
    guess, and every grant and every refusal carries the measured reason.

2.  Grants are gated **spatially**. A camera's near field routinely supports
    number-plate reading while its far field barely supports noticing that a
    person is present. Granting or refusing ANPR for the whole camera throws
    that away. Each grant therefore carries the image-space region in which it
    actually holds, computed from the recovered ground plane.

Face recognition is never granted automatically, under any measurement. See
`docs/privacy.md` for why that is a deliberate product decision rather than an
unimplemented feature.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from prahari.common.models import (
    DORI,
    CameraMeasurement,
    Camera,
    CameraRole,
    Capability,
    CapabilityCertificate,
    CapabilityGrant,
    Point,
    SensorType,
)

# Minimum pixels-on-target for each analytic, expressed as a DORI band.
#
# ANPR sits at IDENTIFY because a ~500 mm Indian number plate needs on the order
# of 120 px across its width for OCR to be dependable, which is 250 px/m - the
# IDENTIFY band almost exactly. Granting ANPR at RECOGNISE would produce reads
# that look confident and are wrong, which is worse than no read at all.
CAPABILITY_DORI: dict[Capability, DORI] = {
    Capability.PERSON_DETECTION: DORI.DETECT,
    Capability.VEHICLE_DETECTION: DORI.DETECT,
    Capability.ANIMAL_DETECTION: DORI.DETECT,
    Capability.BOAT_DETECTION: DORI.DETECT,
    Capability.UAV_DETECTION: DORI.OBSERVE,
    Capability.TRACKING: DORI.DETECT,
    Capability.DIRECTION_ANALYSIS: DORI.DETECT,
    Capability.SPEED_ESTIMATION: DORI.DETECT,
    Capability.ANPR: DORI.IDENTIFY,
    Capability.FACE_DETECTION: DORI.RECOGNISE,
    Capability.FACE_RECOGNITION: DORI.IDENTIFY,
}

# Which analytics a role would even want, before quality is considered. A
# perimeter camera watching a ridge line has no business attempting ANPR however
# good its optics are, and a gate camera does not need boat detection.
ROLE_CANDIDATES: dict[CameraRole, set[Capability]] = {
    CameraRole.PERIMETER: {
        Capability.PERSON_DETECTION, Capability.VEHICLE_DETECTION,
        Capability.ANIMAL_DETECTION, Capability.UAV_DETECTION,
        Capability.TRACKING, Capability.DIRECTION_ANALYSIS,
        Capability.SPEED_ESTIMATION,
    },
    CameraRole.APPROACH: {
        Capability.PERSON_DETECTION, Capability.VEHICLE_DETECTION,
        Capability.ANIMAL_DETECTION, Capability.TRACKING,
        Capability.DIRECTION_ANALYSIS, Capability.SPEED_ESTIMATION,
        Capability.ANPR,
    },
    CameraRole.CHOKEPOINT: {
        Capability.PERSON_DETECTION, Capability.VEHICLE_DETECTION,
        Capability.TRACKING, Capability.DIRECTION_ANALYSIS,
        Capability.ANPR, Capability.FACE_DETECTION,
    },
    CameraRole.RIVERINE: {
        Capability.PERSON_DETECTION, Capability.BOAT_DETECTION,
        Capability.ANIMAL_DETECTION, Capability.TRACKING,
        Capability.DIRECTION_ANALYSIS, Capability.SPEED_ESTIMATION,
    },
}

# Quality floors below which an analytic is refused regardless of pixel density.
# Pixels on target are necessary but not sufficient: a large, sharp-looking but
# heavily block-damaged plate still will not OCR.
#
# Two separate floors, because the two jobs are not comparable. Noticing that a
# human-shaped object is present survives a remarkably soft image - which is the
# whole premise of the DETECT band sitting at 25 px/m. Reading six characters off
# a number plate does not. Applying one floor to both refused person detection on
# a serviceable perimeter dome, which would have disabled the most important
# analytic on the most common camera at the site.
MIN_EFFECTIVE_RESOLUTION_COARSE = 0.018
MIN_EFFECTIVE_RESOLUTION_FINE = 0.070
MAX_BLOCKINESS_FOR_FINE_DETAIL = 0.45
MAX_NOISE_FOR_FINE_DETAIL = 9.0
MIN_FPS_FOR_SPEED = 4.0

# Analytics that depend on resolving small printed or facial structure rather
# than on an object's silhouette.
FINE_DETAIL_CAPABILITIES = {Capability.ANPR, Capability.FACE_DETECTION}


def _row_for_px_per_metre(
    target_ppm: float, horizon_y: float, camera_height_m: float, img_h: int
) -> float | None:
    """Image row at which the scale first reaches `target_ppm`.

    Scale grows linearly down the frame from the horizon:  ppm(v) = (v - cy) / h.
    Solving for v gives the boundary; anything below that row is good enough.
    """
    v = horizon_y + target_ppm * camera_height_m
    if v >= img_h:
        return None            # the camera never achieves this scale anywhere
    return max(0.0, v)


def _full_width_band(top_row: float, img_w: int, img_h: int) -> list[Point]:
    """Normalised polygon covering the frame from `top_row` to the bottom."""
    t = max(0.0, min(1.0, top_row / max(1, img_h)))
    return [Point(x=0.0, y=t), Point(x=1.0, y=t),
            Point(x=1.0, y=1.0), Point(x=0.0, y=1.0)]


def issue_certificate(
    camera: Camera,
    measurement: CameraMeasurement,
    version: int = 1,
) -> CapabilityCertificate:
    """Turn a measurement into a signed statement of what this camera may run."""
    grants: list[CapabilityGrant] = []
    candidates = ROLE_CANDIDATES.get(camera.role, set())
    img_w = measurement.width or camera.claimed_width
    img_h = measurement.height or camera.claimed_height

    # Recover camera height from the fitted ground plane so scale can be turned
    # back into image rows.
    camera_height_m = 0.0
    if measurement.ground_plane_estimated and measurement.horizon_y is not None:
        denom = measurement.px_per_metre_near
        if denom > 1e-6:
            camera_height_m = (img_h - 1 - measurement.horizon_y) / denom

    best_ppm = measurement.px_per_metre_near
    overall = DORI.from_px_per_metre(best_ppm) if best_ppm > 0 else DORI.MONITOR

    for cap in Capability:
        required = CAPABILITY_DORI[cap]

        # --- hard product decision, before any measurement is consulted -----
        if cap is Capability.FACE_RECOGNITION:
            grants.append(CapabilityGrant(
                capability=cap, granted=False, dori_required=required,
                reason=(
                    "Face recognition is never granted automatically by Prahari. "
                    "It is withheld as a matter of product policy, not capability: "
                    "identity recognition carries legal, privacy and authorisation "
                    "requirements that a video analytics layer cannot satisfy on "
                    "its own. See docs/privacy.md."
                ),
            ))
            continue

        # --- role relevance -------------------------------------------------
        if cap not in candidates:
            grants.append(CapabilityGrant(
                capability=cap, granted=False, dori_required=required,
                reason=(
                    f"not applicable to a {camera.role.value} camera; "
                    f"this role's analytics set does not include it"
                ),
            ))
            continue

        # --- geometry known? -------------------------------------------------
        if not measurement.ground_plane_estimated or camera_height_m <= 0:
            grants.append(CapabilityGrant(
                capability=cap, granted=False, dori_required=required,
                reason=(
                    "ground plane not yet self-calibrated, so pixels-on-target "
                    "cannot be computed; let the camera observe foot traffic and "
                    "re-profile"
                ),
            ))
            continue

        assert measurement.horizon_y is not None
        boundary = _row_for_px_per_metre(
            required.px_per_metre, measurement.horizon_y, camera_height_m, img_h
        )
        achieved = DORI.from_px_per_metre(best_ppm)

        if boundary is None:
            grants.append(CapabilityGrant(
                capability=cap, granted=False, dori_required=required,
                dori_achieved=achieved,
                reason=(
                    f"needs {required.px_per_metre:.0f} px/m ({required.value}); "
                    f"this camera peaks at {best_ppm:.0f} px/m ({achieved.value}) "
                    f"at the bottom of frame, so no part of the scene qualifies"
                ),
            ))
            continue

        region = _full_width_band(boundary, img_w, img_h)
        coverage = 1.0 - (boundary / max(1, img_h))

        # --- quality gates ---------------------------------------------------
        blockers: list[str] = []
        fine_detail = cap in FINE_DETAIL_CAPABILITIES
        floor = (MIN_EFFECTIVE_RESOLUTION_FINE if fine_detail
                 else MIN_EFFECTIVE_RESOLUTION_COARSE)
        if measurement.effective_resolution_factor < floor:
            blockers.append(
                f"effective resolution factor {measurement.effective_resolution_factor:.3f} "
                f"below the {floor} floor required for "
                f"{'fine-detail' if fine_detail else 'object'} analytics "
                f"(soft, fogged or upscaled image)"
            )

        if fine_detail:
            if measurement.compression_artifact_score > MAX_BLOCKINESS_FOR_FINE_DETAIL:
                blockers.append(
                    f"block artifacts {measurement.compression_artifact_score:.2f} "
                    f"exceed the {MAX_BLOCKINESS_FOR_FINE_DETAIL} limit for "
                    f"fine-detail analytics"
                )
            if measurement.noise_sigma > MAX_NOISE_FOR_FINE_DETAIL:
                blockers.append(
                    f"noise floor sigma {measurement.noise_sigma:.1f} exceeds the "
                    f"{MAX_NOISE_FOR_FINE_DETAIL} limit for fine-detail analytics"
                )
            if camera.sensor_type is SensorType.THERMAL:
                blockers.append(
                    "thermal sensor: renders no printed or facial detail regardless "
                    "of pixel density"
                )

        if cap is Capability.SPEED_ESTIMATION:
            fps = measurement.measured_fps or measurement.claimed_fps
            if fps < MIN_FPS_FOR_SPEED:
                blockers.append(
                    f"measured {fps:.1f} fps is below the {MIN_FPS_FOR_SPEED} fps "
                    f"needed for a usable speed estimate"
                )

        if cap is Capability.UAV_DETECTION and measurement.sharpness < 40.0:
            blockers.append(
                f"sharpness {measurement.sharpness:.0f} is too low to resolve small "
                f"aerial targets against sky"
            )

        if blockers:
            grants.append(CapabilityGrant(
                capability=cap, granted=False, dori_required=required,
                dori_achieved=achieved, valid_region=region,
                reason="; ".join(blockers),
            ))
            continue

        grants.append(CapabilityGrant(
            capability=cap, granted=True, dori_required=required,
            dori_achieved=achieved, valid_region=region,
            reason=(
                f"{required.px_per_metre:.0f} px/m reached below image row "
                f"{boundary:.0f} - {coverage * 100:.0f}% of the frame qualifies"
            ),
        ))

    cert = CapabilityCertificate(
        certificate_id=f"CERT-{camera.camera_id}-{uuid.uuid4().hex[:8]}",
        camera_id=camera.camera_id,
        issued_at=datetime.now(timezone.utc),
        version=version,
        measurement=measurement,
        grants=grants,
        overall_dori=overall,
    )
    cert.digest = certificate_digest(cert)
    return cert


def certificate_digest(cert: CapabilityCertificate) -> str:
    """Stable SHA-256 over the certificate body, excluding the digest itself.

    Lets an auditor confirm that the capability statement a camera was operating
    under has not been edited after the fact.
    """
    body = cert.model_dump(mode="json", exclude={"digest"})
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify_certificate(cert: CapabilityCertificate) -> bool:
    return bool(cert.digest) and cert.digest == certificate_digest(cert)

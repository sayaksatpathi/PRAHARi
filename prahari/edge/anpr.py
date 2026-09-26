"""Automatic Number Plate Recognition, gated by measured capability.

ANPR is the analytic where Prahari's central claim has to pay off. Every video
analytics product offers plate reading; almost none of them will tell you *where*
in a given camera's frame it can actually be trusted. Here it is refused unless
the camera's measured capability certificate says otherwise, and even then only
inside the image region that reaches the required pixel density.

Two gates, both enforced before a model is ever invoked:

1.  **Capability.** The certificate must grant ANPR. A perimeter camera watching
    a ridge line is refused on role grounds however good its optics; a wide-angle
    dome is refused on pixel density; a thermal sensor is refused outright,
    because thermal renders no printed detail at any resolution.

2.  **Region.** The grant carries the image band where 250 px/m (the IEC 62676-4
    *identify* threshold) is actually reached. A vehicle outside that band is not
    read, because a ~500 mm Indian plate needs on the order of 120 px across for
    OCR to be dependable, and a read below that looks confident and is wrong.

The second gate is the one that matters. Running ANPR on the whole frame and
then filtering low-confidence results afterwards is not the same thing: OCR
confidence on a too-small plate is not calibrated, so the bad reads do not
reliably announce themselves.

Backends follow the same pattern as the detectors: a real ONNX implementation
(fast-alpr, MIT) when installed, and an clearly-labelled synthetic reader for
demonstration when it is not. The synthetic reader never invents a plate that
was not physically legible in the frame.
"""
from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from prahari.common.models import (
    BBox,
    CapabilityCertificate,
    Capability,
    ObjectClass,
    Point,
    Track,
)
from prahari.common.geometry import point_in_polygon

log = logging.getLogger("prahari.anpr")

# A plate narrower than this in the image has no business being OCR'd. Derived
# from the DORI identify band: a 0.5 m plate at 250 px/m is 125 px wide. Set a
# little below that so a marginal read is attempted and then reported with its
# margin, rather than silently dropped.
MIN_PLATE_WIDTH_PX = 90

# Indian civilian format, e.g. "WB 24 AB 1234". Deliberately permissive about
# spacing and the number of series letters, which vary by state and vintage.
INDIAN_PLATE_RE = re.compile(
    r"^([A-Z]{2})[\s-]?(\d{1,2})[\s-]?([A-Z]{0,3})[\s-]?(\d{1,4})$"
)


@dataclass
class PlateRead:
    """One plate observation, with everything needed to judge it."""
    text: str
    text_normalised: str
    ocr_confidence: float
    detection_confidence: float
    plate_bbox: BBox
    plate_width_px: float
    # Whether the plate met the pixel-width threshold. A read below it is
    # retained but flagged, never silently promoted.
    above_threshold: bool
    format_valid: bool
    backend: str
    simulated: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "plate": self.text_normalised,
            "raw_text": self.text,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "plate_width_px": round(self.plate_width_px, 1),
            "above_pixel_threshold": self.above_threshold,
            "format_valid": self.format_valid,
            "backend": self.backend,
            "simulated": self.simulated,
            "note": self.note,
        }


def normalise_plate(text: str) -> tuple[str, bool]:
    """Canonicalise a raw OCR string and say whether it looks like a real plate.

    OCR confuses a small, fixed set of glyph pairs, and the Indian format is
    positional - letters and digits occupy known slots - so most of those
    confusions are correctable from position alone rather than from a dictionary.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text or "").upper()
    if not cleaned:
        return "", False

    match = INDIAN_PLATE_RE.match(cleaned)
    if match:
        state, rto, series, number = match.groups()
        parts = [state, rto] + ([series] if series else []) + [number]
        return " ".join(parts), True
    return cleaned, False


def _conf_scalar(value: Any) -> float:
    """Coerce a confidence to a float.

    Some fast-alpr / OCR versions return the OCR confidence as a per-character
    list (or numpy array) rather than a single float. Treat a sequence as the mean
    of its numeric entries, a scalar as itself, and anything empty/None as 0.0.
    Without this, ``float(confidence)`` raised ``TypeError: ... not 'list'`` and
    every real plate read was silently dropped.
    """
    if value is None:
        return 0.0
    if isinstance(value, (list, tuple)):
        vals = [float(v) for v in value if isinstance(v, (int, float))]
        return float(sum(vals) / len(vals)) if vals else 0.0
    try:
        import numpy as _np
        if isinstance(value, _np.ndarray):
            return float(value.mean()) if value.size else 0.0
    except Exception:                       # noqa: BLE001
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class PlateReader(abc.ABC):
    """Reads a plate from a vehicle crop."""

    name: str = "plate-reader"
    simulated: bool = False

    @abc.abstractmethod
    def read(self, image: np.ndarray, vehicle: BBox,
             context: dict[str, Any] | None = None) -> PlateRead | None:
        ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "simulated": self.simulated}


class FastAlprReader(PlateReader):
    """fast-alpr (MIT) - ONNX plate detection plus ONNX OCR.

    Chosen because it is ONNX end to end, so it adds a model rather than a
    framework: no torch, and the same execution-provider selection as the object
    detector.
    """

    name = "fast-alpr"

    def __init__(self, device: str = "auto",
                 detector_model: str = "yolo-v9-t-384-license-plate-end2end",
                 ocr_model: str = "global-plates-mobile-vit-v2-model") -> None:
        from fast_alpr import ALPR

        providers = None
        if device == "cpu":
            providers = ["CPUExecutionProvider"]
        try:
            self._alpr = ALPR(detector_model=detector_model, ocr_model=ocr_model)
        except TypeError:
            # Older/newer signatures differ; fall back to defaults rather than
            # failing closed on a keyword name.
            self._alpr = ALPR()
        self.device = device
        log.info("ANPR backend: fast-alpr (%s / %s)", detector_model, ocr_model)

    def read(self, image: np.ndarray, vehicle: BBox,
             context: dict[str, Any] | None = None) -> PlateRead | None:
        h, w = image.shape[:2]
        x1 = int(max(0, vehicle.x1)); y1 = int(max(0, vehicle.y1))
        x2 = int(min(w, vehicle.x2)); y2 = int(min(h, vehicle.y2))
        if x2 - x1 < 16 or y2 - y1 < 16:
            return None
        crop = image[y1:y2, x1:x2]

        try:
            results = self._alpr.predict(crop)
        except Exception:
            log.exception("fast-alpr inference failed")
            return None
        if not results:
            return None

        best = max(results, key=lambda r: _conf_scalar(getattr(
            getattr(r, "ocr", None), "confidence", 0.0)))
        ocr = getattr(best, "ocr", None)
        text = (getattr(ocr, "text", "") or "").strip()
        if not text:
            return None

        det = getattr(best, "detection", None)
        box = getattr(det, "bounding_box", None)
        if box is not None:
            px1 = float(getattr(box, "x1", 0)) + x1
            py1 = float(getattr(box, "y1", 0)) + y1
            px2 = float(getattr(box, "x2", 0)) + x1
            py2 = float(getattr(box, "y2", 0)) + y1
        else:
            px1, py1, px2, py2 = float(x1), float(y1), float(x2), float(y2)

        plate_box = BBox(x1=px1, y1=py1, x2=px2, y2=py2)
        normalised, valid = normalise_plate(text)
        width = plate_box.width

        return PlateRead(
            text=text,
            text_normalised=normalised,
            ocr_confidence=_conf_scalar(getattr(ocr, "confidence", 0.0)),
            detection_confidence=_conf_scalar(getattr(det, "confidence", 0.0)),
            plate_bbox=plate_box,
            plate_width_px=width,
            above_threshold=width >= MIN_PLATE_WIDTH_PX,
            format_valid=valid,
            backend=self.name,
            note="" if width >= MIN_PLATE_WIDTH_PX else (
                f"plate is {width:.0f} px wide, below the {MIN_PLATE_WIDTH_PX} px "
                f"needed for a dependable read; treat as indicative only"),
        )


class SyntheticPlateReader(PlateReader):
    """Demonstration reader driven by simulator ground truth.

    Exists so the ANPR path can be exercised end to end without the fast-alpr
    models installed. It is labelled simulated everywhere it surfaces, and - the
    part that matters - it **refuses to read a plate the frame does not
    physically render legibly**. The simulator only draws plate glyphs above a
    pixel width; below that this returns nothing, exactly as a real OCR would
    fail. A synthetic reader that returned the correct plate regardless of
    legibility would make the entire capability-gating argument meaningless.
    """

    name = "synthetic-anpr (SIMULATED)"
    simulated = True

    def __init__(self, seed: int = 20260913) -> None:
        self._rng = np.random.default_rng(seed)

    def read(self, image: np.ndarray, vehicle: BBox,
             context: dict[str, Any] | None = None) -> PlateRead | None:
        truth = (context or {}).get("ground_truth") or []

        # Find the simulated actor this vehicle box corresponds to.
        best, best_iou = None, 0.0
        for gt in truth:
            if not gt.get("plate") or not gt.get("plate_bbox"):
                continue
            gx1, gy1, gx2, gy2 = gt["bbox"]
            iou = vehicle.iou(BBox(x1=gx1, y1=gy1, x2=gx2, y2=gy2))
            if iou > best_iou:
                best, best_iou = gt, iou
        if best is None or best_iou < 0.3:
            return None

        px1, py1, px2, py2 = best["plate_bbox"]
        plate_box = BBox(x1=float(px1), y1=float(py1), x2=float(px2), y2=float(py2))
        width = plate_box.width

        # The simulator only renders readable glyphs above ~46 px of plate width.
        # Below that there is nothing legible in the image, so there is nothing
        # to read.
        if width < 46:
            return None

        true_text = str(best["plate"])
        # Degrade the read as width falls, the way OCR actually degrades.
        quality = float(np.clip((width - 46) / 90.0, 0.0, 1.0))
        text = true_text
        if self._rng.random() > (0.55 + 0.44 * quality):
            text = _corrupt_glyph(true_text, self._rng)

        normalised, valid = normalise_plate(text)
        return PlateRead(
            text=text,
            text_normalised=normalised,
            ocr_confidence=float(np.clip(0.55 + 0.42 * quality
                                         + self._rng.normal(0, 0.03), 0.3, 0.99)),
            detection_confidence=float(np.clip(0.7 + 0.25 * quality, 0.3, 0.99)),
            plate_bbox=plate_box,
            plate_width_px=width,
            above_threshold=width >= MIN_PLATE_WIDTH_PX,
            format_valid=valid,
            backend=self.name,
            simulated=True,
            note=("SYNTHETIC DEMONSTRATION PLATE - not a real vehicle registration"
                  + ("" if width >= MIN_PLATE_WIDTH_PX else
                     f"; {width:.0f} px wide, below the {MIN_PLATE_WIDTH_PX} px "
                     f"threshold, so this read is indicative only")),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "simulated": True,
            "note": ("Driven by simulator ground truth, and refuses to read a "
                     "plate the frame does not render legibly. Plates are "
                     "synthetic demonstration data, not real registrations."),
        }


# The glyph pairs OCR actually confuses.
_CONFUSIONS = {"0": "O", "O": "0", "1": "I", "I": "1", "8": "B", "B": "8",
               "5": "S", "S": "5", "2": "Z", "Z": "2", "6": "G", "G": "6"}


def _corrupt_glyph(text: str, rng: np.random.Generator) -> str:
    chars = list(text)
    candidates = [i for i, c in enumerate(chars) if c in _CONFUSIONS]
    if not candidates:
        return text
    i = int(rng.choice(candidates))
    chars[i] = _CONFUSIONS[chars[i]]
    return "".join(chars)


# =====================================================================
# Capability gating
# =====================================================================

def anpr_region(certificate: CapabilityCertificate | None) -> list[Point] | None:
    """The image region in which this camera may attempt a plate read."""
    if certificate is None:
        return None
    grant = certificate.grant_for(Capability.ANPR)
    if grant is None or not grant.granted:
        return None
    return grant.valid_region or None


def vehicle_in_anpr_region(vehicle: BBox, region: list[Point] | None,
                           frame_w: int, frame_h: int) -> bool:
    """Is the vehicle inside the certified plate-reading band?

    Tested on the vehicle's foot point - where it meets the road - because that
    is what the ground-plane scale is defined against. Using the box centre would
    admit vehicles whose bodywork reaches into the band while their plate, which
    sits low, does not.
    """
    if region is None:
        return False
    if len(region) < 3:
        return True
    fx, fy = vehicle.foot
    return point_in_polygon(fx / max(1, frame_w), fy / max(1, frame_h), region)


# =====================================================================
# Repeat-entity intelligence
# =====================================================================

@dataclass
class PlateSighting:
    plate: str
    camera_id: str
    at: datetime
    confidence: float


@dataclass
class RepeatPlateTracker:
    """Flags vehicles that keep reappearing at hours they should not.

    This is the highest-value open-border signal and it is cheap: on a treaty-open
    border a single crossing is unremarkable, but the *same* vehicle crossing
    repeatedly at 02:00, or appearing at several unofficial routes in a week, is a
    logistics pattern rather than a commuter. It is also the one analytic here
    that genuinely needs plate reading rather than mere vehicle detection.

    Kept deliberately simple and local: counts and timestamps per plate, nothing
    that constitutes a movement profile of an identified individual. See
    docs/privacy.md.
    """
    window_hours: float = 168.0           # one week
    unusual_hours: tuple[int, int] = (22, 5)   # inclusive start, exclusive end
    min_sightings: int = 3
    sightings: dict[str, list[PlateSighting]] = field(default_factory=dict)

    def _is_unusual_hour(self, when: datetime) -> bool:
        start, end = self.unusual_hours
        h = when.hour
        return h >= start or h < end

    def observe(self, read: PlateRead, camera_id: str,
                when: datetime) -> dict[str, Any] | None:
        """Record a sighting. Returns a finding when a pattern emerges."""
        if not read.format_valid or not read.above_threshold:
            # Only well-formed, adequately-sized reads feed the pattern. A
            # miscorrected plate would otherwise create a phantom vehicle and,
            # worse, could attach a pattern to a real registration that never
            # made those crossings.
            return None

        plate = read.text_normalised
        history = self.sightings.setdefault(plate, [])
        history.append(PlateSighting(plate, camera_id, when, read.ocr_confidence))

        cutoff = when - timedelta(hours=self.window_hours)
        history[:] = [s for s in history if s.at >= cutoff]

        odd = [s for s in history if self._is_unusual_hour(s.at)]
        cameras = {s.camera_id for s in history}

        if len(odd) >= self.min_sightings:
            return {
                "plate": plate,
                "sightings": len(history),
                "unusual_hour_sightings": len(odd),
                "cameras": sorted(cameras),
                "summary": (
                    f"Vehicle {plate} has been recorded {len(odd)} times during "
                    f"night hours across {len(cameras)} camera(s) in the last "
                    f"{self.window_hours / 24:.0f} days"
                ),
            }
        if len(cameras) >= 3 and len(history) >= self.min_sightings:
            return {
                "plate": plate,
                "sightings": len(history),
                "unusual_hour_sightings": len(odd),
                "cameras": sorted(cameras),
                "summary": (
                    f"Vehicle {plate} has appeared at {len(cameras)} separate "
                    f"locations in the last {self.window_hours / 24:.0f} days"
                ),
            }
        return None

    def history_for(self, plate: str) -> list[PlateSighting]:
        return list(self.sightings.get(plate, []))


def build_plate_reader(settings, *, simulated_source: bool) -> PlateReader | None:
    """Construct a reader appropriate to the imagery it will be given.

    The choice is driven by the *source*, not by a global setting, because the
    two readers are not interchangeable. A real plate model is trained on
    photographs and finds nothing in a synthetic scene - verified: fast-alpr
    loads and runs on simulated frames and returns no detection, because a flat
    white rectangle with vector-drawn glyphs is not a photograph of a number
    plate. Preferring the real backend on simulated imagery would therefore
    produce a silently dead analytic that looks configured.

    So: synthetic imagery gets the synthetic reader, clearly labelled; real
    imagery gets the real model. Both are gated identically by the capability
    certificate, so the architecture being demonstrated is the same one that
    would run in the field.
    """
    choice = (getattr(settings, "anpr_backend", "auto") or "auto").lower()

    if choice == "synthetic":
        log.warning("ANPR: SYNTHETIC by configuration - plates are demonstration data")
        return SyntheticPlateReader(seed=settings.demo_seed)

    if choice == "auto" and simulated_source:
        log.info("ANPR: simulated source - using the synthetic reader. A real "
                 "plate model finds nothing in synthetic imagery, so preferring "
                 "it here would leave the analytic silently dead.")
        return SyntheticPlateReader(seed=settings.demo_seed)

    try:
        return FastAlprReader(device=settings.device)
    except Exception as exc:
        if choice not in ("auto",):
            raise
        log.warning(
            "ANPR: fast-alpr unavailable (%s) - falling back to the SYNTHETIC "
            "reader. Plates are demonstration data and are labelled as such. "
            "Install with: pip install fast-alpr", exc,
        )
        return SyntheticPlateReader(seed=settings.demo_seed)

"""Event refinement: turn a segmentation into a zone/evidence decision.

This is where a mask earns its cost. Given an event candidate that the cheap
first stage flagged, and a mask from the second stage, it computes the things a
box cannot:

  * **Mask-in-zone fraction.** How much of the actual object lies inside the
    restricted polygon, rather than merely whether the box overlaps it. A person
    whose box clips a zone corner but whose body is entirely outside is a
    different event from one standing squarely inside, and only the mask can tell
    them apart.

  * **Refined ground contact.** The lowest silhouette point is a better estimate
    of where the object meets the ground than the box bottom, so the zone test is
    re-run on it and disagreements with the box-based verdict are surfaced.

  * **A precise outline for evidence**, saved alongside the trigger frame.

The refinement never *overturns* the event on its own - the first stage already
decided the event is worth attention, and a second-stage segmenter that can veto
an alert becomes a single point of failure for missing a real intrusion. Instead
it annotates: it strengthens or weakens the priority score and records why, and
it hands the operator a mask to look at.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from prahari.common.geometry import point_in_polygon
from prahari.common.models import Zone, ZoneKind
from prahari.edge.segment.base import SegmentResult, Segmenter


@dataclass
class Refinement:
    result: SegmentResult
    zone_id: str | None
    mask_in_zone_fraction: float
    ground_contact_in_zone: bool
    box_and_mask_agree: bool
    score_adjustment: float
    detail: dict[str, Any]


def refine_event(
    segmenter: Segmenter,
    image: np.ndarray,
    box,
    zone: Zone | None,
    box_says_in_zone: bool,
) -> Refinement | None:
    """Segment the object and compute its relationship to the event zone."""
    seg = segmenter.segment(image, box)
    if seg is None:
        return None

    h, w = image.shape[:2]

    zone_id = zone.zone_id if zone else None
    mask_fraction = 0.0
    contact_in_zone = box_says_in_zone
    if zone is not None and zone.kind is ZoneKind.POLYGON and len(zone.points) >= 3:
        def in_zone(x: int, y: int) -> bool:
            return point_in_polygon(x / max(1, w), y / max(1, h), zone.points)

        mask_fraction = seg.contains_fraction(in_zone)
        gx, gy = seg.ground_contact
        contact_in_zone = in_zone(int(gx), int(gy))

    box_and_mask_agree = (box_says_in_zone == contact_in_zone)

    # Score adjustment: confirmation nudges up, contradiction nudges down, and
    # both are bounded so the refinement annotates rather than dominates.
    adjustment = 0.0
    reason = "no zone to refine against"
    if zone is not None and zone.kind is ZoneKind.POLYGON:
        if mask_fraction >= 0.5 and contact_in_zone:
            adjustment = 0.08
            reason = (f"{mask_fraction:.0%} of the segmented object is inside "
                      f"{zone.name}, ground contact confirmed")
        elif mask_fraction >= 0.15:
            adjustment = 0.03
            reason = (f"{mask_fraction:.0%} of the segmented object is inside "
                      f"{zone.name}")
        elif box_says_in_zone and mask_fraction < 0.05:
            # The box entered the zone but almost none of the real object did -
            # a clipped-corner event. Weaken, but never below zero contribution.
            adjustment = -0.06
            reason = (f"only {mask_fraction:.0%} of the segmented object is "
                      f"actually inside {zone.name} - the box clipped the zone "
                      f"but the object is largely outside")

    detail = {
        "segmentation": seg.as_dict(),
        "mask_in_zone_fraction": round(mask_fraction, 3),
        "ground_contact_in_zone": contact_in_zone,
        "box_and_mask_agree": box_and_mask_agree,
        "refinement_reason": reason,
    }

    return Refinement(
        result=seg,
        zone_id=zone_id,
        mask_in_zone_fraction=mask_fraction,
        ground_contact_in_zone=contact_in_zone,
        box_and_mask_agree=box_and_mask_agree,
        score_adjustment=adjustment,
        detail=detail,
    )

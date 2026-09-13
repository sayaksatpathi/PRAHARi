"""Detector abstraction.

The model behind this interface is expected to change - that is the point. A
border deployment will want a different detector for thermal than for visible,
a smaller one on a Jetson than on a workstation, and a retrained one once real
domain footage exists. Nothing outside this package may assume which model is
loaded, what framework runs it, or what its class indices are.
"""
from __future__ import annotations

import abc
from typing import Any

import numpy as np

from prahari.common.models import Capability, Detection


class Detector(abc.ABC):
    """Turns a frame into detections, filtered by what the camera may run."""

    name: str = "detector"
    device: str = "cpu"

    @abc.abstractmethod
    def infer(
        self,
        image: np.ndarray,
        *,
        allowed: set[Capability] | None = None,
        frame_index: int = 0,
        context: dict[str, Any] | None = None,
    ) -> list[Detection]:
        """Run detection.

        `allowed` carries the camera's granted capabilities. A detector must not
        return vehicle detections for a camera whose certificate refuses vehicle
        detection - capability gating is enforced at the point of inference, not
        cosmetically hidden later in the UI.
        """

    def warmup(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "device": self.device}


def class_allowed(object_class_value: str, allowed: set[Capability] | None) -> bool:
    """Map a detected class back to the capability that authorises it."""
    if allowed is None:
        return True
    from prahari.common.models import ObjectClass

    try:
        oc = ObjectClass(object_class_value)
    except ValueError:
        return False

    if oc is ObjectClass.PERSON:
        return Capability.PERSON_DETECTION in allowed
    if oc.is_vehicle or oc is ObjectClass.BICYCLE:
        return Capability.VEHICLE_DETECTION in allowed
    if oc is ObjectClass.CATTLE:
        return Capability.ANIMAL_DETECTION in allowed
    if oc is ObjectClass.BOAT:
        return Capability.BOAT_DETECTION in allowed
    if oc is ObjectClass.UAV:
        return Capability.UAV_DETECTION in allowed
    return False

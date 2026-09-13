"""Video source abstraction.

Every source - a simulated camera, a video file, a live RTSP stream - presents
the same interface, so the detection pipeline never knows or cares where frames
came from. This is what makes "demo mode" an adapter rather than a fork in the
code: the simulator implements the same contract a real camera does.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class Frame:
    image: np.ndarray                 # BGR uint8
    index: int
    timestamp: datetime
    # Ground truth is populated only by the simulator, and only ever consumed by
    # the synthetic detector and the evaluation harness. The rule engine, the
    # tracker and the UI never see it - otherwise the demo would be measuring
    # nothing.
    ground_truth: list[dict[str, Any]] = field(default_factory=list)


class VideoSource(abc.ABC):
    """A frame producer with declared and measurable properties."""

    camera_id: str
    width: int
    height: int
    nominal_fps: float

    @abc.abstractmethod
    def open(self) -> bool:
        """Connect. Returns False on failure - never raises for a dead camera,
        because a camera being unreachable is a normal operating condition at a
        border outpost, not an exception."""

    @abc.abstractmethod
    def read(self) -> Frame | None:
        """Next frame, or None if the source has ended or dropped."""

    @abc.abstractmethod
    def close(self) -> None:
        ...

    @property
    def is_simulated(self) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "width": self.width,
            "height": self.height,
            "nominal_fps": self.nominal_fps,
            "simulated": self.is_simulated,
        }

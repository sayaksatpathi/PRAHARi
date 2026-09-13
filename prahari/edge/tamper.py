"""Camera integrity monitoring.

An adversary at a border attacks the sensor, not the algorithm. Spray paint on a
dome, a lens turned skyward, an IR illuminator defeated with a torch, a cut PoE
run, or - the quiet one - a looped recording fed back into the NVR so the screen
shows a peaceful stretch of fence that is no longer there.

None of that is exotic, and none of it is detected by an object detector, which
will happily report "no objects" on a camera that has been painted over and
report it with complete confidence. So integrity is monitored separately, on its
own signals, and produces its own high-priority event class.

Replay detection deserves a note. Comparing consecutive frame hashes catches a
crudely frozen feed, because real sensor noise guarantees that no two genuine
frames are ever byte-identical. It does *not* catch a competent attacker looping
a long, genuine recording - that needs a challenge the camera cannot precompute,
which is designed for in docs/threat-model.md and not implemented here. What is
built catches the crude case and says so.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class TamperVerdict:
    tampered: bool
    kind: str | None          # covered | blinded | defocused | moved | frozen
    confidence: float
    detail: str


@dataclass
class TamperDetector:
    """Rolling integrity checks against a learnt reference fingerprint."""

    # How long a condition must persist before it is reported. A lorry parked in
    # front of a camera is not tampering; a lorry parked there for two minutes
    # is operationally the same thing, and either way one dark frame is not.
    persistence_frames: int = 45
    reference_alpha: float = 0.02      # how fast the reference adapts to real change
    # Frames spent learning what this camera's normal image looks like before
    # any judgement is made about it.
    baseline_frames: int = 120

    _reference: np.ndarray | None = field(default=None, repr=False)
    _recent_hashes: deque = field(default_factory=lambda: deque(maxlen=12), repr=False)
    _streaks: dict = field(default_factory=dict, repr=False)
    _frames_seen: int = 0
    # The camera's own normal brightness and contrast, learnt rather than assumed.
    _base_mean: float | None = field(default=None, repr=False)
    _base_std: float | None = field(default=None, repr=False)
    _base_motion: float = field(default=0.0, repr=False)
    _prev_small: np.ndarray | None = field(default=None, repr=False)

    def _bump(self, key: str) -> int:
        self._streaks[key] = self._streaks.get(key, 0) + 1
        return self._streaks[key]

    def _reset(self, key: str) -> None:
        self._streaks[key] = 0

    def update(self, image: np.ndarray) -> TamperVerdict:
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        small = cv2.resize(grey, (64, 64), interpolation=cv2.INTER_AREA)
        self._frames_seen += 1

        mean = float(small.mean())
        std = float(small.std())
        focus = float(cv2.Laplacian(small, cv2.CV_64F).var())

        # --- frozen / replayed feed ------------------------------------
        digest = hashlib.sha1(small.tobytes()).hexdigest()
        self._recent_hashes.append(digest)
        if len(self._recent_hashes) == self._recent_hashes.maxlen and \
                len(set(self._recent_hashes)) == 1:
            n = self._bump("frozen")
            if n >= 3:
                return TamperVerdict(
                    True, "frozen", 0.95,
                    f"{self._recent_hashes.maxlen} consecutive frames are byte-identical; "
                    f"a live sensor always produces noise, so the feed is frozen, "
                    f"looped or replayed",
                )
        else:
            self._reset("frozen")

        # --- learn this camera's own normal ------------------------------
        # Absolute brightness thresholds do not work here. A thermal unit sits at
        # a mean of ~17 and a night-IR camera at ~20 by design, and an absolute
        # "too dark" rule reported both as a covered lens within a minute of
        # startup. Tampering is a departure from a camera's *own* baseline, not
        # from some global idea of a correct image.
        motion = 0.0
        if self._prev_small is not None:
            motion = float(cv2.absdiff(small, self._prev_small).mean())
        self._prev_small = small

        if self._frames_seen <= self.baseline_frames:
            a = 1.0 / max(1, self._frames_seen)
            self._base_mean = mean if self._base_mean is None else \
                (1 - a) * self._base_mean + a * mean
            self._base_std = std if self._base_std is None else \
                (1 - a) * self._base_std + a * std
            self._base_motion = (1 - a) * self._base_motion + a * motion
            return TamperVerdict(False, None, 0.0, "")

        base_mean = self._base_mean or 1.0
        base_std = self._base_std or 1.0

        # --- lens covered ------------------------------------------------
        # Three signals together: the image went much darker than this camera's
        # normal, lost nearly all its structure, and stopped changing. A dark
        # night scene fails the second and third of those, which is exactly the
        # distinction the absolute threshold could not make.
        went_dark = mean < max(3.0, base_mean * 0.40)
        lost_structure = std < max(2.5, base_std * 0.35)
        went_still = motion < max(0.4, self._base_motion * 0.25)
        if went_dark and lost_structure and went_still:
            n = self._bump("covered")
            if n >= self.persistence_frames:
                return TamperVerdict(
                    True, "covered", 0.88,
                    f"image has collapsed to mean {mean:.0f} against this camera's "
                    f"normal {base_mean:.0f}, with structure and motion gone, for "
                    f"{n} frames - lens obstructed, sprayed or capped",
                )
        else:
            self._reset("covered")

        # --- deliberate glare / IR blinding -----------------------------
        if mean > max(215.0, base_mean * 2.4) and std < max(6.0, base_std * 0.45):
            n = self._bump("blinded")
            if n >= self.persistence_frames:
                return TamperVerdict(
                    True, "blinded", 0.85,
                    f"image saturated at mean {mean:.0f} against a normal of "
                    f"{base_mean:.0f} for {n} frames - the sensor is being flooded "
                    f"with light",
                )
        else:
            self._reset("blinded")

        # --- defocus ----------------------------------------------------
        if focus < 3.0 and std > 10:
            n = self._bump("defocused")
            if n >= self.persistence_frames * 2:
                return TamperVerdict(
                    True, "defocused", 0.62,
                    f"edge energy has collapsed ({focus:.1f}) while the scene still "
                    f"varies - lens defocused, fogged or contaminated",
                )
        else:
            self._reset("defocused")

        # --- camera moved -----------------------------------------------
        ref = self._reference
        if ref is None:
            self._reference = small.astype(np.float32)
        else:
            divergence = float(cv2.absdiff(small.astype(np.float32), ref).mean())
            if divergence > 46.0:
                n = self._bump("moved")
                if n >= self.persistence_frames:
                    self._reference = small.astype(np.float32)   # accept the new view
                    self._reset("moved")
                    return TamperVerdict(
                        True, "moved", 0.70,
                        f"the scene has changed wholesale (divergence {divergence:.0f}) "
                        f"and stayed changed - camera repointed, knocked or rotated",
                    )
            else:
                self._reset("moved")
                # Adapt slowly, so daylight changing into dusk is not tampering.
                self._reference = ((1 - self.reference_alpha) * ref
                                   + self.reference_alpha * small.astype(np.float32))

        return TamperVerdict(False, None, 0.0, "")

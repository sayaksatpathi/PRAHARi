"""Event Priority Score.

A deliberately transparent, additive score. It is **not** a probability and is
never presented as one: nothing here has been calibrated against ground truth on
border imagery, and presenting an uncalibrated number as "87% likely to be an
intrusion" is the kind of claim that collapses the first time it is audited.

What it is instead is a ranking function with a visible derivation. Every
contribution is recorded as a named factor with its weight, and the operator can
open any alert and see the arithmetic. That matters more than accuracy for the
failure mode that actually kills deployments: an operator who cannot tell why the
system is shouting stops listening to it.

Two inputs deserve particular attention, because they are what make the score
fall over time rather than stay constant:

  * `normalcy_ratio` - how unusual this observation is for this camera, this
    zone, this class, at this hour of the week, against what the node has
    actually learnt. Cattle at 10:00 on a camera that sees cattle every morning
    contributes nothing. The same cattle at 03:00 contributes.

  * `feedback` - the operator's own verdicts. A camera and event type the
    operator has repeatedly marked a false alarm is damped automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from prahari.common.models import (
    EventType,
    ObjectClass,
    Priority,
    PriorityFactor,
    Track,
)

# Base weight per event type: how much the bare occurrence is worth before any
# context is applied.
BASE_WEIGHT: dict[EventType, float] = {
    EventType.LINE_CROSSING: 0.52,
    EventType.ZONE_INTRUSION: 0.46,
    EventType.WRONG_DIRECTION: 0.40,
    EventType.LOITERING: 0.28,
    EventType.GROUP_MOVEMENT: 0.44,
    EventType.VEHICLE_MOVEMENT: 0.26,
    EventType.OFF_ROUTE_MOVEMENT: 0.42,
    EventType.TEMPORAL_ANOMALY: 0.30,
    EventType.REPEAT_ENTITY: 0.38,
    EventType.CORRIDOR_DROPOUT: 0.44,
    EventType.NIGHT_MOVEMENT: 0.50,
    EventType.SUSPICIOUS_ACTIVITY: 0.60,
    EventType.CAMERA_TAMPER: 0.78,
    EventType.CAMERA_OFFLINE: 0.34,
    EventType.STREAM_REPLAY_SUSPECTED: 0.82,
}

# Movement/tripwire events whose alarm depends on how routine crossings are for
# this camera: heavy learnt traffic makes a single one pattern-of-life, not an
# intrusion. Security events (tamper, replay, offline) are never downgraded this
# way — a painted-over lens is not more acceptable on a busy camera.
_ROUTINE_MOVEMENT = frozenset({
    EventType.LINE_CROSSING,
    EventType.ZONE_INTRUSION,
    EventType.WRONG_DIRECTION,
    EventType.OFF_ROUTE_MOVEMENT,
    EventType.VEHICLE_MOVEMENT,
    EventType.GROUP_MOVEMENT,
    # In a dense crowd, apparent "erratic movement" and brief loitering are
    # overwhelmingly tracking jitter and people milling, not intent. On a camera
    # that has learnt to be busy these are pattern-of-life too; the genuinely
    # anomalous ones still surface through the alert budget.
    EventType.SUSPICIOUS_ACTIVITY,
    EventType.LOITERING,
})

# How much each class raises or lowers concern, independent of the rule.
CLASS_WEIGHT: dict[ObjectClass, float] = {
    ObjectClass.PERSON: 0.10,
    ObjectClass.TRUCK: 0.09,
    ObjectClass.CAR: 0.06,
    ObjectClass.MOTORCYCLE: 0.07,
    ObjectClass.BUS: 0.04,
    ObjectClass.BICYCLE: 0.04,
    ObjectClass.BOAT: 0.08,
    ObjectClass.UAV: 0.20,
    # Livestock actively reduces concern. At a border it is overwhelmingly
    # benign, and treating it as neutral is how the night shift drowns.
    ObjectClass.CATTLE: -0.14,
    ObjectClass.UNKNOWN: 0.0,
}


@dataclass
class ScoringContext:
    """Everything outside the rule itself that should move the score."""
    is_night: bool = False
    normalcy_ratio: float = 1.0        # observed / expected for this bucket; >1 is unusual
    normalcy_samples: int = 0          # how much evidence the ratio rests on
    normalcy_expected: float = 0.0     # absolute traffic this camera routinely sees here
    feedback_true: int = 0
    feedback_false: int = 0
    detection_confidence: float = 0.0
    capability_supported: bool = True  # was the analytic inside its certified region?
    dwell_seconds: float = 0.0
    speed_mps: float | None = None
    group_size: int = 1
    near_boundary: bool = False
    extra: dict[str, float] = field(default_factory=dict)


def score_event(
    event_type: EventType,
    track: Track | None,
    ctx: ScoringContext,
) -> tuple[float, list[PriorityFactor], Priority]:
    """Return (score in 0..1, the factors that produced it, the priority band)."""
    factors: list[PriorityFactor] = []

    base = BASE_WEIGHT.get(event_type, 0.30)
    factors.append(PriorityFactor(
        name="event type", weight=base,
        detail=f"base weight for {event_type.value.replace('_', ' ')}",
    ))

    # --- object class ---------------------------------------------------
    if track is not None:
        cw = CLASS_WEIGHT.get(track.object_class, 0.0)
        if abs(cw) > 1e-6:
            detail = f"{track.object_class.value} classification"
            if cw < 0:
                detail += " - livestock is overwhelmingly benign at a border"
            factors.append(PriorityFactor(name="object class", weight=cw, detail=detail))

    # --- darkness -------------------------------------------------------
    if ctx.is_night:
        factors.append(PriorityFactor(
            name="night conditions", weight=0.12,
            detail="observed during hours of darkness",
        ))

    # --- learnt pattern of life -----------------------------------------
    # The single biggest lever on false-alarm rate. Weighted by how much
    # evidence the node has actually accumulated, so a freshly installed camera
    # does not get confident opinions from three observations.
    if ctx.normalcy_samples >= 5:
        confidence = min(1.0, ctx.normalcy_samples / 60.0)
        if ctx.normalcy_ratio >= 2.0:
            w = min(0.22, 0.07 * ctx.normalcy_ratio) * confidence
            factors.append(PriorityFactor(
                name="unusual for this time", weight=round(w, 3),
                detail=(f"{ctx.normalcy_ratio:.1f}x the traffic this camera normally "
                        f"sees in this hour ({ctx.normalcy_samples} observations learnt)"),
            ))
        elif ctx.normalcy_ratio <= 1.3:
            # Normal-or-quiet traffic. A single crossing is only alarming where
            # crossings are rare. On a thoroughfare the camera has learnt to be
            # busy, a routine crossing is pattern-of-life, not an intrusion — so
            # the downgrade grows with how much traffic this camera routinely
            # sees. This is the whole point of pattern-of-life over tripwires:
            # it stops a lawful-crossing flood from burying the real events.
            w = -0.16
            if event_type in _ROUTINE_MOVEMENT and ctx.normalcy_expected > 3.0:
                w -= min(0.34, 0.045 * ctx.normalcy_expected)
            w *= confidence
            factors.append(PriorityFactor(
                name="routine for this camera", weight=round(w, 3),
                detail=(f"consistent with normal traffic here at this hour "
                        f"(expected ~{ctx.normalcy_expected:.0f}/bucket, "
                        f"{ctx.normalcy_samples} observations learnt)"),
            ))
    elif ctx.normalcy_samples > 0:
        factors.append(PriorityFactor(
            name="pattern of life", weight=0.0,
            detail=(f"only {ctx.normalcy_samples} observations learnt for this bucket - "
                    f"not yet enough to judge whether this is unusual"),
        ))

    # --- operator feedback ----------------------------------------------
    total_fb = ctx.feedback_true + ctx.feedback_false
    if total_fb >= 4:
        fa_rate = ctx.feedback_false / total_fb
        if fa_rate >= 0.5:
            w = -0.28 * fa_rate
            factors.append(PriorityFactor(
                name="operator feedback", weight=round(w, 3),
                detail=(f"operators marked {ctx.feedback_false} of {total_fb} of these "
                        f"a false alarm on this camera"),
            ))
        elif fa_rate <= 0.2:
            factors.append(PriorityFactor(
                name="operator feedback", weight=0.08,
                detail=(f"operators confirmed {ctx.feedback_true} of {total_fb} of these "
                        f"on this camera"),
            ))

    # --- detection quality ----------------------------------------------
    if ctx.detection_confidence > 0:
        w = (ctx.detection_confidence - 0.6) * 0.20
        factors.append(PriorityFactor(
            name="detection confidence", weight=round(w, 3),
            detail=f"detector confidence {ctx.detection_confidence:.0%}",
        ))

    if not ctx.capability_supported:
        factors.append(PriorityFactor(
            name="outside certified region", weight=-0.20,
            detail=("the object was outside the image region this camera's "
                    "capability certificate covers for this analytic"),
        ))

    # --- behavioural modifiers -------------------------------------------
    if ctx.group_size >= 3:
        w = min(0.18, 0.05 * ctx.group_size)
        factors.append(PriorityFactor(
            name="group size", weight=round(w, 3),
            detail=f"{ctx.group_size} subjects moving together",
        ))

    if ctx.dwell_seconds >= 60:
        factors.append(PriorityFactor(
            name="extended dwell", weight=0.09,
            detail=f"present for {ctx.dwell_seconds:.0f} s",
        ))

    if ctx.speed_mps is not None and ctx.speed_mps > 6.0:
        factors.append(PriorityFactor(
            name="high speed", weight=0.08,
            detail=f"estimated {ctx.speed_mps:.1f} m/s",
        ))

    if ctx.near_boundary:
        factors.append(PriorityFactor(
            name="proximity to boundary", weight=0.07,
            detail="close to the configured border line",
        ))

    for name, weight in ctx.extra.items():
        factors.append(PriorityFactor(
            name=name, weight=round(weight, 3), detail="rule-specific contribution"
        ))

    raw = sum(f.weight for f in factors)
    score = max(0.0, min(1.0, raw))
    return round(score, 3), factors, Priority.from_score(score)


def explain(factors: list[PriorityFactor]) -> str:
    """One-line human-readable derivation, for logs and the alert card."""
    parts = [f"{f.name} {f.weight:+.2f}" for f in factors if abs(f.weight) > 1e-6]
    return " ".join(parts) if parts else "no contributing factors"

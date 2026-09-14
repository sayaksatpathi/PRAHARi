"""Friendly-force (patrol) suppression.

Own patrols walk the same routes every night and trip every rule on them. This
package decides which events are accounted for by a declared patrol movement -
and, just as importantly, which events *look* like a patrol without behaving
like one, because those deserve more attention than an ordinary alert, not less.

Nothing here deletes or hides an event. See `docs/patrol-suppression.md`.
"""
from prahari.edge.patrol.matcher import (
    DOWNGRADE_ADJUSTMENT,
    DEVIATION_ADJUSTMENT,
    Observation,
    PatrolMatcher,
    PatrolMetrics,
    STRONG_MATCH,
    SUPPRESS_ADJUSTMENT,
    UNCERTAIN_MATCH,
)
from prahari.edge.patrol.roster import PatrolRoster, demo_patrols

__all__ = [
    "DEVIATION_ADJUSTMENT",
    "DOWNGRADE_ADJUSTMENT",
    "Observation",
    "PatrolMatcher",
    "PatrolMetrics",
    "PatrolRoster",
    "STRONG_MATCH",
    "SUPPRESS_ADJUSTMENT",
    "UNCERTAIN_MATCH",
    "demo_patrols",
]

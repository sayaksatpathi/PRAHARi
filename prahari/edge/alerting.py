"""Alert governance - the difference between recording and interrupting.

Video analytics deployments do not usually fail because the detector was
inaccurate. They fail on about night three, when the operator has been
interrupted four hundred times by cattle, patrols, rain and moths on the IR
dome, and quietly stops looking. After that the system's accuracy is irrelevant.

So Prahari separates two things that most systems conflate:

    recording an event   - always happens, cheap, complete, auditable
    raising an alert     - costs an operator's attention, and is rationed

Every event is written to the ledger and the database regardless. What this
module decides is which of them are worth interrupting someone for, against a
configured hourly budget. When events arrive faster than the budget allows, the
score threshold rises until the rate fits, so the operator receives the most
significant events rather than the first ones to occur.

Two properties matter for trust:

*   Nothing is discarded. A suppressed event is fully recorded, fully
    synchronised and visible in the event list; it simply did not ring a bell.
    The dashboard shows the suppression count, so the operator can always see
    what the system decided not to bother them with, and why.

*   The threshold is reported. An operator who cannot see that the system is
    rationing will assume it is broken. The current threshold, the budget and
    the suppression rate are all exposed through the status API.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

from prahari.common.models import Event, Priority

log = logging.getLogger("prahari.alerting")

# Events at or above this priority always alert, whatever the budget. A camera
# being blinded or a feed being replayed is never something to ration, because
# the whole point is that those events mean the system itself is under attack.
ALWAYS_ALERT_AT = Priority.CRITICAL


@dataclass
class AlertDecision:
    alerted: bool
    reason: str
    threshold: float


class AlertGovernor:
    """Rate-limits operator interruptions without discarding anything."""

    def __init__(self, budget_per_hour: int, floor: float = 0.25,
                 ceiling: float = 0.92) -> None:
        self.budget_per_hour = max(1, budget_per_hour)
        self.floor = floor
        self.ceiling = ceiling
        self.threshold = floor
        self._alerted: deque[float] = deque()
        self._suppressed_last_hour = 0
        self._suppressed_times: deque[float] = deque()

    # -- accounting ------------------------------------------------------
    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._alerted and self._alerted[0] < cutoff:
            self._alerted.popleft()
        while self._suppressed_times and self._suppressed_times[0] < cutoff:
            self._suppressed_times.popleft()

    @property
    def alerts_last_hour(self) -> int:
        return len(self._alerted)

    @property
    def suppressed_last_hour(self) -> int:
        return len(self._suppressed_times)

    # -- the decision ----------------------------------------------------
    def decide(self, event: Event) -> AlertDecision:
        now = time.monotonic()
        self._prune(now)

        if event.priority.rank >= ALWAYS_ALERT_AT.rank:
            self._alerted.append(now)
            return AlertDecision(
                True, "critical events are never rate-limited", self.threshold)

        over_budget = len(self._alerted) >= self.budget_per_hour
        self._adapt(over_budget)

        if event.priority_score >= self.threshold:
            self._alerted.append(now)
            return AlertDecision(
                True,
                f"score {event.priority_score:.2f} is at or above the current "
                f"alerting threshold of {self.threshold:.2f}",
                self.threshold,
            )

        self._suppressed_times.append(now)
        return AlertDecision(
            False,
            f"recorded but not alerted: score {event.priority_score:.2f} is below "
            f"the current threshold of {self.threshold:.2f}, which has risen "
            f"because {len(self._alerted)} alerts in the last hour is at the "
            f"configured budget of {self.budget_per_hour}",
            self.threshold,
        )

    def _adapt(self, over_budget: bool) -> None:
        """Raise the bar while over budget, relax it slowly when under.

        Asymmetric on purpose: rise quickly so a sudden flood is contained within
        seconds, fall slowly so the threshold does not oscillate and produce
        bursts of alerts every time the scene quietens for a moment.
        """
        if over_budget:
            self.threshold = min(self.ceiling, self.threshold + 0.04)
        else:
            headroom = 1.0 - (len(self._alerted) / self.budget_per_hour)
            if headroom > 0.4:
                self.threshold = max(self.floor, self.threshold - 0.01)

    # -- reporting -------------------------------------------------------
    def status(self) -> dict:
        now = time.monotonic()
        self._prune(now)
        total = len(self._alerted) + len(self._suppressed_times)
        return {
            "budget_per_hour": self.budget_per_hour,
            "alerts_last_hour": len(self._alerted),
            "suppressed_last_hour": len(self._suppressed_times),
            "suppression_rate": (round(len(self._suppressed_times) / total, 3)
                                 if total else 0.0),
            "current_threshold": round(self.threshold, 3),
            "at_budget": len(self._alerted) >= self.budget_per_hour,
            "note": ("Suppressed events are fully recorded, sealed and "
                     "synchronised - they are not discarded, only not raised as "
                     "interruptions."),
        }

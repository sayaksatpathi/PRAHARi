"""Camera topology — the graph that turns cameras into a corridor.

Cross-camera reasoning needs to know which cameras are neighbours and how long an
object takes to travel between them. That is a directed graph: an edge from CAM-A
to CAM-B says "an object leaving A this way should appear at B in roughly this
long", with a spread, because people do not walk at exactly one speed.

The transition times are learned. Every confirmed handoff the coordinator makes
updates the edge it travelled, so the graph sharpens with use — a sector where
the walk from the fence to the gate actually takes 90 seconds converges on 90
seconds rather than a guess. For a demonstration the graph is seeded with
plausible values so the reasoning has something to work with before it has
observed a week of traffic; a real deployment learns it.

The graph is the difference between "three cameras that each raise their own
events" and "one corridor that knows a subject seen at the fence should reach the
gate, and notices when it doesn't."
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Edge:
    """A directed adjacency: objects leaving `src` are expected at `dst`."""
    src: str
    dst: str
    mean_transition_s: float
    std_transition_s: float
    observations: int = 0
    # Learned reliability: the fraction of exits along this edge that actually
    # arrived. A corridor where objects routinely vanish is itself a signal.
    arrivals: int = 0
    departures: int = 0

    def window(self, sigmas: float = 2.5) -> tuple[float, float]:
        """The (min, max) travel time to accept a handoff, in seconds."""
        lo = max(1.0, self.mean_transition_s - sigmas * self.std_transition_s)
        hi = self.mean_transition_s + sigmas * self.std_transition_s
        return lo, hi

    def timing_score(self, elapsed_s: float) -> float:
        """How well an observed travel time fits this edge, in [0, 1]."""
        if self.std_transition_s <= 0:
            return 1.0 if abs(elapsed_s - self.mean_transition_s) < 2 else 0.0
        z = (elapsed_s - self.mean_transition_s) / self.std_transition_s
        return float(math.exp(-0.5 * z * z))

    def observe_transition(self, elapsed_s: float) -> None:
        """Update the learned transition time with a confirmed handoff (Welford)."""
        self.observations += 1
        self.arrivals += 1
        k = self.observations
        delta = elapsed_s - self.mean_transition_s
        self.mean_transition_s += delta / k
        # Running std via exponential smoothing; good enough and bounded.
        self.std_transition_s = max(
            2.0, 0.8 * self.std_transition_s + 0.2 * abs(delta))

    @property
    def arrival_rate(self) -> float:
        return self.arrivals / self.departures if self.departures else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "src": self.src, "dst": self.dst,
            "mean_transition_s": round(self.mean_transition_s, 1),
            "std_transition_s": round(self.std_transition_s, 1),
            "observations": self.observations,
            "arrival_rate": round(self.arrival_rate, 3),
        }


class Topology:
    """The camera adjacency graph."""

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], Edge] = {}

    def add_edge(self, src: str, dst: str, mean_transition_s: float,
                 std_transition_s: float | None = None) -> Edge:
        edge = Edge(src=src, dst=dst, mean_transition_s=mean_transition_s,
                    std_transition_s=std_transition_s
                    if std_transition_s is not None
                    else max(3.0, mean_transition_s * 0.35))
        self._edges[(src, dst)] = edge
        return edge

    def edge(self, src: str, dst: str) -> Edge | None:
        return self._edges.get((src, dst))

    def edges_from(self, src: str) -> list[Edge]:
        return [e for (s, _), e in self._edges.items() if s == src]

    def edges_to(self, dst: str) -> list[Edge]:
        return [e for (_, d), e in self._edges.items() if d == dst]

    def neighbours(self, src: str) -> list[str]:
        return [e.dst for e in self.edges_from(src)]

    def all_edges(self) -> list[Edge]:
        return list(self._edges.values())

    def as_dict(self) -> dict[str, Any]:
        return {"edges": [e.as_dict() for e in self._edges.values()]}


def demo_topology() -> Topology:
    """A plausible corridor for the demo fleet.

    The lawful approach runs from the perimeter fence (CAM-014) past the forest
    approach track (CAM-022) to the main gate (CAM-011); the legacy south track
    (CAM-045) feeds the gate by a separate path. Times are seeded and will be
    refined by observed handoffs. Bidirectional, because people leave as well as
    arrive.
    """
    t = Topology()
    corridor = [
        ("CAM-014", "CAM-022", 35.0),
        ("CAM-022", "CAM-011", 50.0),
        ("CAM-045", "CAM-011", 40.0),
    ]
    for src, dst, mean in corridor:
        t.add_edge(src, dst, mean)
        t.add_edge(dst, src, mean)   # the return leg
    return t

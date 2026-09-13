"""Image-space and ground-plane geometry helpers.

All zone tests operate in normalised image coordinates (0..1 on both axes) so a
zone drawn once survives a camera being reconfigured from 1080p to 720p, which
happens routinely when bandwidth is squeezed.
"""
from __future__ import annotations

import math

from prahari.common.models import Point


def point_in_polygon(px: float, py: float, poly: list[Point]) -> bool:
    """Standard ray-casting test. Points exactly on an edge are treated as inside."""
    if len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i].x, poly[i].y
        xj, yj = poly[j].x, poly[j].y
        if (yi > py) != (yj > py):
            x_at = (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi
            if px < x_at:
                inside = not inside
        j = i
    return inside


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    q1: tuple[float, float], q2: tuple[float, float],
) -> bool:
    """True if segment p1->p2 crosses segment q1->q2."""
    d1 = _orient(*q1, *q2, *p1)
    d2 = _orient(*q1, *q2, *p2)
    d3 = _orient(*p1, *p2, *q1)
    d4 = _orient(*p1, *p2, *q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def side_of_line(p: tuple[float, float], a: Point, b: Point) -> int:
    """Which side of the directed line a->b the point falls on: -1, 0 or +1.

    Used for tripwire directionality: an inbound crossing and an outbound
    crossing of the same virtual line are very different events.
    """
    v = _orient(a.x, a.y, b.x, b.y, p[0], p[1])
    if v > 1e-9:
        return 1
    if v < -1e-9:
        return -1
    return 0


def heading_deg(p_from: tuple[float, float], p_to: tuple[float, float]) -> float | None:
    """Heading in degrees, 0 = East, counter-clockwise positive.

    Image y grows downward, so it is negated to give a conventional compass-like
    frame that an operator reading the UI will not have to think about.
    """
    dx = p_to[0] - p_from[0]
    dy = -(p_to[1] - p_from[1])
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx)) % 360.0


def angular_difference(a: float, b: float) -> float:
    """Smallest absolute difference between two headings, in degrees (0..180)."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def compass_label(deg: float | None) -> str:
    if deg is None:
        return "stationary"
    names = ["East", "North-East", "North", "North-West",
             "West", "South-West", "South", "South-East"]
    idx = int(((deg % 360.0) + 22.5) // 45.0) % 8
    return names[idx]


def polyline_length(points: list[Point]) -> float:
    return sum(
        math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)
        for i in range(len(points) - 1)
    )


def distance_point_to_segment(p: tuple[float, float], a: Point, b: Point) -> float:
    ax, ay, bx, by = a.x, a.y, b.x, b.y
    dx, dy = bx - ax, by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def distance_to_polyline(p: tuple[float, float], points: list[Point]) -> float:
    if len(points) < 2:
        return math.hypot(p[0] - points[0].x, p[1] - points[0].y) if points else 1.0
    return min(
        distance_point_to_segment(p, points[i], points[i + 1])
        for i in range(len(points) - 1)
    )

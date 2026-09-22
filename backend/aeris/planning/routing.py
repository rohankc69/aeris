"""Transit routing around restricted regions.

Coverage sweeps stay inside their (convex) zone, but the leg from a drone's current position
to the first waypoint of a plan can cross a no-fly pocket. ``detour`` finds a short path around
convex obstacles by trying buffered bounding-box corners, deterministically and with a bounded
search. It is not a general planner; it is enough for the isolated pockets an operator draws.
"""

from __future__ import annotations

import itertools
import math

from shapely.geometry import LineString, Point, Polygon

XY = tuple[float, float]


def crosses(a: XY, b: XY, obstacles: list[Polygon]) -> bool:
    seg = LineString([a, b])
    return any(seg.intersects(o) for o in obstacles)


def detour(start: XY, goal: XY, obstacles: list[Polygon], *, clearance_m: float = 25.0) -> list[XY]:
    """Intermediate points routing ``start → goal`` clear of ``obstacles``.

    Returns ``[]`` when the direct leg is already clear or when no one- or two-corner detour
    clears every obstacle; callers treat the latter as an unroutable plan.
    """
    blocking = [o for o in obstacles if LineString([start, goal]).intersects(o)]
    if not blocking:
        return []
    corners: list[XY] = []
    for o in blocking:
        min_x, min_y, max_x, max_y = o.buffer(clearance_m).bounds
        corners.extend([(min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)])
    corners = [c for c in corners if not any(o.contains(Point(c)) for o in obstacles)]

    def clear(path: list[XY]) -> bool:
        return all(not crosses(a, b, obstacles) for a, b in itertools.pairwise(path))

    def length(path: list[XY]) -> float:
        return sum(math.dist(a, b) for a, b in itertools.pairwise(path))

    best: list[XY] | None = None
    for via in corners:
        path = [start, via, goal]
        if clear(path) and (best is None or length(path) < length(best)):
            best = path
    if best is None:
        for via1, via2 in itertools.permutations(corners, 2):
            path = [start, via1, via2, goal]
            if clear(path) and (best is None or length(path) < length(best)):
                best = path
    return best[1:-1] if best else []

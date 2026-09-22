"""Search-area partitioning.

MVP: a metric grid clipped to the search polygon. Zone ids are ``<row letter><column number>``
(``A1``, ``B3``) with row A at the north edge, matching what an operator expects on a map.
"""

from __future__ import annotations

import math
from typing import Protocol

from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import split

from aeris.domain.geo import GeoPolygon
from aeris.domain.models import SearchZone
from aeris.planning.geo_frame import LocalFrame


class Partitioner(Protocol):
    def partition(
        self, area: GeoPolygon, *, restricted: tuple[GeoPolygon, ...] = ()
    ) -> list[SearchZone]: ...


def _row_label(index: int) -> str:
    label = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


class GridPartitioner:
    """Square-cell grid clipped to the polygon. Slivers below ``min_area_fraction`` are dropped."""

    def __init__(
        self,
        cell_size_m: float = 250.0,
        min_area_fraction: float = 0.05,
        restricted_clearance_m: float = 20.0,
    ) -> None:
        if cell_size_m <= 0:
            msg = "cell_size_m must be positive"
            raise ValueError(msg)
        if not 0 <= min_area_fraction < 1:
            msg = "min_area_fraction must be in [0, 1)"
            raise ValueError(msg)
        self.cell_size_m = cell_size_m
        self.min_area_fraction = min_area_fraction
        self.restricted_clearance_m = restricted_clearance_m

    def partition(
        self, area: GeoPolygon, *, restricted: tuple[GeoPolygon, ...] = ()
    ) -> list[SearchZone]:
        frame = LocalFrame.for_polygon(area)
        local = frame.polygon_to_local(area)
        if not local.is_valid or local.area <= 0:
            msg = "search polygon is not a valid simple polygon"
            raise ValueError(msg)
        # Keep a standoff from no-fly regions so sweep endpoints never touch their boundary.
        obstacles = [
            frame.polygon_to_local(r).buffer(self.restricted_clearance_m, join_style="mitre")
            for r in restricted
        ]
        searchable: BaseGeometry = local
        for obstacle in obstacles:
            searchable = searchable.difference(obstacle)
        if searchable.is_empty:
            return []
        min_x, min_y, max_x, max_y = local.bounds
        cols = max(1, math.ceil((max_x - min_x) / self.cell_size_m))
        rows = max(1, math.ceil((max_y - min_y) / self.cell_size_m))
        min_cell_area = self.min_area_fraction * self.cell_size_m**2

        zones: list[SearchZone] = []
        for row in range(rows):
            top = max_y - row * self.cell_size_m
            bottom = top - self.cell_size_m
            for col in range(cols):
                left = min_x + col * self.cell_size_m
                cell = box(left, bottom, left + self.cell_size_m, top)
                pieces = _convex_pieces(cell.intersection(searchable), obstacles)
                pieces = [pc for pc in pieces if pc.area >= min_cell_area]
                for index, piece in enumerate(pieces):
                    suffix = "" if index == 0 else chr(ord("a") + index)
                    zones.append(
                        SearchZone(
                            zone_id=f"{_row_label(row)}{col + 1}{suffix}",
                            polygon=frame.polygon_to_geo(piece),
                        )
                    )
        return zones


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [g for g in getattr(geometry, "geoms", []) if isinstance(g, Polygon)]


def _convex_pieces(geometry: BaseGeometry, obstacles: list[Polygon]) -> list[Polygon]:
    """Split a clipped cell along the edges of any obstacle it touches.

    A rectangular cell minus axis-aligned obstacles splits into rectangles, which keeps every
    zone convex so a lawnmower sweep never has to cross the pocket between rows.
    """
    pieces = _polygons(geometry)
    if not obstacles:
        return pieces
    result: list[Polygon] = []
    for piece in pieces:
        min_x, min_y, max_x, max_y = piece.bounds
        cutters: list[LineString] = []
        for o in obstacles:
            if not o.intersects(piece.buffer(1e-6)):
                continue
            o_min_x, o_min_y, o_max_x, o_max_y = o.bounds
            for x in (o_min_x, o_max_x):
                if min_x < x < max_x:
                    cutters.append(LineString([(x, min_y - 1), (x, max_y + 1)]))
            for y in (o_min_y, o_max_y):
                if min_y < y < max_y:
                    cutters.append(LineString([(min_x - 1, y), (max_x + 1, y)]))
        if not cutters:
            result.append(piece)
            continue
        result.extend(
            sorted(
                _polygons(split(piece, MultiLineString(cutters))),
                key=lambda g: (-g.bounds[3], g.bounds[0]),
            )
        )
    return result


def _largest_polygon(geometry: BaseGeometry) -> Polygon | None:
    if geometry.is_empty:
        return None
    if isinstance(geometry, Polygon):
        return geometry
    polygons = [g for g in getattr(geometry, "geoms", []) if isinstance(g, Polygon)]
    return max(polygons, key=lambda g: g.area) if polygons else None

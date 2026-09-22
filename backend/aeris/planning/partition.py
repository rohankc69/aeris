"""Search-area partitioning.

MVP: a metric grid clipped to the search polygon. Zone ids are ``<row letter><column number>``
(``A1``, ``B3``) with row A at the north edge, matching what an operator expects on a map.
"""

from __future__ import annotations

import math
from typing import Protocol

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from aeris.domain.geo import GeoPolygon
from aeris.domain.models import SearchZone
from aeris.planning.geo_frame import LocalFrame


class Partitioner(Protocol):
    def partition(self, area: GeoPolygon) -> list[SearchZone]: ...


def _row_label(index: int) -> str:
    label = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


class GridPartitioner:
    """Square-cell grid clipped to the polygon. Slivers below ``min_area_fraction`` are dropped."""

    def __init__(self, cell_size_m: float = 250.0, min_area_fraction: float = 0.05) -> None:
        if cell_size_m <= 0:
            msg = "cell_size_m must be positive"
            raise ValueError(msg)
        if not 0 <= min_area_fraction < 1:
            msg = "min_area_fraction must be in [0, 1)"
            raise ValueError(msg)
        self.cell_size_m = cell_size_m
        self.min_area_fraction = min_area_fraction

    def partition(self, area: GeoPolygon) -> list[SearchZone]:
        frame = LocalFrame.for_polygon(area)
        local = frame.polygon_to_local(area)
        if not local.is_valid or local.area <= 0:
            msg = "search polygon is not a valid simple polygon"
            raise ValueError(msg)
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
                clipped = _largest_polygon(cell.intersection(local))
                if clipped is None or clipped.area < min_cell_area:
                    continue
                zones.append(
                    SearchZone(
                        zone_id=f"{_row_label(row)}{col + 1}",
                        polygon=frame.polygon_to_geo(clipped),
                    )
                )
        return zones


def _largest_polygon(geometry: BaseGeometry) -> Polygon | None:
    if geometry.is_empty:
        return None
    if isinstance(geometry, Polygon):
        return geometry
    polygons = [g for g in getattr(geometry, "geoms", []) if isinstance(g, Polygon)]
    return max(polygons, key=lambda g: g.area) if polygons else None

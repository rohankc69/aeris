"""Local tangent-plane projection.

Search areas are a few kilometres across, so an equirectangular projection centred on the
area introduces sub-metre error, which is far below camera footprint size. Planning happens in
this metric frame; results are converted back to WGS84 at the boundary.
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon

from aeris.domain.geo import EARTH_RADIUS_M, GeoPoint, GeoPolygon


class LocalFrame:
    """East/north metres relative to an origin point."""

    def __init__(self, origin: GeoPoint) -> None:
        self.origin = origin
        self._lat0 = math.radians(origin.latitude)
        self._lon0 = math.radians(origin.longitude)
        self._cos_lat0 = math.cos(self._lat0)

    @classmethod
    def for_polygon(cls, polygon: GeoPolygon) -> LocalFrame:
        return cls(polygon.centroid)

    def to_local(self, point: GeoPoint) -> tuple[float, float]:
        x = (math.radians(point.longitude) - self._lon0) * self._cos_lat0 * EARTH_RADIUS_M
        y = (math.radians(point.latitude) - self._lat0) * EARTH_RADIUS_M
        return x, y

    def to_geo(self, x: float, y: float, altitude_m: float = 0.0) -> GeoPoint:
        lat = math.degrees(self._lat0 + y / EARTH_RADIUS_M)
        lon = math.degrees(self._lon0 + x / (EARTH_RADIUS_M * self._cos_lat0))
        return GeoPoint(latitude=lat, longitude=lon, altitude_m=altitude_m)

    def polygon_to_local(self, polygon: GeoPolygon) -> Polygon:
        return Polygon([self.to_local(v) for v in polygon.vertices])

    def polygon_to_geo(self, polygon: Polygon) -> GeoPolygon:
        coords = list(polygon.exterior.coords)
        return GeoPolygon(vertices=tuple(self.to_geo(x, y) for x, y in coords))

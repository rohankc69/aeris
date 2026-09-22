"""Geographic primitives in WGS84 (latitude/longitude in degrees, altitude in metres).

Planning algorithms convert these to a local metric frame; the domain stays geographic.
"""

from __future__ import annotations

import math
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EARTH_RADIUS_M = 6_371_008.8


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float = 0.0

    @model_validator(mode="after")
    def _finite(self) -> Self:
        for value in (self.latitude, self.longitude, self.altitude_m):
            if not math.isfinite(value):
                msg = "coordinates must be finite"
                raise ValueError(msg)
        return self

    def distance_to(self, other: GeoPoint) -> float:
        """Great-circle ground distance in metres (haversine)."""
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(other.latitude), math.radians(other.longitude)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class BoundingBox(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_latitude: float
    min_longitude: float
    max_latitude: float
    max_longitude: float

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.min_latitude <= point.latitude <= self.max_latitude
            and self.min_longitude <= point.longitude <= self.max_longitude
        )


class GeoPolygon(BaseModel):
    """A simple polygon defined by its exterior ring. The ring is not required to be closed."""

    model_config = ConfigDict(frozen=True)

    vertices: tuple[GeoPoint, ...] = Field(min_length=3)

    @model_validator(mode="after")
    def _drop_closing_vertex(self) -> Self:
        first, last = self.vertices[0], self.vertices[-1]
        if (
            len(self.vertices) > 3
            and first.latitude == last.latitude
            and first.longitude == last.longitude
        ):
            object.__setattr__(self, "vertices", self.vertices[:-1])
        return self

    @property
    def bounding_box(self) -> BoundingBox:
        lats = [v.latitude for v in self.vertices]
        lons = [v.longitude for v in self.vertices]
        return BoundingBox(
            min_latitude=min(lats),
            min_longitude=min(lons),
            max_latitude=max(lats),
            max_longitude=max(lons),
        )

    @property
    def centroid(self) -> GeoPoint:
        """Vertex-average centroid; adequate for the small areas AERIS works with."""
        n = len(self.vertices)
        return GeoPoint(
            latitude=sum(v.latitude for v in self.vertices) / n,
            longitude=sum(v.longitude for v in self.vertices) / n,
        )

    def coordinates(self) -> list[tuple[float, float]]:
        """Ring as ``(longitude, latitude)`` pairs, closed, in GeoJSON order."""
        ring = [(v.longitude, v.latitude) for v in self.vertices]
        return [*ring, ring[0]]

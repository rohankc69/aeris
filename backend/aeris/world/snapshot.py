"""Immutable snapshot of world state taken at a known instant.

Decisions, planning, and safety validation operate on snapshots, never on the live service,
so the exact inputs to any action can be reconstructed from the snapshot hash.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeris.domain.enums import LinkState
from aeris.domain.models import (
    CandidateSurvivor,
    Detection,
    Drone,
    DroneState,
    Mission,
    MissionAssignment,
    SearchZone,
)


class DroneView(BaseModel):
    """A drone, its last known state, and how old that state is at snapshot time."""

    model_config = ConfigDict(frozen=True)

    drone: Drone
    state: DroneState | None
    telemetry_age_s: float | None

    @property
    def link_state(self) -> LinkState:
        return self.state.link_state if self.state else LinkState.LOST

    @property
    def is_available_for_assignment(self) -> bool:
        return self.state is not None and self.state.is_available_for_assignment


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    taken_at: datetime
    mission: Mission
    drones: tuple[DroneView, ...]
    zones: tuple[SearchZone, ...]
    detections: tuple[Detection, ...] = ()
    candidates: tuple[CandidateSurvivor, ...] = ()
    assignments: tuple[MissionAssignment, ...] = ()
    snapshot_hash: str = Field(default="")

    def drone(self, drone_id: str) -> DroneView | None:
        return next((d for d in self.drones if d.drone.drone_id == drone_id), None)

    def zone(self, zone_id: str) -> SearchZone | None:
        return next((z for z in self.zones if z.zone_id == zone_id), None)

    @property
    def coverage_fraction(self) -> float:
        """Area-weighted would be better; zones are near-equal so a mean is adequate for MVP."""
        if not self.zones:
            return 0.0
        return sum(z.coverage for z in self.zones) / len(self.zones)

    @property
    def open_candidates(self) -> tuple[CandidateSurvivor, ...]:
        return tuple(c for c in self.candidates if c.confirmed is None)


def compute_hash(snapshot: WorldSnapshot) -> str:
    payload = snapshot.model_dump_json(exclude={"snapshot_hash"})
    return hashlib.sha256(payload.encode()).hexdigest()[:16]

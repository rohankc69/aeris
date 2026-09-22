"""Scenario file schema and loader.

Scenarios live in ``sim/scenarios/*.yaml`` and describe the mission, the fleet, and a
timeline of events. Nothing about a scenario is hardcoded in application logic.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aeris.domain.enums import DetectionSource
from aeris.domain.geo import GeoPoint, GeoPolygon
from aeris.domain.models import Drone, DroneCapability, Mission, SearchArea


class ScenarioEventType(StrEnum):
    """Timeline events the simulation runner knows how to apply.

    Phase 1 supports battery and link manipulation. Detection events arrive in Phase 5.
    """

    BATTERY_DRAIN_MULTIPLIER = "battery_drain_multiplier"
    BATTERY_SET = "battery_set"
    LINK_SET = "link_set"
    DETECTION = "detection"
    OPERATOR_CONFIRM = "operator_confirm"
    OPERATOR_REJECT = "operator_reject"

    @property
    def needs_drone(self) -> bool:
        return self not in {ScenarioEventType.OPERATOR_CONFIRM, ScenarioEventType.OPERATOR_REJECT}


class ScenarioEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at_s: float = Field(ge=0, description="Seconds after mission start")
    type: ScenarioEventType
    drone_id: str | None = None
    value: float | bool = 0.0
    source: DetectionSource | None = Field(default=None, description="detection events only")
    position: GeoPoint | None = Field(
        default=None, description="detection events only; defaults to the missing person"
    )
    movement: bool = False
    note: str = ""

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.type.needs_drone and not self.drone_id:
            msg = f"{self.type} event needs a drone_id"
            raise ValueError(msg)
        if self.type is ScenarioEventType.DETECTION and self.source is None:
            msg = "detection event needs a source"
            raise ValueError(msg)
        return self


class ScenarioDrone(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    drone_id: str
    name: str | None = None
    capability: DroneCapability = Field(default_factory=DroneCapability)
    battery_percent: float = Field(100.0, ge=0, le=100)
    battery_drain_percent_per_s: float | None = Field(
        default=None, description="Defaults to 100 / nominal_endurance_s"
    )
    start_position: GeoPoint | None = None

    def to_drone(self) -> Drone:
        return Drone(
            drone_id=self.drone_id, name=self.name or self.drone_id, capability=self.capability
        )

    @property
    def drain_per_s(self) -> float:
        if self.battery_drain_percent_per_s is not None:
            return self.battery_drain_percent_per_s
        return 100.0 / self.capability.nominal_endurance_s


class MissingPerson(BaseModel):
    """A simulated person the fake sensors can detect (**simulated** perception)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: GeoPoint
    detection_range_m: float = Field(60.0, gt=0)
    thermal_confidence: float = Field(0.55, ge=0, le=1)
    visual_confidence: float = Field(0.35, ge=0, le=1)
    sighting_cooldown_s: float = Field(20.0, gt=0)


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    seed: int = 0
    base_position: GeoPoint
    search_polygon: GeoPolygon
    restricted_regions: tuple[GeoPolygon, ...] = ()
    last_known_position: GeoPoint | None = None
    missing_person: MissingPerson | None = None
    search_altitude_m: float = Field(60.0, gt=0)
    overlap_fraction: float = Field(0.2, ge=0, lt=1)
    zone_size_m: float = Field(250.0, gt=0)
    tick_s: float = Field(1.0, gt=0)
    max_duration_s: float = Field(3600.0, gt=0)
    drones: tuple[ScenarioDrone, ...] = Field(min_length=1)
    events: tuple[ScenarioEvent, ...] = ()

    @model_validator(mode="after")
    def _unique_drones_and_known_event_targets(self) -> Self:
        ids = [d.drone_id for d in self.drones]
        if len(set(ids)) != len(ids):
            msg = "drone ids must be unique"
            raise ValueError(msg)
        for event in self.events:
            if event.drone_id is not None and event.drone_id not in ids:
                msg = f"event at {event.at_s}s targets unknown drone {event.drone_id}"
                raise ValueError(msg)
            if (
                event.type is ScenarioEventType.DETECTION
                and event.position is None
                and self.missing_person is None
            ):
                msg = f"detection event at {event.at_s}s needs a position or a missing_person"
                raise ValueError(msg)
        return self

    def to_mission(self, *, created_at: datetime | None = None) -> Mission:
        extra: dict[str, object] = {"created_at": created_at} if created_at else {}
        return Mission(
            name=self.name,
            search_area=SearchArea(
                polygon=self.search_polygon,
                search_altitude_m=self.search_altitude_m,
                overlap_fraction=self.overlap_fraction,
            ),
            base_position=self.base_position,
            restricted_regions=self.restricted_regions,
            last_known_position=self.last_known_position,
            drone_ids=tuple(d.drone_id for d in self.drones),
            **extra,
        )


def default_scenario_dir() -> Path:
    override = os.environ.get("AERIS_SCENARIO_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "sim" / "scenarios"


def list_scenarios(directory: Path | None = None) -> list[str]:
    directory = directory or default_scenario_dir()
    return sorted(p.stem for p in directory.glob("*.yaml"))


def load_scenario(name_or_path: str | Path, directory: Path | None = None) -> Scenario:
    path = Path(name_or_path)
    if path.suffix != ".yaml":
        path = (directory or default_scenario_dir()) / f"{path.name}.yaml"
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Scenario.model_validate(raw)

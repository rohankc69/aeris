"""Scenario runner: wires a full local AERIS stack around the fake fleet and steps it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel

from aeris.clock import SimClock
from aeris.config import Settings, load_settings
from aeris.decisions.base import DecisionProvider
from aeris.decisions.engine import DecisionEngine
from aeris.decisions.factory import build_decision_provider
from aeris.decisions.rules import RuleBasedDecisionProvider
from aeris.domain.enums import MissionStatus
from aeris.events.bus import EventBus, InMemoryEventBus
from aeris.events.events import DomainEvent
from aeris.fleet.fake import FakeFleetAdapter, SimDroneConfig, SimSensorTarget
from aeris.mission.manager import MissionManager
from aeris.planning.assignment import GreedyAssignmentStrategy
from aeris.planning.coverage import BoustrophedonPlanner
from aeris.planning.partition import GridPartitioner
from aeris.safety.governor import SafetyGovernor
from aeris.simulation.scenario import Scenario, ScenarioEvent, ScenarioEventType
from aeris.world.service import WorldStateService
from aeris.world.snapshot import WorldSnapshot


class SimulationSummary(BaseModel):
    scenario: str
    seed: int
    final_status: MissionStatus
    elapsed_s: float
    ticks: int
    coverage_fraction: float
    zones_total: int
    zones_complete: int
    reassignments: int
    detections: int
    candidates: int
    person_located_at_s: float | None
    decisions: int
    safety_overrides: int
    decision_provider: str
    events_applied: int
    event_counts: dict[str, int]


class ScenarioRunner:
    """Builds the stack for one scenario and advances it tick by tick.

    Use ``step()`` for fine control, ``run()`` to run headless to completion, or
    ``run_realtime()`` to pace the simulation against the wall clock for a dashboard.
    """

    def __init__(
        self,
        scenario: Scenario,
        *,
        settings: Settings | None = None,
        decision_provider: DecisionProvider | None = None,
        bus: EventBus | None = None,
        start: datetime | None = None,
    ) -> None:
        self.scenario = scenario
        self.clock = SimClock(start)
        self.bus = bus or InMemoryEventBus()
        self.settings = settings or load_settings()
        self._safety = self.settings.safety
        self.decision_provider = decision_provider or build_decision_provider(
            self.settings, clock=self.clock
        )
        self._elapsed_s = 0.0
        self._ticks = 0
        self._pending_events = sorted(scenario.events, key=lambda e: e.at_s)
        self._applied_events = 0
        self._person_located_at_s: float | None = None
        self._event_counts: dict[str, int] = {}
        self.bus.subscribe(None, self._count_event)

        mission = scenario.to_mission(created_at=self.clock.now())
        zones = GridPartitioner(
            cell_size_m=scenario.zone_size_m,
            restricted_clearance_m=self._safety.restricted_clearance_m,
        ).partition(mission.search_area.polygon, restricted=mission.restricted_regions)
        self.world = WorldStateService(
            mission=mission, zones=zones, bus=self.bus, clock=self.clock, safety=self._safety
        )
        self.fleet = FakeFleetAdapter(
            base_position=scenario.base_position,
            clock=self.clock,
            drones=[
                SimDroneConfig(
                    drone=d.to_drone(),
                    start_position=d.start_position,
                    battery_percent=d.battery_percent,
                    battery_drain_percent_per_s=d.drain_per_s,
                )
                for d in scenario.drones
            ],
            targets=[
                SimSensorTarget(
                    position=scenario.missing_person.position,
                    range_m=scenario.missing_person.detection_range_m,
                    thermal_confidence=scenario.missing_person.thermal_confidence,
                    visual_confidence=scenario.missing_person.visual_confidence,
                    cooldown_s=scenario.missing_person.sighting_cooldown_s,
                )
            ]
            if scenario.missing_person
            else [],
        )
        self.manager = MissionManager(
            world=self.world,
            fleet=self.fleet,
            coverage_planner=BoustrophedonPlanner(),
            assignment_strategy=GreedyAssignmentStrategy(),
            safety=SafetyGovernor(self._safety),
            clock=self.clock,
            decision_engine=DecisionEngine(
                provider=self.decision_provider,
                policy=RuleBasedDecisionProvider(safety=self._safety),
                clock=self.clock,
            ),
            settings=self.settings,
        )

    @property
    def elapsed_s(self) -> float:
        return self._elapsed_s

    async def setup(self) -> None:
        for d in self.scenario.drones:
            await self.world.register_drone(d.to_drone())
        await self.manager.tick()  # initial telemetry before start

    async def start(self) -> None:
        await self.manager.start()

    async def step(self) -> WorldSnapshot:
        dt = self.scenario.tick_s
        self.clock.advance(dt)
        self._elapsed_s += dt
        self._ticks += 1
        self.fleet.advance(dt)
        await self._apply_due_events()
        return await self.manager.tick()

    async def run(
        self, on_tick: Callable[[WorldSnapshot], None] | None = None
    ) -> SimulationSummary:
        await self.setup()
        await self.start()
        while (
            not self.world.mission.status.is_terminal
            and self._elapsed_s < self.scenario.max_duration_s
        ):
            snap = await self.step()
            if on_tick:
                on_tick(snap)
        return self.summary()

    async def run_realtime(
        self, *, time_scale: float = 1.0, on_tick: Callable[[WorldSnapshot], None] | None = None
    ) -> SimulationSummary:
        await self.setup()
        await self.start()
        while (
            not self.world.mission.status.is_terminal
            and self._elapsed_s < self.scenario.max_duration_s
        ):
            snap = await self.step()
            if on_tick:
                on_tick(snap)
            await asyncio.sleep(self.scenario.tick_s / time_scale)
        return self.summary()

    def summary(self) -> SimulationSummary:
        snap = self.world.snapshot()
        return SimulationSummary(
            scenario=self.scenario.name,
            seed=self.scenario.seed,
            final_status=snap.mission.status,
            elapsed_s=self._elapsed_s,
            ticks=self._ticks,
            coverage_fraction=snap.coverage_fraction,
            zones_total=len(snap.zones),
            zones_complete=sum(z.status.value == "COMPLETE" for z in snap.zones),
            reassignments=self._event_counts.get("ZoneReassignmentRequested", 0),
            detections=len(snap.detections),
            candidates=len(snap.candidates),
            person_located_at_s=self._person_located_at_s,
            decisions=len(self.world.decisions),
            safety_overrides=len(self.world.safety_events),
            decision_provider=self.decision_provider.name,
            events_applied=self._applied_events,
            event_counts=dict(sorted(self._event_counts.items())),
        )

    # ------------------------------------------------------------------ internals

    async def _apply_due_events(self) -> None:
        while self._pending_events and self._pending_events[0].at_s <= self._elapsed_s:
            await self._apply(self._pending_events.pop(0))
            self._applied_events += 1

    async def _apply(self, event: ScenarioEvent) -> None:
        drone_id = event.drone_id or ""
        if event.type is ScenarioEventType.BATTERY_DRAIN_MULTIPLIER:
            self.fleet.set_battery_drain_multiplier(drone_id, float(event.value))
        elif event.type is ScenarioEventType.BATTERY_SET:
            self.fleet.set_battery(drone_id, float(event.value))
        elif event.type is ScenarioEventType.LINK_SET:
            self.fleet.set_link(drone_id, online=bool(event.value))
        elif event.type is ScenarioEventType.DETECTION:
            position = event.position or (
                self.scenario.missing_person.position if self.scenario.missing_person else None
            )
            if position is not None and event.source is not None:
                self.fleet.inject_observation(
                    drone_id,
                    source=event.source,
                    confidence=float(event.value),
                    position=position,
                    movement_observed=event.movement,
                )
        elif event.type in {ScenarioEventType.OPERATOR_CONFIRM, ScenarioEventType.OPERATOR_REJECT}:
            open_candidates = self.world.snapshot().open_candidates
            if open_candidates:
                candidate = open_candidates[0]
                if event.type is ScenarioEventType.OPERATOR_CONFIRM:
                    await self.manager.confirm_candidate(candidate.candidate_id, note=event.note)
                else:
                    await self.manager.reject_candidate(candidate.candidate_id, note=event.note)

    async def _count_event(self, event: DomainEvent) -> None:
        self._event_counts[event.type_name] = self._event_counts.get(event.type_name, 0) + 1
        if event.type_name == "SurvivorConfirmed" and self._person_located_at_s is None:
            self._person_located_at_s = self._elapsed_s

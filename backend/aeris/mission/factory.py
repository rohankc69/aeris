"""Build the mission stack once, the same way, for simulated and live fleets."""

from __future__ import annotations

from dataclasses import dataclass

from aeris.clock import Clock
from aeris.config import Settings
from aeris.decisions.base import DecisionProvider
from aeris.decisions.engine import DecisionEngine
from aeris.decisions.factory import build_decision_provider
from aeris.decisions.rules import RuleBasedDecisionProvider
from aeris.domain.models import Mission
from aeris.events.bus import EventBus, InMemoryEventBus
from aeris.fleet.base import FleetAdapter
from aeris.mission.manager import MissionManager
from aeris.planning.assignment import GreedyAssignmentStrategy
from aeris.planning.coverage import BoustrophedonPlanner
from aeris.planning.partition import GridPartitioner
from aeris.safety.governor import SafetyGovernor
from aeris.world.service import WorldStateService


@dataclass
class MissionStack:
    world: WorldStateService
    manager: MissionManager
    bus: EventBus
    decision_provider: DecisionProvider


def build_mission_stack(
    *,
    mission: Mission,
    fleet: FleetAdapter,
    clock: Clock,
    settings: Settings,
    zone_size_m: float,
    decision_provider: DecisionProvider | None = None,
    bus: EventBus | None = None,
) -> MissionStack:
    bus = bus or InMemoryEventBus()
    provider = decision_provider or build_decision_provider(settings, clock=clock)
    zones = GridPartitioner(
        cell_size_m=zone_size_m, restricted_clearance_m=settings.safety.restricted_clearance_m
    ).partition(mission.search_area.polygon, restricted=mission.restricted_regions)
    world = WorldStateService(
        mission=mission, zones=zones, bus=bus, clock=clock, safety=settings.safety
    )
    manager = MissionManager(
        world=world,
        fleet=fleet,
        coverage_planner=BoustrophedonPlanner(),
        assignment_strategy=GreedyAssignmentStrategy(),
        safety=SafetyGovernor(settings.safety),
        clock=clock,
        decision_engine=DecisionEngine(
            provider=provider,
            policy=RuleBasedDecisionProvider(safety=settings.safety),
            clock=clock,
        ),
        settings=settings,
    )
    return MissionStack(world=world, manager=manager, bus=bus, decision_provider=provider)

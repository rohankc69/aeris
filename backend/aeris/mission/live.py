"""Live mission runner: the same mission logic on a real (or SITL) fleet with a wall clock.

Scenario timeline events are simulation-only and are ignored here; the mission definition
(search area, base, restricted regions, fleet roster, last known position) is reused.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from aeris.clock import SystemClock
from aeris.config import Settings
from aeris.decisions.base import DecisionProvider
from aeris.events.bus import EventBus
from aeris.fleet.base import FleetAdapter
from aeris.mission.factory import build_mission_stack
from aeris.simulation.scenario import Scenario
from aeris.world.snapshot import WorldSnapshot


class LiveMissionRunner:
    def __init__(
        self,
        scenario: Scenario,
        *,
        fleet: FleetAdapter,
        settings: Settings,
        decision_provider: DecisionProvider | None = None,
        bus: EventBus | None = None,
        tick_s: float = 1.0,
    ) -> None:
        self.scenario = scenario
        self.clock = SystemClock()
        self.settings = settings
        self.fleet = fleet
        self.tick_s = tick_s
        self._ticks = 0
        stack = build_mission_stack(
            mission=scenario.to_mission(created_at=self.clock.now()),
            fleet=fleet,
            clock=self.clock,
            settings=settings,
            zone_size_m=scenario.zone_size_m,
            decision_provider=decision_provider,
            bus=bus,
        )
        self.world = stack.world
        self.manager = stack.manager
        self.bus = stack.bus
        self.decision_provider = stack.decision_provider
        self._started_at = self.clock.now()

    @property
    def elapsed_s(self) -> float:
        return (self.clock.now() - self._started_at).total_seconds()

    async def setup(self) -> None:
        for d in self.scenario.drones:
            await self.world.register_drone(d.to_drone())
        await self.manager.tick()

    async def start(self) -> None:
        self._started_at = self.clock.now()
        await self.manager.start()

    async def step(self) -> WorldSnapshot:
        self._ticks += 1
        return await self.manager.tick()

    async def run(self, on_tick: Callable[[WorldSnapshot], None] | None = None) -> WorldSnapshot:
        await self.setup()
        await self.start()
        snap = self.world.snapshot()
        while not self.world.mission.status.is_terminal:
            snap = await self.step()
            if on_tick:
                on_tick(snap)
            await asyncio.sleep(self.tick_s)
        return snap

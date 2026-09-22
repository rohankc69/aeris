"""Mission runtime registry: runs scenario-backed missions as background tasks.

Phase 1 missions are always scenario-driven and use the fake fleet. The registry is the
single owner of running tasks and the fan-out point for WebSocket subscribers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from aeris.config import Settings
from aeris.events.events import DomainEvent
from aeris.simulation.runner import ScenarioRunner
from aeris.simulation.scenario import Scenario
from aeris.world.snapshot import WorldSnapshot

logger = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_SIZE = 256


@dataclass
class MissionRuntime:
    runner: ScenarioRunner
    time_scale: float
    task: asyncio.Task[None] | None = None
    subscribers: set[asyncio.Queue[dict[str, object]]] = field(default_factory=set)

    @property
    def mission_id(self) -> str:
        return self.runner.world.mission_id

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self.subscribers.discard(queue)

    def payload(self, snap: WorldSnapshot | None = None) -> dict[str, object]:
        snap = snap or self.runner.world.snapshot()
        return snapshot_payload(
            snap, emergency_stop_active=self.runner.manager.emergency_stop_active
        )

    def broadcast(self, message: dict[str, object]) -> None:
        for queue in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)


class MissionRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._missions: dict[str, MissionRuntime] = {}

    def list(self) -> list[MissionRuntime]:
        return list(self._missions.values())

    def get(self, mission_id: str) -> MissionRuntime | None:
        return self._missions.get(mission_id)

    async def create(self, scenario: Scenario, *, time_scale: float) -> MissionRuntime:
        runner = ScenarioRunner(scenario, settings=self._settings)
        runtime = MissionRuntime(runner=runner, time_scale=time_scale)

        async def forward(event: DomainEvent) -> None:
            runtime.broadcast(
                {
                    "kind": "event",
                    "data": event.model_dump(mode="json") | {"event_type": event.type_name},
                }
            )

        runner.bus.subscribe(None, forward)
        await runner.setup()
        self._missions[runtime.mission_id] = runtime
        return runtime

    async def start(self, runtime: MissionRuntime) -> None:
        if runtime.running:
            return
        await runtime.runner.start()
        runtime.task = asyncio.create_task(
            self._loop(runtime), name=f"mission-{runtime.mission_id}"
        )

    async def _loop(self, runtime: MissionRuntime) -> None:
        runner = runtime.runner
        try:
            while (
                not runner.world.mission.status.is_terminal
                and runner.elapsed_s < runner.scenario.max_duration_s
            ):
                snap = await runner.step()
                runtime.broadcast({"kind": "snapshot", "data": runtime.payload(snap)})
                await asyncio.sleep(runner.scenario.tick_s / runtime.time_scale)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("mission loop failed", extra={"mission_id": runtime.mission_id})
        finally:
            runtime.broadcast(
                {"kind": "snapshot", "data": snapshot_payload(runner.world.snapshot())}
            )

    async def shutdown(self) -> None:
        for runtime in self._missions.values():
            if runtime.task and not runtime.task.done():
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task


def snapshot_payload(
    snap: WorldSnapshot, *, emergency_stop_active: bool = False
) -> dict[str, object]:
    """Compact snapshot for the wire: mission, drones, zones, coverage."""
    return {
        "taken_at": snap.taken_at.isoformat(),
        "snapshot_hash": snap.snapshot_hash,
        "mission": snap.mission.model_dump(mode="json"),
        "coverage_fraction": snap.coverage_fraction,
        "emergency_stop_active": emergency_stop_active,
        "drones": [
            {
                "drone": v.drone.model_dump(mode="json"),
                "state": v.state.model_dump(mode="json") if v.state else None,
                "telemetry_age_s": v.telemetry_age_s,
                "link_state": v.link_state,
            }
            for v in snap.drones
        ],
        "zones": [z.model_dump(mode="json") for z in snap.zones],
        "detections": [d.model_dump(mode="json") for d in snap.detections],
        "candidates": [c.model_dump(mode="json") for c in snap.candidates],
    }

"""Mission Manager control loop.

One ``tick()`` does, in order:

1. pull telemetry from the fleet adapter into world state
2. re-derive link states from telemetry age
3. advance coverage from telemetry position against each drone's plan
4. release zones held by drones that can no longer work them
5. assign open zones to available drones and send plans
6. send idle drones home when nothing is left, and complete the mission when appropriate

Phase 1 scope: deterministic search coordination only. Decision providers (Phase 2) and the
Safety Governor (Phase 3) slot in between steps 4 and 5 without changing this structure.
"""

from __future__ import annotations

import logging

from aeris.clock import Clock
from aeris.domain.enums import (
    AssignmentTask,
    DroneStatus,
    LinkState,
    MissionStatus,
    OperatorActionType,
    ZoneStatus,
)
from aeris.domain.models import MissionAssignment, OperatorAction, WaypointPlan
from aeris.events.bus import EventBus
from aeris.events.events import (
    DroneReturning,
    OperatorActionReceived,
    ZoneAssigned,
    ZoneReassignmentRequested,
)
from aeris.fleet.base import FleetAdapter
from aeris.planning.assignment import AssignmentCandidate, AssignmentStrategy
from aeris.planning.coverage import CoveragePlanner, CoverageRequest
from aeris.planning.progress import PlanProgressTracker
from aeris.world.service import WorldStateService
from aeris.world.snapshot import WorldSnapshot

logger = logging.getLogger(__name__)


class MissionManager:
    def __init__(
        self,
        *,
        world: WorldStateService,
        fleet: FleetAdapter,
        coverage_planner: CoveragePlanner,
        assignment_strategy: AssignmentStrategy,
        bus: EventBus,
        clock: Clock,
    ) -> None:
        self._world = world
        self._fleet = fleet
        self._coverage = coverage_planner
        self._assign = assignment_strategy
        self._bus = bus
        self._clock = clock
        self._trackers: dict[str, PlanProgressTracker] = {}
        self._plans: dict[str, WaypointPlan] = {}

    @property
    def world(self) -> WorldStateService:
        return self._world

    def plan_for(self, drone_id: str) -> WaypointPlan | None:
        return self._plans.get(drone_id)

    # ------------------------------------------------------------------ loop

    async def tick(self) -> WorldSnapshot:
        for frame in await self._fleet.get_telemetry():
            await self._world.apply_telemetry(frame)
        await self._world.refresh_link_states()

        if self._world.mission.status is MissionStatus.ACTIVE:
            await self._advance_coverage()
            await self._release_unworkable_zones()
            await self._assign_open_zones()
            await self._settle_idle_drones()
            await self._check_completion()

        return self._world.snapshot()

    # ------------------------------------------------------------------ operator commands

    async def start(self) -> None:
        await self._record_action(OperatorActionType.START_MISSION)
        await self._world.start_mission()

    async def pause(self) -> None:
        await self._record_action(OperatorActionType.PAUSE_MISSION)
        await self._world.pause_mission()
        for view in self._world.snapshot().drones:
            if view.state and view.state.status is DroneStatus.SEARCHING:
                await self._fleet.hold(view.drone.drone_id)
                self._world.set_drone_status(
                    view.drone.drone_id,
                    DroneStatus.HOLDING,
                    assigned_zone_id=view.state.assigned_zone_id,
                    coverage_completed=view.state.coverage_completed,
                )

    async def resume(self) -> None:
        await self._record_action(OperatorActionType.RESUME_MISSION)
        await self._world.resume_mission()
        for view in self._world.snapshot().drones:
            state = view.state
            if state and state.status is DroneStatus.HOLDING and state.assigned_zone_id:
                plan = self._plans.get(view.drone.drone_id)
                if plan and (await self._fleet.send_mission(view.drone.drone_id, plan)).accepted:
                    self._world.set_drone_status(
                        view.drone.drone_id,
                        DroneStatus.SEARCHING,
                        assigned_zone_id=state.assigned_zone_id,
                        coverage_completed=state.coverage_completed,
                    )

    async def abort(self, reason: str = "operator abort") -> None:
        await self._record_action(OperatorActionType.ABORT_MISSION, note=reason)
        for view in self._world.snapshot().drones:
            if view.state and view.state.status not in {
                DroneStatus.LANDED,
                DroneStatus.UNAVAILABLE,
            }:
                await self._send_home(view.drone.drone_id, reason)
        await self._world.abort_mission(reason)

    async def return_drone(self, drone_id: str) -> None:
        await self._record_action(OperatorActionType.RETURN_DRONE, target_id=drone_id)
        await self._send_home(drone_id, "operator return")

    async def hold_drone(self, drone_id: str) -> None:
        await self._record_action(OperatorActionType.HOLD_DRONE, target_id=drone_id)
        state = self._world.drone_state(drone_id)
        if state is None:
            return
        if (await self._fleet.hold(drone_id)).accepted:
            self._world.set_drone_status(
                drone_id,
                DroneStatus.HOLDING,
                assigned_zone_id=state.assigned_zone_id,
                coverage_completed=state.coverage_completed,
            )

    # ------------------------------------------------------------------ steps

    async def _advance_coverage(self) -> None:
        for drone_id, tracker in list(self._trackers.items()):
            state = self._world.drone_state(drone_id)
            if state is None or state.assigned_zone_id is None:
                continue
            if state.link_state is not LinkState.CONNECTED:
                continue
            coverage = tracker.update(state.position)
            await self._world.update_zone_coverage(state.assigned_zone_id, drone_id, coverage)
            zone = self._world.zone(state.assigned_zone_id)
            if zone.status is ZoneStatus.ASSIGNED:
                await self._world.set_zone_status(zone.zone_id, ZoneStatus.SEARCHING)
            if tracker.complete:
                await self._world.set_zone_status(
                    zone.zone_id, ZoneStatus.COMPLETE, assigned_drone_id=None, coverage=1.0
                )
                self._world.set_drone_status(drone_id, DroneStatus.IDLE, assigned_zone_id=None)
                self._drop_plan(drone_id)

    async def _release_unworkable_zones(self) -> None:
        for view in self._world.snapshot().drones:
            state = view.state
            if state is None or state.assigned_zone_id is None:
                continue
            if state.link_state is LinkState.CONNECTED and state.status in {
                DroneStatus.SEARCHING,
                DroneStatus.HOLDING,
            }:
                continue
            reason = (
                f"link {state.link_state}"
                if state.link_state is not LinkState.CONNECTED
                else f"drone {state.status}"
            )
            await self._release_zone(view.drone.drone_id, reason)

    async def _assign_open_zones(self) -> None:
        snap = self._world.snapshot()
        candidates = [
            AssignmentCandidate(drone=v.drone, state=v.state)
            for v in snap.drones
            if v.state is not None
            and v.is_available_for_assignment
            and v.state.assigned_zone_id is None
        ]
        open_zones = [z for z in snap.zones if z.status.needs_work]
        if not candidates or not open_zones:
            return
        for proposal in self._assign.assign(
            candidates, open_zones, base_position=snap.mission.base_position
        ):
            await self._dispatch(proposal.drone_id, proposal.zone_id, proposal.reason)

    async def _settle_idle_drones(self) -> None:
        snap = self._world.snapshot()
        if any(z.status.needs_work for z in snap.zones):
            return
        for view in snap.drones:
            state = view.state
            if (
                state
                and state.status is DroneStatus.IDLE
                and state.link_state is LinkState.CONNECTED
            ):
                await self._send_home(view.drone.drone_id, "no remaining zones")

    async def _check_completion(self) -> None:
        snap = self._world.snapshot()
        all_done = all(z.status is ZoneStatus.COMPLETE for z in snap.zones)
        if all_done and not snap.open_candidates:
            await self._world.complete_mission(MissionStatus.COMPLETED)

    # ------------------------------------------------------------------ helpers

    async def _dispatch(self, drone_id: str, zone_id: str, reason: str) -> bool:
        snap = self._world.snapshot()
        view = snap.drone(drone_id)
        zone = snap.zone(zone_id)
        if view is None or view.state is None or zone is None:
            return False
        request = CoverageRequest(
            drone_id=drone_id,
            zone_id=zone_id,
            polygon=zone.polygon,
            altitude_m=snap.mission.search_area.search_altitude_m,
            footprint_width_m=view.drone.capability.camera_footprint_width_m,
            overlap_fraction=snap.mission.search_area.overlap_fraction,
            start_fraction=zone.coverage,
            speed_mps=view.drone.capability.cruise_speed_mps,
        )
        plan = self._coverage.plan(request)
        result = await self._fleet.send_mission(drone_id, plan)
        if not result.accepted:
            logger.warning(
                "mission rejected", extra={"drone_id": drone_id, "reason": result.message}
            )
            return False
        assignment = MissionAssignment(
            mission_id=snap.mission.mission_id,
            drone_id=drone_id,
            zone_id=zone_id,
            task=AssignmentTask.SEARCH,
            plan_id=plan.plan_id,
            created_at=self._clock.now(),
            reason=reason,
        )
        self._world.record_assignment(assignment)
        self._plans[drone_id] = plan
        self._trackers[drone_id] = PlanProgressTracker(plan)
        await self._world.set_zone_status(zone_id, ZoneStatus.ASSIGNED, assigned_drone_id=drone_id)
        self._world.set_drone_status(
            drone_id,
            DroneStatus.SEARCHING,
            assigned_zone_id=zone_id,
            coverage_completed=zone.coverage,
        )
        await self._bus.publish(
            ZoneAssigned(
                mission_id=snap.mission.mission_id,
                zone_id=zone_id,
                drone_id=drone_id,
                assignment_id=assignment.assignment_id,
                start_fraction=zone.coverage,
                reason=reason,
            )
        )
        return True

    async def _release_zone(self, drone_id: str, reason: str) -> None:
        state = self._world.drone_state(drone_id)
        if state is None or state.assigned_zone_id is None:
            return
        zone = self._world.zone(state.assigned_zone_id)
        new_status = ZoneStatus.PARTIAL if zone.coverage > 0 else ZoneStatus.UNSEARCHED
        await self._world.set_zone_status(zone.zone_id, new_status, assigned_drone_id=None)
        next_status = (
            DroneStatus.UNAVAILABLE if state.link_state is not LinkState.CONNECTED else state.status
        )
        if next_status in {DroneStatus.SEARCHING, DroneStatus.HOLDING}:
            next_status = DroneStatus.IDLE
        self._world.set_drone_status(drone_id, next_status, assigned_zone_id=None)
        self._drop_plan(drone_id)
        await self._bus.publish(
            ZoneReassignmentRequested(
                mission_id=self._world.mission_id,
                zone_id=zone.zone_id,
                previous_drone_id=drone_id,
                remaining_fraction=1.0 - zone.coverage,
                reason=reason,
            )
        )

    async def _send_home(self, drone_id: str, reason: str) -> None:
        state = self._world.drone_state(drone_id)
        if state is None or state.status in {DroneStatus.RETURNING, DroneStatus.LANDED}:
            return
        if state.assigned_zone_id is not None:
            await self._release_zone(drone_id, reason)
        result = await self._fleet.return_to_base(drone_id)
        if result.accepted:
            self._world.set_drone_status(drone_id, DroneStatus.RETURNING, assigned_zone_id=None)
            await self._bus.publish(
                DroneReturning(mission_id=self._world.mission_id, drone_id=drone_id, reason=reason)
            )

    def _drop_plan(self, drone_id: str) -> None:
        self._trackers.pop(drone_id, None)
        self._plans.pop(drone_id, None)

    async def _record_action(
        self, action_type: OperatorActionType, *, target_id: str | None = None, note: str = ""
    ) -> None:
        action = OperatorAction(
            mission_id=self._world.mission_id,
            action_type=action_type,
            target_id=target_id,
            note=note,
            timestamp=self._clock.now(),
        )
        self._world.record_operator_action(action)
        await self._bus.publish(
            OperatorActionReceived(
                mission_id=self._world.mission_id,
                action_id=action.action_id,
                action_type=action_type,
                target_id=target_id,
            )
        )

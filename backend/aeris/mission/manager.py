"""Mission Manager control loop.

One ``tick()`` does, in order:

1. pull telemetry from the fleet adapter into world state
2. re-derive link states from telemetry age
3. advance coverage from telemetry position against each drone's plan
4. release zones held by drones that can no longer work them
5. ask the decision engine (if configured) the bounded ``drone_disposition`` question for
   each due searching drone; every proposal is validated by the Safety Governor before it
   acts, and the ``DecisionRecord`` carries the policy opinion, override flag and final action
6. enforce the Safety Governor's deterministic rules on every drone, AI or not
7. assign open zones to available drones and send plans
8. send idle drones home when nothing is left, and complete the mission when appropriate
"""

from __future__ import annotations

import logging
from datetime import datetime

from aeris.clock import Clock
from aeris.config import DecisionSettings
from aeris.decisions.engine import DecisionEngine
from aeris.decisions.modules import DroneDispositionInput, ZonePriorityInput
from aeris.domain.enums import (
    AssignmentTask,
    Disposition,
    DroneStatus,
    LinkState,
    MissionStatus,
    OperatorActionType,
    ZoneStatus,
)
from aeris.domain.models import (
    DroneState,
    MissionAssignment,
    OperatorAction,
    SafetyEvent,
    SearchZone,
    Waypoint,
    WaypointPlan,
)
from aeris.events.events import (
    DecisionCompleted,
    DroneReturning,
    HumanReviewRequested,
    OperatorActionReceived,
    SafetyOverrideTriggered,
    ZoneAssigned,
    ZoneReassignmentRequested,
)
from aeris.fleet.base import FleetAdapter
from aeris.planning.assignment import AssignmentCandidate, AssignmentStrategy
from aeris.planning.coverage import CoveragePlanner, CoverageRequest
from aeris.planning.geo_frame import LocalFrame
from aeris.planning.progress import PlanProgressTracker
from aeris.planning.routing import detour
from aeris.safety.governor import SafetyGovernor
from aeris.world.service import WorldStateService
from aeris.world.snapshot import DroneView, WorldSnapshot

logger = logging.getLogger(__name__)

SAFETY_CLEAR_TICKS = 3  # consecutive clear ticks before a safety hold is released


class MissionManager:
    def __init__(
        self,
        *,
        world: WorldStateService,
        fleet: FleetAdapter,
        coverage_planner: CoveragePlanner,
        assignment_strategy: AssignmentStrategy,
        safety: SafetyGovernor,
        clock: Clock,
        decision_engine: DecisionEngine | None = None,
        decision_settings: DecisionSettings | None = None,
    ) -> None:
        self._world = world
        self._fleet = fleet
        self._coverage = coverage_planner
        self._assign = assignment_strategy
        self._safety = safety
        self._clock = clock
        self._engine = decision_engine
        cadence = decision_settings or DecisionSettings()
        self._disposition_interval_s = cadence.disposition_interval_s
        self._zone_priority_interval_s = cadence.zone_priority_interval_s
        self._last_zone_score_at: dict[str, datetime] = {}
        self._trackers: dict[str, PlanProgressTracker] = {}
        self._plans: dict[str, WaypointPlan] = {}
        self._last_decision_at: dict[str, datetime] = {}
        self._active_violations: dict[str, str] = {}
        self._safety_held: set[str] = set()
        self._clear_streak: dict[str, int] = {}
        self._blocked_assignments: set[tuple[str, str]] = set()
        self._estop_active = False

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

        if self._estop_active:
            return self._world.snapshot()  # nothing executes until the operator clears it

        if self._world.mission.status is MissionStatus.ACTIVE:
            await self._advance_coverage()
            await self._release_unworkable_zones()
            await self._evaluate_dispositions()  # proposals are validated by the governor
            await self._enforce_safety()  # then rules run on every drone regardless
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
        if self._estop_active:
            msg = "emergency stop is active; clear it before resuming"
            raise ValueError(msg)
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

    async def emergency_stop(self) -> None:
        """Every drone holds immediately; nothing else executes until cleared."""
        await self._record_action(OperatorActionType.EMERGENCY_STOP)
        self._estop_active = True
        for view in self._world.snapshot().drones:
            state = view.state
            if state is None or state.status in {DroneStatus.LANDED, DroneStatus.UNAVAILABLE}:
                continue
            if (await self._fleet.hold(view.drone.drone_id)).accepted:
                self._world.set_drone_status(
                    view.drone.drone_id,
                    DroneStatus.HOLDING,
                    assigned_zone_id=state.assigned_zone_id,
                    coverage_completed=state.coverage_completed,
                )
        if self._world.mission.status is MissionStatus.ACTIVE:
            await self._world.pause_mission()

    async def clear_emergency_stop(self) -> None:
        """Lift the stop. The mission stays PAUSED until the operator resumes it."""
        await self._record_action(OperatorActionType.RESUME_MISSION, note="emergency stop cleared")
        self._estop_active = False

    @property
    def emergency_stop_active(self) -> bool:
        return self._estop_active

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
            link_ok = state.link_state in {LinkState.CONNECTED, LinkState.DEGRADED}
            if link_ok and state.status in {DroneStatus.SEARCHING, DroneStatus.HOLDING}:
                continue
            reason = f"link {state.link_state}" if not link_ok else f"drone {state.status}"
            await self._release_zone(view.drone.drone_id, reason)

    async def _enforce_safety(self) -> None:
        """Deterministic rules run on every drone every tick, whether or not AI is configured."""
        snap = self._world.snapshot()
        for view in snap.drones:
            drone_id = view.drone.drone_id
            violation = self._safety.check(view, snap)
            if violation is None:
                self._active_violations.pop(drone_id, None)
                if drone_id in self._safety_held:
                    self._safety_held.discard(drone_id)
                    await self._apply_disposition(
                        drone_id, Disposition.CONTINUE_SEARCH, "safety:cleared"
                    )
                continue
            if self._active_violations.get(drone_id) == violation.rule:
                continue  # already acted on this rule; do not re-emit every tick
            self._active_violations[drone_id] = violation.rule
            state = view.state
            if state is not None and _already_satisfies(state.status, violation.required_action):
                continue
            current = state.status if state else "NO_TELEMETRY"
            event = SafetyEvent(
                timestamp=snap.taken_at,
                mission_id=snap.mission.mission_id,
                drone_id=drone_id,
                rule=violation.rule,
                proposed_action=f"current:{current}",
                safe_alternative=violation.required_action,
                reason=violation.reason,
                snapshot_hash=snap.snapshot_hash,
            )
            await self._record_safety_event(event)
            if violation.required_action is Disposition.HOLD:
                self._safety_held.add(drone_id)
            await self._apply_disposition(
                drone_id, violation.required_action, f"safety:{violation.rule}"
            )

    async def _evaluate_dispositions(self) -> None:
        if self._engine is None:
            return
        snap = self._world.snapshot()
        for view in snap.drones:
            state = view.state
            if state is None or state.link_state is not LinkState.CONNECTED:
                continue
            if state.status not in {DroneStatus.SEARCHING, DroneStatus.HOLDING}:
                continue
            if state.assigned_zone_id is None:
                continue
            last = self._last_decision_at.get(view.drone.drone_id)
            if last and (snap.taken_at - last).total_seconds() < self._disposition_interval_s:
                continue
            self._last_decision_at[view.drone.drone_id] = snap.taken_at
            await self._decide_disposition(view, snap)

    async def _decide_disposition(self, view: DroneView, snap: WorldSnapshot) -> None:
        assert self._engine is not None
        state = view.state
        assert state is not None
        drone_id = view.drone.drone_id
        inputs = DroneDispositionInput(
            battery_percent=state.battery_percent,
            estimated_return_battery_percent=self._safety.return_battery_percent(view, snap),
            distance_to_base_m=state.position.distance_to(snap.mission.base_position),
            zone_completion=state.coverage_completed,
            connection_quality=state.connection_quality,
            link_state=state.link_state,
            telemetry_age_s=view.telemetry_age_s or 0.0,
            active_detection=any(d.investigating_drone_id == drone_id for d in snap.detections),
        )
        outcome = await self._engine.drone_disposition(
            inputs, mission_id=snap.mission.mission_id, drone_id=drone_id
        )
        proposal = Disposition(outcome.selected)
        verdict = self._safety.validate_disposition(
            proposal, view, snap, source=outcome.record.provider
        )
        record = outcome.record.model_copy(
            update={
                "safety_override": verdict.overridden,
                "safety_event_id": verdict.event.event_id if verdict.event else None,
                "final_action": verdict.action.value,
            }
        )
        self._world.record_decision(record)
        if verdict.event is not None:
            await self._record_safety_event(verdict.event)
        await self._world.publish(
            DecisionCompleted(
                mission_id=snap.mission.mission_id,
                decision_id=record.decision_id,
                decision_type=record.decision_type,
                drone_id=drone_id,
                provider=record.provider,
                selected_value=record.selected_value,
                final_action=record.final_action,
                safety_override=record.safety_override,
            )
        )
        await self._apply_disposition(drone_id, verdict.action, f"decision:{record.decision_id}")

    async def _apply_disposition(self, drone_id: str, action: Disposition, reason: str) -> None:
        state = self._world.drone_state(drone_id)
        if state is None:
            return
        if action is Disposition.RETURN_TO_BASE:
            await self._send_home(drone_id, reason)
        elif action is Disposition.HOLD:
            if (
                state.status is not DroneStatus.HOLDING
                and (await self._fleet.hold(drone_id)).accepted
            ):
                self._world.set_drone_status(
                    drone_id,
                    DroneStatus.HOLDING,
                    assigned_zone_id=state.assigned_zone_id,
                    coverage_completed=state.coverage_completed,
                )
        elif action is Disposition.HANDOFF_ZONE:
            if state.assigned_zone_id is not None:
                await self._release_zone(drone_id, reason)
        elif action is Disposition.REQUEST_HUMAN_REVIEW:
            await self._world.publish(
                HumanReviewRequested(
                    mission_id=self._world.mission_id,
                    subject_type="drone",
                    subject_id=drone_id,
                    reason=reason,
                )
            )
        elif action is Disposition.CONTINUE_SEARCH and state.status is DroneStatus.HOLDING:
            plan = self._plans.get(drone_id)
            if plan and (await self._fleet.send_mission(drone_id, plan)).accepted:
                self._world.set_drone_status(
                    drone_id,
                    DroneStatus.SEARCHING,
                    assigned_zone_id=state.assigned_zone_id,
                    coverage_completed=state.coverage_completed,
                )

    async def _record_safety_event(self, event: SafetyEvent) -> None:
        self._world.record_safety_event(event)
        await self._world.publish(
            SafetyOverrideTriggered(
                mission_id=event.mission_id,
                safety_event_id=event.event_id,
                drone_id=event.drone_id,
                rule=event.rule,
                proposed_action=event.proposed_action,
                safe_alternative=event.safe_alternative,
            )
        )

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
        if await self._score_open_zones(open_zones, snap):
            snap = self._world.snapshot()
            open_zones = [z for z in snap.zones if z.status.needs_work]
        for proposal in self._assign.assign(
            candidates, open_zones, base_position=snap.mission.base_position
        ):
            if (proposal.drone_id, proposal.zone_id) in self._blocked_assignments:
                continue
            await self._dispatch(proposal.drone_id, proposal.zone_id, proposal.reason)

    async def _score_open_zones(self, zones: list[SearchZone], snap: WorldSnapshot) -> bool:
        """Ask the bounded zone_priority question for zones whose score is stale.

        The score is one weight among several in the deterministic assignment; it never
        assigns anything by itself. Returns True when any priority changed.
        """
        if self._engine is None:
            return False
        reference = snap.mission.last_known_position or snap.mission.search_area.polygon.centroid
        changed = False
        for zone in zones:
            last = self._last_zone_score_at.get(zone.zone_id)
            if last and (snap.taken_at - last).total_seconds() < self._zone_priority_interval_s:
                continue
            self._last_zone_score_at[zone.zone_id] = snap.taken_at
            since_search = (
                (snap.taken_at - zone.last_searched_at).total_seconds()
                if zone.last_searched_at
                else 0.0
            )
            inputs = ZonePriorityInput(
                zone_id=zone.zone_id,
                coverage=zone.coverage,
                time_since_last_search_s=since_search,
                nearby_candidate_detection=any(d.zone_id == zone.zone_id for d in snap.detections),
                distance_from_last_known_position_m=zone.polygon.centroid.distance_to(reference),
            )
            outcome = await self._engine.zone_priority(inputs, mission_id=snap.mission.mission_id)
            self._world.record_decision(outcome.record)
            await self._world.publish(
                DecisionCompleted(
                    mission_id=snap.mission.mission_id,
                    decision_id=outcome.record.decision_id,
                    decision_type=outcome.record.decision_type,
                    drone_id=None,
                    provider=outcome.record.provider,
                    selected_value=outcome.record.selected_value,
                    final_action=outcome.record.final_action,
                    safety_override=False,
                )
            )
            if abs(outcome.result.score - zone.priority) > 1e-9:
                self._world.set_zone_priority(zone.zone_id, outcome.result.score)
                changed = True
        return changed

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
        plan = self._with_transit(self._coverage.plan(request), view.state, snap)
        verdict = self._safety.validate_plan(plan, view, snap, source="planner")
        if not verdict.allowed:
            if verdict.event is not None:
                await self._record_safety_event(verdict.event)
            self._blocked_assignments.add((drone_id, zone_id))
            return False
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
        await self._world.publish(
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

    @staticmethod
    def _with_transit(plan: WaypointPlan, state: DroneState, snap: WorldSnapshot) -> WaypointPlan:
        """Prepend detour waypoints when the leg to the first waypoint crosses a no-fly region."""
        if not snap.mission.restricted_regions:
            return plan
        frame = LocalFrame.for_polygon(snap.mission.search_area.polygon)
        obstacles = [frame.polygon_to_local(r) for r in snap.mission.restricted_regions]
        first = plan.waypoints[0]
        via = detour(frame.to_local(state.position), frame.to_local(first.position), obstacles)
        if not via:
            return plan
        transit = tuple(
            Waypoint(
                position=frame.to_geo(x, y, first.position.altitude_m), speed_mps=first.speed_mps
            )
            for x, y in via
        )
        return plan.model_copy(
            update={"waypoints": transit + plan.waypoints, "transit_count": len(transit)}
        )

    async def _release_zone(self, drone_id: str, reason: str) -> None:
        state = self._world.drone_state(drone_id)
        if state is None or state.assigned_zone_id is None:
            return
        zone = self._world.zone(state.assigned_zone_id)
        new_status = ZoneStatus.PARTIAL if zone.coverage > 0 else ZoneStatus.UNSEARCHED
        await self._world.set_zone_status(zone.zone_id, new_status, assigned_drone_id=None)
        # Status stays IDLE here; the Safety Governor's lost-link rule is what marks a LOST
        # drone unavailable, so that transition is always audited.
        next_status = state.status
        if next_status in {DroneStatus.SEARCHING, DroneStatus.HOLDING}:
            next_status = DroneStatus.IDLE
        self._world.set_drone_status(drone_id, next_status, assigned_zone_id=None)
        self._drop_plan(drone_id)
        await self._world.publish(
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
            await self._world.publish(
                DroneReturning(mission_id=self._world.mission_id, drone_id=drone_id, reason=reason)
            )
        elif state.link_state is LinkState.LOST:
            # The vehicle cannot hear us; its own autopilot failsafe governs it from here.
            self._world.set_drone_status(drone_id, DroneStatus.UNAVAILABLE, assigned_zone_id=None)
            logger.warning(
                "lost-link return could not be delivered; drone marked unavailable",
                extra={"drone_id": drone_id, "reason": result.message},
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
        await self._world.publish(
            OperatorActionReceived(
                mission_id=self._world.mission_id,
                action_id=action.action_id,
                action_type=action_type,
                target_id=target_id,
            )
        )


def _already_satisfies(status: DroneStatus, required: Disposition) -> bool:
    """A drone already returning or holding needs no new command or audit event."""
    if required is Disposition.RETURN_TO_BASE:
        return status in {DroneStatus.RETURNING, DroneStatus.LANDED}
    if required is Disposition.HOLD:
        return status in {DroneStatus.HOLDING, DroneStatus.RETURNING, DroneStatus.LANDED}
    return False

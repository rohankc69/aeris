"""World State service.

Holds the live state of one mission and exposes explicit mutation methods that publish domain
events. Nothing reads the internal dictionaries directly; readers take a ``snapshot()``.

Link state is derived from telemetry age on every ``refresh_link_states`` call. Missing
telemetry is never treated as healthy: a drone that has never reported is ``LOST``.
"""

from __future__ import annotations

from datetime import datetime

from aeris.clock import Clock
from aeris.config import SafetySettings
from aeris.domain.enums import DroneStatus, LinkState, MissionStatus, ZoneStatus
from aeris.domain.models import (
    CandidateSurvivor,
    DecisionRecord,
    Detection,
    Drone,
    DroneState,
    Mission,
    MissionAssignment,
    OperatorAction,
    SafetyEvent,
    SearchZone,
    TelemetryFrame,
)
from aeris.events.bus import EventBus
from aeris.events.events import (
    DomainEvent,
    DroneDisconnected,
    DroneLinkStateChanged,
    DroneRegistered,
    MissionAborted,
    MissionCompleted,
    MissionPaused,
    MissionResumed,
    MissionStarted,
    TelemetryReceived,
    ZoneCoverageUpdated,
    ZoneStatusChanged,
)
from aeris.world.snapshot import DroneView, WorldSnapshot, compute_hash


class WorldStateService:
    def __init__(
        self,
        *,
        mission: Mission,
        zones: list[SearchZone],
        bus: EventBus,
        clock: Clock,
        safety: SafetySettings,
    ) -> None:
        self._mission = mission
        self._zones: dict[str, SearchZone] = {z.zone_id: z for z in zones}
        self._drones: dict[str, Drone] = {}
        self._states: dict[str, DroneState] = {}
        self._detections: dict[str, Detection] = {}
        self._candidates: dict[str, CandidateSurvivor] = {}
        self._assignments: dict[str, MissionAssignment] = {}
        self._decisions: list[DecisionRecord] = []
        self._safety_events: list[SafetyEvent] = []
        self._operator_actions: list[OperatorAction] = []
        self._event_log: list[DomainEvent] = []
        self._bus = bus
        self._clock = clock
        self._safety = safety

    # ------------------------------------------------------------------ properties

    @property
    def mission(self) -> Mission:
        return self._mission

    @property
    def mission_id(self) -> str:
        return self._mission.mission_id

    @property
    def decisions(self) -> tuple[DecisionRecord, ...]:
        return tuple(self._decisions)

    @property
    def safety_events(self) -> tuple[SafetyEvent, ...]:
        return tuple(self._safety_events)

    @property
    def operator_actions(self) -> tuple[OperatorAction, ...]:
        return tuple(self._operator_actions)

    @property
    def event_log(self) -> tuple[DomainEvent, ...]:
        return tuple(self._event_log)

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> WorldSnapshot:
        now = self._clock.now()
        views = tuple(
            DroneView(
                drone=drone,
                state=self._states.get(drone_id),
                telemetry_age_s=self._age_s(drone_id, now),
            )
            for drone_id, drone in self._drones.items()
        )
        snap = WorldSnapshot(
            taken_at=now,
            mission=self._mission,
            drones=views,
            zones=tuple(self._zones.values()),
            detections=tuple(self._detections.values()),
            candidates=tuple(self._candidates.values()),
            assignments=tuple(self._assignments.values()),
        )
        return snap.model_copy(update={"snapshot_hash": compute_hash(snap)})

    # ------------------------------------------------------------------ mission lifecycle

    async def start_mission(self) -> None:
        await self._transition(MissionStatus.ACTIVE, MissionStarted(mission_id=self.mission_id))

    async def pause_mission(self) -> None:
        await self._transition(MissionStatus.PAUSED, MissionPaused(mission_id=self.mission_id))

    async def resume_mission(self) -> None:
        await self._transition(MissionStatus.ACTIVE, MissionResumed(mission_id=self.mission_id))

    async def abort_mission(self, reason: str = "") -> None:
        await self._transition(
            MissionStatus.ABORTED, MissionAborted(mission_id=self.mission_id, reason=reason)
        )

    async def complete_mission(self, status: MissionStatus = MissionStatus.COMPLETED) -> None:
        await self._transition(
            status, MissionCompleted(mission_id=self.mission_id, final_status=status)
        )

    async def _transition(self, status: MissionStatus, event: DomainEvent) -> None:
        self._mission = self._mission.transition(status, self._clock.now())
        await self._publish(event)

    # ------------------------------------------------------------------ fleet

    async def register_drone(self, drone: Drone) -> None:
        self._drones[drone.drone_id] = drone
        await self._publish(DroneRegistered(mission_id=self.mission_id, drone_id=drone.drone_id))

    async def apply_telemetry(self, frame: TelemetryFrame) -> DroneState:
        if frame.drone_id not in self._drones:
            msg = f"telemetry for unregistered drone {frame.drone_id}"
            raise KeyError(msg)
        previous = self._states.get(frame.drone_id)
        state = DroneState.from_telemetry(frame, previous)
        state = state.model_copy(update={"link_state": self._link_state_for(frame.timestamp)})
        self._states[frame.drone_id] = state
        await self._publish(
            TelemetryReceived(
                mission_id=self.mission_id,
                drone_id=frame.drone_id,
                battery_percent=frame.battery_percent,
                position=frame.position,
            )
        )
        return state

    async def refresh_link_states(self) -> None:
        """Re-derive every drone's link state from telemetry age and publish changes."""
        now = self._clock.now()
        for drone_id in self._drones:
            state = self._states.get(drone_id)
            if state is None:
                continue
            age = self._age_s(drone_id, now) or 0.0
            new_link = self._link_state_for(state.timestamp)
            if new_link is state.link_state:
                continue
            self._states[drone_id] = state.model_copy(update={"link_state": new_link})
            await self._publish(
                DroneLinkStateChanged(
                    mission_id=self.mission_id,
                    drone_id=drone_id,
                    previous=state.link_state,
                    current=new_link,
                    telemetry_age_s=age,
                )
            )
            if new_link is LinkState.LOST:
                await self._publish(
                    DroneDisconnected(
                        mission_id=self.mission_id, drone_id=drone_id, telemetry_age_s=age
                    )
                )

    def set_drone_status(
        self,
        drone_id: str,
        status: DroneStatus,
        *,
        assigned_zone_id: str | None = None,
        coverage_completed: float | None = None,
    ) -> DroneState:
        state = self._require_state(drone_id)
        update: dict[str, object] = {"status": status, "assigned_zone_id": assigned_zone_id}
        if coverage_completed is not None:
            update["coverage_completed"] = coverage_completed
        elif assigned_zone_id is None:
            update["coverage_completed"] = 0.0
        self._states[drone_id] = state.model_copy(update=update)
        return self._states[drone_id]

    def drone_state(self, drone_id: str) -> DroneState | None:
        return self._states.get(drone_id)

    # ------------------------------------------------------------------ zones

    def zone(self, zone_id: str) -> SearchZone:
        return self._zones[zone_id]

    async def set_zone_status(
        self, zone_id: str, status: ZoneStatus, **updates: object
    ) -> SearchZone:
        zone = self._zones[zone_id]
        if status is not zone.status:
            await self._publish(
                ZoneStatusChanged(
                    mission_id=self.mission_id,
                    zone_id=zone_id,
                    previous=zone.status,
                    current=status,
                )
            )
        self._zones[zone_id] = zone.model_copy(update={"status": status, **updates})
        return self._zones[zone_id]

    def set_zone_priority(self, zone_id: str, priority: float) -> SearchZone:
        zone = self._zones[zone_id]
        self._zones[zone_id] = zone.model_copy(update={"priority": min(1.0, max(0.0, priority))})
        return self._zones[zone_id]

    async def update_zone_coverage(
        self, zone_id: str, drone_id: str, coverage: float
    ) -> SearchZone:
        zone = self._zones[zone_id]
        coverage = min(1.0, max(zone.coverage, coverage))
        if coverage == zone.coverage:
            return zone
        self._zones[zone_id] = zone.model_copy(
            update={"coverage": coverage, "last_searched_at": self._clock.now()}
        )
        state = self._states.get(drone_id)
        if state is not None and state.assigned_zone_id == zone_id:
            self._states[drone_id] = state.model_copy(update={"coverage_completed": coverage})
        await self._publish(
            ZoneCoverageUpdated(
                mission_id=self.mission_id, zone_id=zone_id, drone_id=drone_id, coverage=coverage
            )
        )
        return self._zones[zone_id]

    # ------------------------------------------------------------------ records

    def record_assignment(self, assignment: MissionAssignment) -> None:
        self._assignments[assignment.assignment_id] = assignment

    def record_decision(self, record: DecisionRecord) -> None:
        self._decisions.append(record)

    def record_safety_event(self, event: SafetyEvent) -> None:
        self._safety_events.append(event)

    def record_operator_action(self, action: OperatorAction) -> None:
        self._operator_actions.append(action)

    def upsert_detection(self, detection: Detection) -> None:
        self._detections[detection.detection_id] = detection

    def detection(self, detection_id: str) -> Detection | None:
        return self._detections.get(detection_id)

    def upsert_candidate(self, candidate: CandidateSurvivor) -> None:
        self._candidates[candidate.candidate_id] = candidate

    def candidate(self, candidate_id: str) -> CandidateSurvivor | None:
        return self._candidates.get(candidate_id)

    # ------------------------------------------------------------------ helpers

    async def publish(self, event: DomainEvent) -> None:
        """Append to the mission event log and forward to the bus. All mission events go here."""
        self._event_log.append(event)
        await self._bus.publish(event)

    async def _publish(self, event: DomainEvent) -> None:
        await self.publish(event)

    def _require_state(self, drone_id: str) -> DroneState:
        state = self._states.get(drone_id)
        if state is None:
            msg = f"no telemetry yet for drone {drone_id}"
            raise KeyError(msg)
        return state

    def _age_s(self, drone_id: str, now: datetime) -> float | None:
        state = self._states.get(drone_id)
        if state is None:
            return None
        return max(0.0, (now - state.timestamp).total_seconds())

    def _link_state_for(self, telemetry_timestamp: datetime) -> LinkState:
        age = max(0.0, (self._clock.now() - telemetry_timestamp).total_seconds())
        if age <= self._safety.link_degraded_after_s:
            return LinkState.CONNECTED
        if age <= self._safety.link_stale_after_s:
            return LinkState.DEGRADED
        if age <= self._safety.link_lost_after_s:
            return LinkState.STALE
        return LinkState.LOST

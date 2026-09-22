from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from aeris.api.runtime import MissionRegistry, MissionRuntime
from aeris.api.schemas import CommandResponse, CreateMissionRequest, MissionSummary
from aeris.config import Settings
from aeris.simulation.scenario import list_scenarios, load_scenario

router = APIRouter(prefix="/api/v1")


def get_registry(request: Request) -> MissionRegistry:
    registry: MissionRegistry = request.app.state.registry
    return registry


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Registry = Annotated[MissionRegistry, Depends(get_registry)]


def _runtime_or_404(registry: MissionRegistry, mission_id: str) -> MissionRuntime:
    runtime = registry.get(mission_id)
    if runtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"mission {mission_id} not found")
    return runtime


def _summary(runtime: MissionRuntime) -> MissionSummary:
    snap = runtime.runner.world.snapshot()
    return MissionSummary(
        mission_id=runtime.mission_id,
        name=snap.mission.name,
        status=snap.mission.status,
        scenario=runtime.runner.scenario.name,
        time_scale=runtime.time_scale,
        running=runtime.running,
        elapsed_s=runtime.runner.elapsed_s,
        coverage_fraction=snap.coverage_fraction,
        zone_count=len(snap.zones),
        drone_count=len(snap.drones),
        emergency_stop_active=runtime.runner.manager.emergency_stop_active,
        decision_count=len(runtime.runner.world.decisions),
        safety_event_count=len(runtime.runner.world.safety_events),
    )


# ----------------------------------------------------------------- meta


@router.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    return {
        "status": "ok",
        "env": settings.env,
        "fleet_provider": settings.fleet_provider,
        "decision_provider": settings.decision_provider,
    }


@router.get("/scenarios")
async def scenarios() -> list[str]:
    return list_scenarios()


# ----------------------------------------------------------------- missions


@router.post("/missions", status_code=status.HTTP_201_CREATED)
async def create_mission(body: CreateMissionRequest, registry: Registry) -> MissionSummary:
    if body.spec is not None:
        spec = body.spec
    else:
        try:
            spec = load_scenario(body.scenario or "")
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"scenario {body.scenario} not found"
            ) from exc
    runtime = await registry.create(spec, time_scale=body.time_scale)
    if body.autostart:
        await registry.start(runtime)
    return _summary(runtime)


@router.get("/missions")
async def list_missions(registry: Registry) -> list[MissionSummary]:
    return [_summary(r) for r in registry.list()]


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str, registry: Registry) -> dict[str, object]:
    runtime = _runtime_or_404(registry, mission_id)
    return {
        "summary": _summary(runtime).model_dump(mode="json"),
        **runtime.payload(),
    }


@router.get("/missions/{mission_id}/drones")
async def get_drones(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    payload = runtime.payload()
    drones: list[dict[str, object]] = payload["drones"]  # type: ignore[assignment]
    return drones


@router.get("/missions/{mission_id}/zones")
async def get_zones(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    return [z.model_dump(mode="json") for z in runtime.runner.world.snapshot().zones]


@router.get("/missions/{mission_id}/detections")
async def get_detections(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    return [d.model_dump(mode="json") for d in runtime.runner.world.snapshot().detections]


@router.get("/missions/{mission_id}/candidates")
async def get_candidates(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    return [c.model_dump(mode="json") for c in runtime.runner.world.snapshot().candidates]


@router.get("/missions/{mission_id}/decisions")
async def get_decisions(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    return [d.model_dump(mode="json") for d in runtime.runner.world.decisions]


@router.get("/missions/{mission_id}/safety-events")
async def get_safety_events(mission_id: str, registry: Registry) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    return [e.model_dump(mode="json") for e in runtime.runner.world.safety_events]


@router.get("/missions/{mission_id}/events")
async def get_events(
    mission_id: str, registry: Registry, limit: int = 200, exclude_telemetry: bool = True
) -> list[dict[str, object]]:
    runtime = _runtime_or_404(registry, mission_id)
    log = runtime.runner.world.event_log
    if exclude_telemetry:
        log = tuple(
            e for e in log if e.type_name not in {"TelemetryReceived", "ZoneCoverageUpdated"}
        )
    return [e.model_dump(mode="json") | {"event_type": e.type_name} for e in log[-limit:]]


# ----------------------------------------------------------------- commands


def _command_response(runtime: MissionRuntime, detail: str = "") -> CommandResponse:
    return CommandResponse(
        ok=True,
        mission_id=runtime.mission_id,
        status=runtime.runner.world.mission.status,
        detail=detail,
    )


@router.post("/missions/{mission_id}/start")
async def start_mission(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await registry.start(runtime)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "started")


@router.post("/missions/{mission_id}/pause")
async def pause_mission(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await runtime.runner.manager.pause()
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "paused")


@router.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await runtime.runner.manager.resume()
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "resumed")


@router.post("/missions/{mission_id}/abort")
async def abort_mission(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await runtime.runner.manager.abort()
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "aborted")


@router.post("/missions/{mission_id}/candidates/{candidate_id}/confirm")
async def confirm_candidate(
    mission_id: str, candidate_id: str, registry: Registry
) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await runtime.runner.manager.confirm_candidate(candidate_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "survivor confirmed by operator")


@router.post("/missions/{mission_id}/candidates/{candidate_id}/reject")
async def reject_candidate(
    mission_id: str, candidate_id: str, registry: Registry
) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    try:
        await runtime.runner.manager.reject_candidate(candidate_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "candidate rejected by operator")


@router.post("/missions/{mission_id}/detections/{detection_id}/confirm")
async def confirm_detection(
    mission_id: str, detection_id: str, registry: Registry
) -> CommandResponse:
    """Operator confirms a detection directly; it is escalated first if it was not already."""
    runtime = _runtime_or_404(registry, mission_id)
    world = runtime.runner.world
    if world.detection(detection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"detection {detection_id} not found")
    candidate = world.candidate_for_detection(detection_id) or await world.escalate_candidate(
        detection_id
    )
    try:
        await runtime.runner.manager.confirm_candidate(candidate.candidate_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "survivor confirmed by operator")


@router.post("/missions/{mission_id}/detections/{detection_id}/reject")
async def reject_detection(
    mission_id: str, detection_id: str, registry: Registry
) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    world = runtime.runner.world
    if world.detection(detection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"detection {detection_id} not found")
    candidate = world.candidate_for_detection(detection_id) or await world.escalate_candidate(
        detection_id
    )
    try:
        await runtime.runner.manager.reject_candidate(candidate.candidate_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _command_response(runtime, "candidate rejected by operator")


@router.post("/missions/{mission_id}/estop")
async def emergency_stop(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    await runtime.runner.manager.emergency_stop()
    return _command_response(runtime, "emergency stop active")


@router.post("/missions/{mission_id}/estop/clear")
async def clear_emergency_stop(mission_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    await runtime.runner.manager.clear_emergency_stop()
    return _command_response(runtime, "emergency stop cleared; mission remains paused")


@router.post("/missions/{mission_id}/drones/{drone_id}/return")
async def return_drone(mission_id: str, drone_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    if runtime.runner.world.drone_state(drone_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"drone {drone_id} not found")
    await runtime.runner.manager.return_drone(drone_id)
    return _command_response(runtime, f"{drone_id} returning")


@router.post("/missions/{mission_id}/drones/{drone_id}/hold")
async def hold_drone(mission_id: str, drone_id: str, registry: Registry) -> CommandResponse:
    runtime = _runtime_or_404(registry, mission_id)
    if runtime.runner.world.drone_state(drone_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"drone {drone_id} not found")
    await runtime.runner.manager.hold_drone(drone_id)
    return _command_response(runtime, f"{drone_id} holding")


# ----------------------------------------------------------------- websocket


@router.websocket("/missions/{mission_id}/ws")
async def mission_stream(websocket: WebSocket, mission_id: str) -> None:
    registry: MissionRegistry = websocket.app.state.registry
    runtime = registry.get(mission_id)
    if runtime is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    queue = runtime.subscribe()
    try:
        await websocket.send_json({"kind": "snapshot", "data": runtime.payload()})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        runtime.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(), timeout=1)

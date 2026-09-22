import asyncio

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from aeris.api import create_app
from aeris.config import load_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    app = create_app(load_settings(_env_file=None))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_health_and_scenarios(client: AsyncClient) -> None:
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["fleet_provider"] == "fake"
    assert health.json()["decision_provider"] == "mock"
    scenarios = await client.get("/api/v1/scenarios")
    assert "basic_search" in scenarios.json()


async def test_mission_lifecycle_over_http(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/missions", json={"scenario": "basic_search", "time_scale": 1000}
    )
    assert created.status_code == 201, created.text
    summary = created.json()
    mission_id = summary["mission_id"]
    assert summary["status"] == "CREATED"
    assert summary["zone_count"] == 9
    assert summary["drone_count"] == 3

    listed = await client.get("/api/v1/missions")
    assert [m["mission_id"] for m in listed.json()] == [mission_id]

    zones = await client.get(f"/api/v1/missions/{mission_id}/zones")
    assert {z["status"] for z in zones.json()} == {"UNSEARCHED"}

    started = await client.post(f"/api/v1/missions/{mission_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "ACTIVE"

    for _ in range(200):
        detail = (await client.get(f"/api/v1/missions/{mission_id}")).json()
        if detail["mission"]["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.05)
    assert detail["mission"]["status"] == "COMPLETED"
    assert detail["coverage_fraction"] == pytest.approx(1.0)
    assert len(detail["drones"]) == 3
    assert all(d["state"] is not None for d in detail["drones"])

    events = (await client.get(f"/api/v1/missions/{mission_id}/events")).json()
    types = {e["event_type"] for e in events}
    assert {"MissionStarted", "ZoneAssigned", "MissionCompleted"} <= types
    assert "TelemetryReceived" not in types

    again = await client.post(f"/api/v1/missions/{mission_id}/start")
    assert again.status_code == 409


async def test_operator_commands_over_http(client: AsyncClient) -> None:
    mission_id = (
        await client.post(
            "/api/v1/missions",
            json={"scenario": "basic_search", "time_scale": 200, "autostart": True},
        )
    ).json()["mission_id"]
    await asyncio.sleep(0.3)

    paused = await client.post(f"/api/v1/missions/{mission_id}/pause")
    assert paused.json()["status"] == "PAUSED"
    drones = (await client.get(f"/api/v1/missions/{mission_id}/drones")).json()
    assert all(d["state"]["status"] == "HOLDING" for d in drones)

    resumed = await client.post(f"/api/v1/missions/{mission_id}/resume")
    assert resumed.json()["status"] == "ACTIVE"

    ret = await client.post(f"/api/v1/missions/{mission_id}/drones/drone-01/return")
    assert ret.status_code == 200
    ghost = await client.post(f"/api/v1/missions/{mission_id}/drones/ghost/return")
    assert ghost.status_code == 404

    aborted = await client.post(f"/api/v1/missions/{mission_id}/abort")
    assert aborted.json()["status"] == "ABORTED"


async def test_emergency_stop_over_http(client: AsyncClient) -> None:
    mission_id = (
        await client.post(
            "/api/v1/missions",
            json={"scenario": "basic_search", "time_scale": 200, "autostart": True},
        )
    ).json()["mission_id"]
    await asyncio.sleep(0.3)

    stopped = await client.post(f"/api/v1/missions/{mission_id}/estop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "PAUSED"
    summary = (await client.get(f"/api/v1/missions/{mission_id}")).json()["summary"]
    assert summary["emergency_stop_active"] is True
    drones = (await client.get(f"/api/v1/missions/{mission_id}/drones")).json()
    assert all(d["state"]["status"] == "HOLDING" for d in drones)

    blocked = await client.post(f"/api/v1/missions/{mission_id}/resume")
    assert blocked.status_code == 409

    cleared = await client.post(f"/api/v1/missions/{mission_id}/estop/clear")
    assert cleared.status_code == 200 and cleared.json()["status"] == "PAUSED"
    resumed = await client.post(f"/api/v1/missions/{mission_id}/resume")
    assert resumed.json()["status"] == "ACTIVE"
    events = (await client.get(f"/api/v1/missions/{mission_id}/events")).json()
    assert any(
        e["event_type"] == "OperatorActionReceived" and e["action_type"] == "EMERGENCY_STOP"
        for e in events
    )


async def test_candidate_confirmation_over_http(client: AsyncClient) -> None:
    mission_id = (
        await client.post(
            "/api/v1/missions",
            json={"scenario": "candidate_detection", "time_scale": 1000, "autostart": True},
        )
    ).json()["mission_id"]
    for _ in range(200):
        candidates = (await client.get(f"/api/v1/missions/{mission_id}/candidates")).json()
        if candidates:
            break
        await asyncio.sleep(0.05)
    assert candidates, "scenario should escalate a candidate"
    candidate_id = candidates[0]["candidate_id"]
    detection_id = candidates[0]["detection_id"]

    confirmed = await client.post(
        f"/api/v1/missions/{mission_id}/candidates/{candidate_id}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "PERSON_LOCATED"
    again = await client.post(f"/api/v1/missions/{mission_id}/candidates/{candidate_id}/reject")
    assert again.status_code == 409
    missing = await client.post(f"/api/v1/missions/{mission_id}/candidates/nope/confirm")
    assert missing.status_code == 404
    detections = (await client.get(f"/api/v1/missions/{mission_id}/detections")).json()
    assert detections[0]["detection_id"] == detection_id


async def test_validation_errors(client: AsyncClient) -> None:
    both = await client.post(
        "/api/v1/missions", json={"scenario": "basic_search", "spec": None, "time_scale": 0}
    )
    assert both.status_code == 422
    missing = await client.post("/api/v1/missions", json={"scenario": "does_not_exist"})
    assert missing.status_code == 404
    unknown = await client.get("/api/v1/missions/nope")
    assert unknown.status_code == 404


def test_websocket_streams_snapshot_and_events() -> None:
    app = create_app(load_settings(_env_file=None))
    with TestClient(app) as tc:
        mission_id = tc.post(
            "/api/v1/missions", json={"scenario": "basic_search", "time_scale": 500}
        ).json()["mission_id"]
        with tc.websocket_connect(f"/api/v1/missions/{mission_id}/ws") as ws:
            first = ws.receive_json()
            assert first["kind"] == "snapshot"
            assert first["data"]["mission"]["status"] == "CREATED"
            tc.post(f"/api/v1/missions/{mission_id}/start")
            kinds = set()
            event_types = set()
            for _ in range(40):
                msg = ws.receive_json()
                kinds.add(msg["kind"])
                if msg["kind"] == "event":
                    event_types.add(msg["data"]["event_type"])
                if "snapshot" in kinds and "ZoneAssigned" in event_types:
                    break
            assert {"snapshot", "event"} <= kinds
            assert "ZoneAssigned" in event_types

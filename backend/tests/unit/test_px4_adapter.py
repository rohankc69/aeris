"""PX4FleetAdapter against an in-process fake bridge. No ROS, no PX4."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from websockets.asyncio.server import Server, ServerConnection, serve

from aeris.domain import AssignmentTask, GeoPoint, Waypoint, WaypointPlan
from aeris.fleet.px4 import PROTOCOL_VERSION, PX4FleetAdapter

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeBridge:
    """Speaks the wire protocol like aeris_px4_bridge would, scriptable per test."""

    def __init__(
        self, *, accept: bool = True, ack_delay_s: float = 0.0, version: int = PROTOCOL_VERSION
    ) -> None:
        self.accept = accept
        self.ack_delay_s = ack_delay_s
        self.version = version
        self.received: list[dict[str, object]] = []
        self.connections: list[ServerConnection] = []
        self.server: Server | None = None

    async def __aenter__(self) -> "FakeBridge":
        self.server = await serve(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self) -> str:
        assert self.server is not None
        port = next(iter(self.server.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    async def _handle(self, conn: ServerConnection) -> None:
        self.connections.append(conn)
        await conn.send(
            json.dumps(
                {
                    "type": "hello",
                    "protocol_version": self.version,
                    "vehicles": {"drone-01": 1, "drone-02": 2},
                }
            )
        )
        async for raw in conn:
            msg = json.loads(raw)
            self.received.append(msg)
            if self.ack_delay_s:
                await asyncio.sleep(self.ack_delay_s)
            await conn.send(
                json.dumps(
                    {
                        "type": "ack",
                        "request_id": msg["request_id"],
                        "drone_id": msg["drone_id"],
                        "accepted": self.accept,
                        "message": "ok" if self.accept else "rejected by bridge",
                    }
                )
            )

    async def push(self, payload: dict[str, object]) -> None:
        for conn in self.connections:
            await conn.send(json.dumps(payload))


@pytest.fixture
async def bridge() -> AsyncIterator[FakeBridge]:
    async with FakeBridge() as b:
        yield b


async def connected_adapter(bridge: FakeBridge, **kwargs: object) -> PX4FleetAdapter:
    adapter = PX4FleetAdapter(
        bridge_url=bridge.url, command_timeout_s=1.0, reconnect_min_s=0.05, **kwargs
    )  # type: ignore[arg-type]
    await adapter.start()
    assert await adapter.wait_connected(2.0)
    return adapter


def plan() -> WaypointPlan:
    return WaypointPlan(
        drone_id="drone-01",
        zone_id="A1",
        task=AssignmentTask.SEARCH,
        waypoints=(
            Waypoint(position=GeoPoint(latitude=47, longitude=8, altitude_m=60), speed_mps=10),
        ),
    )


async def test_hello_maps_vehicles_and_telemetry_becomes_frames(bridge: FakeBridge) -> None:
    adapter = await connected_adapter(bridge)
    try:
        assert adapter.vehicles == {"drone-01": 1, "drone-02": 2}
        assert await adapter.get_telemetry() == []
        await bridge.push(
            {
                "type": "telemetry",
                "drone_id": "drone-01",
                "timestamp": T0.isoformat(),
                "latitude": 47.0,
                "longitude": 8.0,
                "altitude_m": 55.0,
                "heading_deg": 370.0,
                "velocity_mps": 9.5,
                "battery_percent": 81.0,
                "estimated_remaining_s": 900.0,
                "thermal_available": True,
            }
        )
        await asyncio.sleep(0.05)
        frames = await adapter.get_telemetry()
        assert len(frames) == 1
        f = frames[0]
        assert f.drone_id == "drone-01" and f.timestamp == T0
        assert f.position.altitude_m == 55 and f.heading_deg == 10  # normalised
        assert f.battery_percent == 81 and f.thermal_available
    finally:
        await adapter.stop()


async def test_observations_are_queued_and_drained(bridge: FakeBridge) -> None:
    adapter = await connected_adapter(bridge)
    try:
        await bridge.push(
            {
                "type": "observation",
                "drone_id": "drone-02",
                "timestamp": T0.isoformat(),
                "source": "THERMAL",
                "confidence": 0.6,
                "latitude": 47.0,
                "longitude": 8.0,
            }
        )
        await asyncio.sleep(0.05)
        obs = await adapter.get_observations()
        assert len(obs) == 1 and obs[0].source.value == "THERMAL" and obs[0].confidence == 0.6
        assert await adapter.get_observations() == []
    finally:
        await adapter.stop()


async def test_commands_round_trip_with_ack(bridge: FakeBridge) -> None:
    adapter = await connected_adapter(bridge)
    try:
        sent = await adapter.send_mission("drone-01", plan())
        assert sent.accepted and sent.message == "ok"
        rtb = await adapter.return_to_base("drone-02")
        hold = await adapter.hold("drone-02")
        assert rtb.accepted and hold.accepted
        types = [m["type"] for m in bridge.received]
        assert types == ["send_mission", "return_to_base", "hold"]
        mission = bridge.received[0]
        assert mission["drone_id"] == "drone-01"
        assert mission["waypoints"] == [
            {"latitude": 47.0, "longitude": 8.0, "altitude_m": 60.0, "speed_mps": 10.0}
        ]  # type: ignore[comparison-overlap]
    finally:
        await adapter.stop()


async def test_bridge_rejection_and_timeout_are_reported(bridge: FakeBridge) -> None:
    bridge.accept = False
    adapter = await connected_adapter(bridge)
    try:
        result = await adapter.hold("drone-01")
        assert not result.accepted and "rejected" in result.message
        bridge.ack_delay_s = 2.0
        slow = await adapter.hold("drone-01")
        assert not slow.accepted and "timeout" in slow.message
    finally:
        await adapter.stop()


async def test_disconnected_adapter_reports_nothing_and_rejects_commands() -> None:
    adapter = PX4FleetAdapter(
        bridge_url="ws://127.0.0.1:1", command_timeout_s=0.2, reconnect_min_s=0.05
    )
    await adapter.start()
    try:
        await asyncio.sleep(0.2)
        assert not adapter.connected
        assert await adapter.get_telemetry() == []
        result = await adapter.hold("drone-01")
        assert not result.accepted and "disconnected" in result.message
    finally:
        await adapter.stop()


async def test_reconnects_after_bridge_restart() -> None:
    async with FakeBridge() as first:
        adapter = PX4FleetAdapter(
            bridge_url=first.url, command_timeout_s=1.0, reconnect_min_s=0.05, reconnect_max_s=0.1
        )
        await adapter.start()
        assert await adapter.wait_connected(2.0)
        port = int(first.url.rsplit(":", 1)[1])
    await asyncio.sleep(0.1)
    assert not adapter.connected
    assert await adapter.get_telemetry() == []
    server = await serve(FakeBridge()._handle, "127.0.0.1", port)
    try:
        assert await adapter.wait_connected(3.0)
    finally:
        server.close()
        await server.wait_closed()
        await adapter.stop()


async def test_protocol_version_mismatch_is_refused() -> None:
    async with FakeBridge(version=99) as wrong:
        adapter = PX4FleetAdapter(bridge_url=wrong.url, command_timeout_s=0.5, reconnect_min_s=0.05)
        await adapter.start()
        try:
            assert not await adapter.wait_connected(0.5)
        finally:
            await adapter.stop()

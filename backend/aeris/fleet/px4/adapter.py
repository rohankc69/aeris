"""PX4 fleet adapter (**real** transport, exercised against a fake bridge in tests; the live
PX4 SITL path requires the Docker ``px4`` profile).

Connects to the ``aeris_px4_bridge`` WebSocket, keeps the latest telemetry per drone, queues
observations, and turns commands into request/ack round-trips with a timeout. Reconnects with
bounded backoff. While disconnected it reports no telemetry, so the World State's staleness
logic marks every drone STALE then LOST, which is the honest picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from uuid import uuid4

import websockets
from websockets.asyncio.client import ClientConnection

from aeris.domain.models import DetectionObservation, TelemetryFrame, WaypointPlan
from aeris.fleet.base import CommandResult
from aeris.fleet.px4.protocol import (
    PROTOCOL_VERSION,
    Ack,
    Hello,
    Hold,
    ObservationMessage,
    Outbound,
    ReturnToBase,
    SendMission,
    TelemetryMessage,
    parse_inbound,
)

logger = logging.getLogger(__name__)


class PX4FleetAdapter:
    def __init__(
        self,
        *,
        bridge_url: str,
        command_timeout_s: float = 5.0,
        reconnect_min_s: float = 0.5,
        reconnect_max_s: float = 10.0,
    ) -> None:
        self._url = bridge_url
        self._command_timeout_s = command_timeout_s
        self._reconnect_min_s = reconnect_min_s
        self._reconnect_max_s = reconnect_max_s
        self._latest: dict[str, TelemetryFrame] = {}
        self._observations: list[DetectionObservation] = []
        self._pending: dict[str, asyncio.Future[Ack]] = {}
        self._conn: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._vehicles: dict[str, int] = {}
        self._connected = asyncio.Event()

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._reader is None:
            self._reader = asyncio.create_task(self._run(), name="px4-bridge-reader")

    async def stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    async def wait_connected(self, timeout_s: float) -> bool:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected.wait(), timeout=timeout_s)
        return self._connected.is_set()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def vehicles(self) -> dict[str, int]:
        return dict(self._vehicles)

    # ------------------------------------------------------------- FleetAdapter

    async def get_telemetry(self) -> list[TelemetryFrame]:
        return list(self._latest.values())

    async def get_observations(self) -> list[DetectionObservation]:
        out, self._observations = self._observations, []
        return out

    async def send_mission(self, drone_id: str, plan: WaypointPlan) -> CommandResult:
        return await self._command(SendMission.from_plan(self._request_id(), plan), drone_id)

    async def return_to_base(self, drone_id: str) -> CommandResult:
        return await self._command(
            ReturnToBase(request_id=self._request_id(), drone_id=drone_id), drone_id
        )

    async def hold(self, drone_id: str) -> CommandResult:
        return await self._command(Hold(request_id=self._request_id(), drone_id=drone_id), drone_id)

    # ------------------------------------------------------------- internals

    async def _command(self, message: Outbound, drone_id: str) -> CommandResult:
        conn = self._conn
        if conn is None or not self._connected.is_set():
            return CommandResult(drone_id=drone_id, accepted=False, message="bridge disconnected")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Ack] = loop.create_future()
        self._pending[message.request_id] = future
        try:
            await conn.send(message.model_dump_json())
            ack = await asyncio.wait_for(future, timeout=self._command_timeout_s)
        except TimeoutError:
            return CommandResult(drone_id=drone_id, accepted=False, message="bridge ack timeout")
        except websockets.ConnectionClosed:
            return CommandResult(
                drone_id=drone_id, accepted=False, message="bridge connection closed"
            )
        finally:
            self._pending.pop(message.request_id, None)
        return CommandResult(drone_id=drone_id, accepted=ack.accepted, message=ack.message)

    async def _run(self) -> None:
        delay = self._reconnect_min_s
        while True:
            try:
                async with websockets.connect(self._url, open_timeout=5) as conn:
                    self._conn = conn
                    delay = self._reconnect_min_s
                    await self._read_loop(conn)
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("bridge connection failed: %s", exc, extra={"url": self._url})
            finally:
                self._conn = None
                self._connected.clear()
                self._latest.clear()
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(websockets.ConnectionClosedError(None, None))
            await asyncio.sleep(delay)
            delay = min(self._reconnect_max_s, delay * 2)

    async def _read_loop(self, conn: ClientConnection) -> None:
        async for raw in conn:
            try:
                message = parse_inbound(raw)
            except ValueError as exc:
                logger.warning("ignoring malformed bridge message: %s", exc)
                continue
            if isinstance(message, Hello):
                if message.protocol_version != PROTOCOL_VERSION:
                    logger.error(
                        "bridge protocol %s != %s; refusing",
                        message.protocol_version,
                        PROTOCOL_VERSION,
                    )
                    await conn.close()
                    return
                self._vehicles = dict(message.vehicles)
                self._connected.set()
                logger.info("bridge connected", extra={"vehicles": self._vehicles})
            elif isinstance(message, TelemetryMessage):
                self._latest[message.drone_id] = message.to_frame()
            elif isinstance(message, ObservationMessage):
                self._observations.append(message.to_observation())
            elif isinstance(message, Ack):
                future = self._pending.get(message.request_id)
                if future is not None and not future.done():
                    future.set_result(message)

    @staticmethod
    def _request_id() -> str:
        return uuid4().hex


def loads(raw: str | bytes) -> dict[str, object]:
    """Helper for tests and tools: decode one message as a plain dict."""
    data: dict[str, object] = json.loads(raw)
    return data

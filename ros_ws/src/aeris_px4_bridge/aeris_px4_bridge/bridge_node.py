"""aeris_px4_bridge node.

Responsibilities (and nothing more):
  PX4 telemetry        -> AERIS telemetry messages
  AERIS waypoint plans -> PX4 offboard setpoints
  AERIS RTL / hold     -> PX4 vehicle commands
  PX4 acks             -> AERIS command results
  perception hits      -> AERIS observation messages (from /aeris/<drone_id>/observation,
                          hyphens in the id replaced by underscores)

Vehicles are mapped explicitly through the ``vehicles`` parameter ("drone-01:1" = AERIS
drone-01 <-> PX4 instance 1, topics under /px4_1, MAV_SYS_ID 2). Nothing is hardcoded to one
drone.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from aeris_px4_bridge.protocol import ack, hello
from aeris_px4_bridge.vehicle import Vehicle, Waypoint


def topic_safe(drone_id: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in drone_id)


class BridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("aeris_px4_bridge")
        self.declare_parameter("vehicles", ["drone-01:1", "drone-02:2", "drone-03:3"])
        self.declare_parameter("ws_host", "0.0.0.0")
        self.declare_parameter("ws_port", 8765)
        self.declare_parameter("telemetry_hz", 4.0)
        self.declare_parameter("control_hz", 10.0)
        self.declare_parameter("waypoint_tolerance_m", 3.0)
        self.declare_parameter("cruise_speed_mps", 8.0)
        self.declare_parameter("thermal_vehicles", ["drone-01", "drone-03"])

        mapping: dict[str, int] = {}
        for entry in self.get_parameter("vehicles").value:
            drone_id, instance = str(entry).split(":")
            mapping[drone_id] = int(instance)
        self.vehicles: dict[str, Vehicle] = {
            drone_id: Vehicle(
                node=self,
                drone_id=drone_id,
                instance=instance,
                waypoint_tolerance_m=float(self.get_parameter("waypoint_tolerance_m").value),
                cruise_speed_mps=float(self.get_parameter("cruise_speed_mps").value),
            )
            for drone_id, instance in mapping.items()
        }
        self._thermal = set(self.get_parameter("thermal_vehicles").value)
        self._outbox: asyncio.Queue[str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_clients: set[object] = set()  # not _clients: rclpy.Node uses that name

        for drone_id in self.vehicles:
            # ROS topic names allow only [A-Za-z0-9_]; AERIS ids like "drone-01" become "drone_01".
            self.create_subscription(
                String, f"/aeris/{topic_safe(drone_id)}/observation", self._make_observation_cb(drone_id), 10
            )

        control_period = 1.0 / float(self.get_parameter("control_hz").value)
        telemetry_period = 1.0 / float(self.get_parameter("telemetry_hz").value)
        self.create_timer(control_period, self._control_tick)
        self.create_timer(telemetry_period, self._telemetry_tick)

        host = str(self.get_parameter("ws_host").value)
        port = int(self.get_parameter("ws_port").value)
        threading.Thread(target=self._serve, args=(host, port), daemon=True, name="aeris-ws").start()
        self.get_logger().info(f"bridge up: vehicles={mapping} ws://{host}:{port}")

    # ------------------------------------------------------------- ROS side

    def _control_tick(self) -> None:
        for vehicle in self.vehicles.values():
            vehicle.tick()

    def _telemetry_tick(self) -> None:
        stamp = datetime.now(tz=UTC).isoformat()
        for drone_id, vehicle in self.vehicles.items():
            payload = vehicle.telemetry(stamp, camera=True, thermal=drone_id in self._thermal)
            if payload is not None:
                self._broadcast(payload)

    def _make_observation_cb(self, drone_id: str):  # noqa: ANN202
        def _cb(msg: String) -> None:
            try:
                data = json.loads(msg.data)
            except ValueError:
                self.get_logger().warning(f"bad observation JSON from {drone_id}")
                return
            data.setdefault("type", "observation")
            data["drone_id"] = drone_id
            data.setdefault("timestamp", datetime.now(tz=UTC).isoformat())
            self._broadcast(data)

        return _cb

    # ------------------------------------------------------------- AERIS side (WebSocket)

    def _serve(self, host: str, port: int) -> None:
        import websockets  # noqa: PLC0415 - imported in the server thread only

        async def handler(conn, _path=None) -> None:  # noqa: ANN001
            # websockets < 11 (Debian/Ubuntu apt package) passes (connection, path); newer
            # versions pass only the connection. Accept both.
            self._ws_clients.add(conn)
            try:
                await conn.send(json.dumps(hello({d: v.instance for d, v in self.vehicles.items()})))
                async for raw in conn:
                    reply = self._handle_command(raw)
                    if reply is not None:
                        await conn.send(json.dumps(reply))
            finally:
                self._ws_clients.discard(conn)

        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._outbox = asyncio.Queue()
            async with websockets.serve(handler, host, port):
                while True:
                    message = await self._outbox.get()
                    for conn in list(self._ws_clients):
                        try:
                            await conn.send(message)
                        except Exception:  # noqa: BLE001 - a dropped client must not stop others
                            self._ws_clients.discard(conn)

        asyncio.run(main())

    def _broadcast(self, payload: dict) -> None:
        if self._loop is None or self._outbox is None:
            return
        self._loop.call_soon_threadsafe(self._outbox.put_nowait, json.dumps(payload))

    def _handle_command(self, raw: str) -> dict | None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        request_id = str(msg.get("request_id", ""))
        drone_id = str(msg.get("drone_id", ""))
        vehicle = self.vehicles.get(drone_id)
        if vehicle is None:
            return ack(request_id, drone_id, False, f"unknown drone {drone_id}")
        kind = msg.get("type")
        if kind == "send_mission":
            waypoints = [
                Waypoint(float(w["latitude"]), float(w["longitude"]), float(w["altitude_m"]), w.get("speed_mps"))
                for w in msg.get("waypoints", [])
            ]
            ok, text = vehicle.send_mission(waypoints)
        elif kind == "return_to_base":
            ok, text = vehicle.return_to_base()
        elif kind == "hold":
            ok, text = vehicle.hold()
        else:
            ok, text = False, f"unknown command {kind}"
        self.get_logger().info(f"{drone_id} {kind}: {'ok' if ok else 'rejected'} ({text})")
        return ack(request_id, drone_id, ok, text)


def main() -> None:
    rclpy.init()
    node = BridgeNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

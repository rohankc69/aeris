"""The ROS bridge's hand-written protocol dicts must parse with the backend's typed protocol."""

import importlib.util
import json
from pathlib import Path

from aeris.fleet.px4 import PROTOCOL_VERSION
from aeris.fleet.px4.protocol import Ack, Hello, parse_inbound

BRIDGE = (
    Path(__file__).resolve().parents[3]
    / "ros_ws"
    / "src"
    / "aeris_px4_bridge"
    / "aeris_px4_bridge"
    / "protocol.py"
)


def load_bridge_protocol():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("bridge_protocol", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_and_backend_agree_on_protocol_version() -> None:
    assert load_bridge_protocol().PROTOCOL_VERSION == PROTOCOL_VERSION


def test_bridge_hello_and_ack_parse_as_backend_messages() -> None:
    bridge = load_bridge_protocol()
    hello = parse_inbound(json.dumps(bridge.hello({"drone-01": 1, "drone-02": 2})))
    assert isinstance(hello, Hello) and hello.vehicles["drone-02"] == 2
    ack = parse_inbound(json.dumps(bridge.ack("r1", "drone-01", True, "ok")))
    assert isinstance(ack, Ack) and ack.accepted and ack.request_id == "r1"

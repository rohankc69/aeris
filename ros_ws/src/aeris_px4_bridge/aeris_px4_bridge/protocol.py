"""Wire protocol constants shared with backend/aeris/fleet/px4/protocol.py.

Kept as plain dicts here so the bridge has no dependency on the AERIS backend package.
Bump PROTOCOL_VERSION on any incompatible change, in both places.
"""

PROTOCOL_VERSION = 1


def hello(vehicles: dict[str, int]) -> dict:
    return {"type": "hello", "protocol_version": PROTOCOL_VERSION, "vehicles": vehicles}


def ack(request_id: str, drone_id: str, accepted: bool, message: str = "") -> dict:
    return {
        "type": "ack",
        "request_id": request_id,
        "drone_id": drone_id,
        "accepted": accepted,
        "message": message,
    }

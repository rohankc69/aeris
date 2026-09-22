"""PX4 fleet adapter: talks to the ``aeris_px4_bridge`` ROS 2 node over a WebSocket.

Nothing here imports ROS. The bridge translates PX4 uORB topics to the JSON protocol in
``protocol.py`` and back; the backend stays ROS-free.
"""

from aeris.fleet.px4.adapter import PX4FleetAdapter
from aeris.fleet.px4.protocol import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION", "PX4FleetAdapter"]

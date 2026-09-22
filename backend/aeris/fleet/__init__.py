"""Fleet adapters: the only place vehicle transports live.

``FleetAdapter`` is the protocol the Mission Manager talks to. ``FakeFleetAdapter`` is a
simulated fleet that needs no ROS or PX4. ``PX4FleetAdapter`` is planned for Phase 4.
"""

from aeris.fleet.base import CommandResult, FleetAdapter
from aeris.fleet.fake import FakeFleetAdapter, SimDroneConfig, SimSensorTarget

__all__ = ["CommandResult", "FakeFleetAdapter", "FleetAdapter", "SimDroneConfig", "SimSensorTarget"]

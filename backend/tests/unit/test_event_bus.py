import pytest
from pydantic import ValidationError

from aeris.domain import GeoPoint
from aeris.events import (
    DomainEvent,
    InMemoryEventBus,
    MissionStarted,
    TelemetryReceived,
    ZoneAssigned,
)


async def test_typed_and_wildcard_subscriptions() -> None:
    bus = InMemoryEventBus()
    typed: list[str] = []
    everything: list[str] = []

    async def on_zone(event: DomainEvent) -> None:
        typed.append(event.type_name)

    async def on_any(event: DomainEvent) -> None:
        everything.append(event.type_name)

    bus.subscribe(ZoneAssigned, on_zone)
    bus.subscribe(None, on_any)

    await bus.publish(MissionStarted(mission_id="m"))
    await bus.publish(ZoneAssigned(mission_id="m", zone_id="A1", drone_id="d", assignment_id="a"))

    assert typed == ["ZoneAssigned"]
    assert everything == ["MissionStarted", "ZoneAssigned"]


async def test_failing_handler_does_not_block_others() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def bad(event: DomainEvent) -> None:
        raise RuntimeError("boom")

    async def good(event: DomainEvent) -> None:
        seen.append(event.event_id)

    bus.subscribe(None, bad)
    bus.subscribe(None, good)
    event = TelemetryReceived(
        mission_id="m", drone_id="d", battery_percent=50, position=GeoPoint(latitude=0, longitude=0)
    )
    await bus.publish(event)
    assert seen == [event.event_id]


def test_events_are_immutable_and_named() -> None:
    event = MissionStarted(mission_id="m")
    assert event.type_name == "MissionStarted"
    with pytest.raises(ValidationError, match="frozen"):
        event.mission_id = "x"  # type: ignore[misc]

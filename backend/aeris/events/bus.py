"""In-process asynchronous event bus.

``EventBus`` is a protocol so a broker-backed implementation can replace ``InMemoryEventBus``
later without touching application logic. Handlers run sequentially in subscription order,
which keeps simulation runs deterministic.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Protocol

from aeris.events.events import DomainEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: type[DomainEvent] | None, handler: EventHandler) -> None:
        """Subscribe to one event class (and its subclasses) or, with ``None``, to everything."""
        ...


class InMemoryEventBus:
    """Deterministic in-process bus. A failing handler is logged and does not stop others."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent] | None, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[DomainEvent] | None, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for key, handlers in list(self._handlers.items()):
            if key is not None and not isinstance(event, key):
                continue
            for handler in list(handlers):
                try:
                    await handler(event)
                except Exception:
                    logger.exception(
                        "event handler failed",
                        extra={"event_type": event.type_name, "mission_id": event.mission_id},
                    )

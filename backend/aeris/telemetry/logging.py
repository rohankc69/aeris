"""Structured logging via structlog.

Every log line carries whatever context the caller binds (mission_id, drone_id, event_type,
decision_id, provider, model, latency_ms). JSON in production, coloured console otherwise.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog


def configure_logging(*, json_output: bool, level: str = "INFO") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared, processors=[_extra_to_event, renderer]
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def _extra_to_event(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Lift ``logging`` ``extra=`` fields (mission_id, drone_id, ...) into the structured event."""
    record = event_dict.get("_record")
    if record is not None:
        for key, value in vars(record).items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            event_dict.setdefault(key, value)
    return event_dict


_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

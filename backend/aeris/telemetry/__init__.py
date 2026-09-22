"""Observability: structured logging and an in-process metrics registry.

Metrics are plain counters, gauges and histograms with a Prometheus text renderer, so a real
exporter can be attached later without touching mission code.
"""

from aeris.telemetry.logging import configure_logging, get_logger
from aeris.telemetry.metrics import MetricsRegistry, MissionMetrics

__all__ = ["MetricsRegistry", "MissionMetrics", "configure_logging", "get_logger"]

"""In-process metrics registry and the mission-level metrics that feed evaluation."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from aeris.domain.geo import GeoPoint
from aeris.events.bus import EventBus
from aeris.events.events import (
    CandidateEscalated,
    DecisionCompleted,
    DomainEvent,
    HumanReviewRequested,
    MissionCompleted,
    MissionStarted,
    SafetyOverrideTriggered,
    SurvivorConfirmed,
    TelemetryReceived,
    ZoneCoverageUpdated,
    ZoneReassignmentRequested,
)

Labels = tuple[tuple[str, str], ...]


def _labels(**kwargs: str) -> Labels:
    return tuple(sorted(kwargs.items()))


@dataclass
class Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    n: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.n += 1
        self.total += value
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[i] += 1
                return
        self.counts[-1] += 1

    @property
    def mean(self) -> float | None:
        return self.total / self.n if self.n else None


class MetricsRegistry:
    """Counters, gauges and histograms keyed by name and label set."""

    DEFAULT_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)

    def __init__(self) -> None:
        self._counters: dict[str, dict[Labels, float]] = defaultdict(dict)
        self._gauges: dict[str, dict[Labels, float]] = defaultdict(dict)
        self._histograms: dict[str, dict[Labels, Histogram]] = defaultdict(dict)
        self._help: dict[str, str] = {}

    def describe(self, name: str, help_text: str) -> None:
        self._help[name] = help_text

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = _labels(**labels)
        self._counters[name][key] = self._counters[name].get(key, 0.0) + value

    def set(self, name: str, value: float, **labels: str) -> None:
        self._gauges[name][_labels(**labels)] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _labels(**labels)
        hist = self._histograms[name].get(key)
        if hist is None:
            hist = self._histograms[name][key] = Histogram(self.DEFAULT_BUCKETS)
        hist.observe(value)

    def counter(self, name: str, **labels: str) -> float:
        return self._counters.get(name, {}).get(_labels(**labels), 0.0)

    def gauge(self, name: str, **labels: str) -> float | None:
        return self._gauges.get(name, {}).get(_labels(**labels))

    def histogram(self, name: str, **labels: str) -> Histogram | None:
        return self._histograms.get(name, {}).get(_labels(**labels))

    def snapshot(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, series in self._counters.items():
            out[name] = {_label_str(k): v for k, v in series.items()}
        for name, series in self._gauges.items():
            out[name] = {_label_str(k): v for k, v in series.items()}
        for name, hists in self._histograms.items():
            out[name] = {
                _label_str(k): {"count": h.n, "sum": h.total, "mean": h.mean}
                for k, h in hists.items()
            }
        return out

    def render_prometheus(self) -> str:
        """Prometheus text exposition, so an exporter is a one-line addition later."""
        lines: list[str] = []
        for name, series in sorted(self._counters.items()):
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} counter")
            lines.extend(f"{name}{_prom_labels(k)} {v}" for k, v in series.items())
        for name, series in sorted(self._gauges.items()):
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} gauge")
            lines.extend(f"{name}{_prom_labels(k)} {v}" for k, v in series.items())
        for name, hists in sorted(self._histograms.items()):
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} histogram")
            for k, h in hists.items():
                cumulative = 0
                for bound, count in zip(h.buckets, h.counts, strict=False):
                    cumulative += count
                    lines.append(f"{name}_bucket{_prom_labels(k, le=str(bound))} {cumulative}")
                lines.append(f"{name}_bucket{_prom_labels(k, le='+Inf')} {h.n}")
                lines.append(f"{name}_sum{_prom_labels(k)} {h.total}")
                lines.append(f"{name}_count{_prom_labels(k)} {h.n}")
        return "\n".join(lines) + "\n"


def _label_str(labels: Labels) -> str:
    return ",".join(f"{k}={v}" for k, v in labels) or "_"


def _prom_labels(labels: Labels, **extra: str) -> str:
    items = sorted([*labels, *extra.items()])
    if not items:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}"


class MissionMetrics:
    """Subscribes to a mission's event bus and maintains the metrics evaluation compares."""

    def __init__(self, registry: MetricsRegistry | None = None) -> None:
        self.registry = registry or MetricsRegistry()
        self._last_position: dict[str, GeoPoint] = {}
        self._first_battery: dict[str, float] = {}
        self._last_battery: dict[str, float] = {}
        self._started_at: float | None = None
        self._completed_at: float | None = None
        self._located_at: float | None = None
        r = self.registry
        r.describe("aeris_decisions_total", "AI-assisted decisions by provider and type")
        r.describe("aeris_decision_latency_ms", "Decision provider latency")
        r.describe("aeris_safety_overrides_total", "Safety Governor overrides by rule")
        r.describe("aeris_human_reviews_total", "Human review requests")
        r.describe("aeris_reassignments_total", "Zone reassignments")
        r.describe("aeris_coverage_fraction", "Search coverage of the mission")
        r.describe("aeris_distance_travelled_m", "Distance travelled per drone")
        r.describe("aeris_battery_consumed_percent", "Battery consumed per drone")

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(None, self.on_event)

    async def on_event(self, event: DomainEvent) -> None:
        r = self.registry
        ts = event.timestamp.timestamp()
        if isinstance(event, MissionStarted):
            self._started_at = ts
        elif isinstance(event, MissionCompleted):
            self._completed_at = ts
        elif isinstance(event, SurvivorConfirmed):
            self._located_at = ts
        elif isinstance(event, DecisionCompleted):
            r.inc(
                "aeris_decisions_total", provider=event.provider, decision_type=event.decision_type
            )
            if event.safety_override:
                r.inc("aeris_ai_overridden_total", provider=event.provider)
        elif isinstance(event, SafetyOverrideTriggered):
            r.inc("aeris_safety_overrides_total", rule=event.rule)
        elif isinstance(event, HumanReviewRequested):
            r.inc("aeris_human_reviews_total", subject=event.subject_type)
        elif isinstance(event, ZoneReassignmentRequested):
            r.inc("aeris_reassignments_total")
        elif isinstance(event, CandidateEscalated):
            r.inc("aeris_candidates_total")
        elif isinstance(event, ZoneCoverageUpdated):
            pass  # coverage gauge is set from snapshots by the runner
        elif isinstance(event, TelemetryReceived):
            self._on_telemetry(event)

    def _on_telemetry(self, event: TelemetryReceived) -> None:
        last = self._last_position.get(event.drone_id)
        if last is not None:
            step = last.distance_to(event.position)
            if math.isfinite(step) and step < 1000:  # ignore teleports from resets
                self.registry.inc("aeris_distance_travelled_m", step, drone_id=event.drone_id)
        self._last_position[event.drone_id] = event.position
        self._first_battery.setdefault(event.drone_id, event.battery_percent)
        self._last_battery[event.drone_id] = event.battery_percent
        consumed = self._first_battery[event.drone_id] - event.battery_percent
        self.registry.set(
            "aeris_battery_consumed_percent", max(0.0, consumed), drone_id=event.drone_id
        )

    def observe_decision_latency(self, provider: str, latency_ms: float | None) -> None:
        if latency_ms is not None:
            self.registry.observe("aeris_decision_latency_ms", latency_ms, provider=provider)

    def observe_tokens_and_cost(
        self, provider: str, tokens: int | None, cost: float | None
    ) -> None:
        if tokens:
            self.registry.inc("aeris_decision_input_tokens_total", tokens, provider=provider)
        if cost:
            self.registry.inc("aeris_decision_cost_usd_total", cost, provider=provider)

    def set_coverage(self, fraction: float) -> None:
        self.registry.set("aeris_coverage_fraction", fraction)

    def summary(self) -> dict[str, object]:
        r = self.registry
        decisions = {
            _label_str(k): v for k, v in r._counters.get("aeris_decisions_total", {}).items()
        }
        latency = {
            _label_str(k): h.mean
            for k, h in r._histograms.get("aeris_decision_latency_ms", {}).items()
        }
        distance = sum(r._counters.get("aeris_distance_travelled_m", {}).values())
        battery = sum(r._gauges.get("aeris_battery_consumed_percent", {}).values())
        return {
            "mission_completion_s": (
                self._completed_at - self._started_at
                if self._started_at is not None and self._completed_at is not None
                else None
            ),
            "time_to_survivor_s": (
                self._located_at - self._started_at
                if self._started_at is not None and self._located_at is not None
                else None
            ),
            "coverage_fraction": r.gauge("aeris_coverage_fraction"),
            "distance_travelled_m": distance,
            "battery_consumed_percent": battery,
            "reassignments": r.counter("aeris_reassignments_total"),
            "decisions": decisions,
            "decision_latency_ms_mean": latency,
            "input_tokens": {
                _label_str(k): v
                for k, v in r._counters.get("aeris_decision_input_tokens_total", {}).items()
            },
            "estimated_cost_usd": {
                _label_str(k): v
                for k, v in r._counters.get("aeris_decision_cost_usd_total", {}).items()
            },
            "safety_overrides": {
                _label_str(k): v
                for k, v in r._counters.get("aeris_safety_overrides_total", {}).items()
            },
            "ai_overridden": sum(r._counters.get("aeris_ai_overridden_total", {}).values()),
            "human_reviews": sum(r._counters.get("aeris_human_reviews_total", {}).values()),
            "candidates": r.counter("aeris_candidates_total"),
        }

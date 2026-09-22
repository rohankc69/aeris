from datetime import UTC, datetime

from aeris.domain import GeoPoint
from aeris.domain.enums import MissionStatus
from aeris.events import (
    DecisionCompleted,
    InMemoryEventBus,
    MissionCompleted,
    MissionStarted,
    SafetyOverrideTriggered,
    TelemetryReceived,
)
from aeris.telemetry import MetricsRegistry, MissionMetrics

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_registry_counters_gauges_histograms_and_prometheus_text() -> None:
    r = MetricsRegistry()
    r.describe("aeris_decisions_total", "decisions")
    r.inc("aeris_decisions_total", provider="rules")
    r.inc("aeris_decisions_total", provider="rules")
    r.set("aeris_coverage_fraction", 0.42)
    r.observe("aeris_decision_latency_ms", 12, provider="openrouter")
    r.observe("aeris_decision_latency_ms", 300, provider="openrouter")
    assert r.counter("aeris_decisions_total", provider="rules") == 2
    assert r.gauge("aeris_coverage_fraction") == 0.42
    h = r.histogram("aeris_decision_latency_ms", provider="openrouter")
    assert h is not None and h.n == 2 and h.mean == 156
    text = r.render_prometheus()
    assert "# TYPE aeris_decisions_total counter" in text
    assert 'aeris_decisions_total{provider="rules"} 2.0' in text
    assert 'aeris_decision_latency_ms_bucket{le="+Inf",provider="openrouter"} 2' in text
    assert 'aeris_decision_latency_ms_count{provider="openrouter"} 2' in text
    snap = r.snapshot()
    assert snap["aeris_coverage_fraction"] == {"_": 0.42}


async def test_mission_metrics_follow_events() -> None:
    bus = InMemoryEventBus()
    metrics = MissionMetrics()
    metrics.attach(bus)
    await bus.publish(MissionStarted(mission_id="m", timestamp=T0))
    await bus.publish(
        TelemetryReceived(
            mission_id="m",
            drone_id="d1",
            battery_percent=100,
            position=GeoPoint(latitude=0, longitude=0),
        )
    )
    await bus.publish(
        TelemetryReceived(
            mission_id="m",
            drone_id="d1",
            battery_percent=90,
            position=GeoPoint(latitude=0, longitude=0.001),
        )
    )
    await bus.publish(
        DecisionCompleted(
            mission_id="m",
            decision_id="x",
            decision_type="drone_disposition",
            drone_id="d1",
            provider="rules",
            selected_value="CONTINUE_SEARCH",
            final_action="RETURN_TO_BASE",
            safety_override=True,
        )
    )
    await bus.publish(
        SafetyOverrideTriggered(
            mission_id="m",
            safety_event_id="s",
            drone_id="d1",
            rule="mandatory_return_battery",
            proposed_action="rules:CONTINUE_SEARCH",
            safe_alternative="RETURN_TO_BASE",
        )
    )
    await bus.publish(
        MissionCompleted(
            mission_id="m", final_status=MissionStatus.COMPLETED, timestamp=T0.replace(minute=5)
        )
    )
    metrics.observe_decision_latency("rules", 4.0)
    metrics.set_coverage(0.75)
    s = metrics.summary()
    assert s["mission_completion_s"] == 300
    assert s["coverage_fraction"] == 0.75
    assert 100 < float(s["distance_travelled_m"]) < 120  # type: ignore[arg-type]
    assert s["battery_consumed_percent"] == 10
    assert s["decisions"] == {"decision_type=drone_disposition,provider=rules": 1}
    assert s["ai_overridden"] == 1
    assert s["safety_overrides"] == {"rule=mandatory_return_battery": 1}
    assert s["decision_latency_ms_mean"] == {"provider=rules": 4.0}

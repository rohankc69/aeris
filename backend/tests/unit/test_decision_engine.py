from datetime import UTC, datetime

import pytest

from aeris.clock import SimClock
from aeris.config import SafetySettings
from aeris.decisions import (
    MalformedResponseError,
    MockDecisionProvider,
    ResilientDecisionProvider,
    RuleBasedDecisionProvider,
    hash_state,
)
from aeris.decisions.engine import DecisionEngine
from aeris.decisions.modules import (
    DetectionTriageInput,
    DroneDispositionInput,
    HumanReviewInput,
    ZonePriorityInput,
)
from aeris.domain.enums import LinkState

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def engine(provider: object) -> DecisionEngine:
    return DecisionEngine(
        provider=provider,
        policy=RuleBasedDecisionProvider(safety=SafetySettings()),
        clock=SimClock(T0),
    )  # type: ignore[arg-type]


def disposition(battery: float = 80) -> DroneDispositionInput:
    return DroneDispositionInput(
        battery_percent=battery,
        estimated_return_battery_percent=8,
        distance_to_base_m=700,
        zone_completion=0.4,
        connection_quality=0.9,
        link_state=LinkState.CONNECTED,
        telemetry_age_s=0.5,
    )


async def test_disposition_record_captures_everything_needed_for_audit() -> None:
    mock = MockDecisionProvider(
        fixed_choices={"drone_disposition": "HANDOFF_ZONE"}, model="mock-jev"
    )
    outcome = await engine(mock).drone_disposition(
        disposition(), mission_id="m1", drone_id="drone-02"
    )
    r = outcome.record
    assert outcome.selected == "HANDOFF_ZONE"
    assert r.mission_id == "m1" and r.drone_id == "drone-02"
    assert r.decision_type == "drone_disposition"
    assert r.provider == "mock" and r.model == "mock-jev"
    assert r.selected_value == "HANDOFF_ZONE" and r.final_action == "HANDOFF_ZONE"
    assert r.policy_value == "CONTINUE_SEARCH" and r.policy_reason == "nominal"
    assert r.input_state["battery_percent"] == 80
    assert r.input_state_hash == hash_state(r.input_state)
    assert set(r.probabilities) == {
        "CONTINUE_SEARCH",
        "RETURN_TO_BASE",
        "HANDOFF_ZONE",
        "HOLD",
        "REQUEST_HUMAN_REVIEW",
    }
    assert r.probabilities["HANDOFF_ZONE"] == pytest.approx(0.7)
    assert r.latency_ms == 0.0
    assert r.safety_override is False and r.safety_event_id is None
    assert r.fallback_reason is None
    assert r.timestamp == T0


async def test_resilient_fallback_is_visible_in_record() -> None:
    failing = MockDecisionProvider(fail_with=MalformedResponseError("openrouter", "junk"))
    provider = ResilientDecisionProvider(
        primary=failing,
        fallback=RuleBasedDecisionProvider(safety=SafetySettings()),
        clock=SimClock(T0),
    )
    outcome = await engine(provider).drone_disposition(
        disposition(20), mission_id="m", drone_id="d"
    )
    assert outcome.record.provider == "rules"
    assert outcome.record.fallback_reason == "MalformedResponseError"
    assert outcome.selected == "RETURN_TO_BASE"


async def test_bare_provider_failure_falls_back_to_policy_and_is_recorded() -> None:
    failing = MockDecisionProvider(fail_with=MalformedResponseError("mock", "junk"))
    outcome = await engine(failing).drone_disposition(disposition(), mission_id="m", drone_id="d")
    assert outcome.record.provider == "rules"
    assert outcome.record.fallback_reason == "MalformedResponseError"
    assert outcome.selected == "CONTINUE_SEARCH"


async def test_triage_never_returns_a_confirmed_person() -> None:
    mock = MockDecisionProvider(fixed_choices={"detection_triage": "POSSIBLE_SURVIVOR"})
    outcome = await engine(mock).detection_triage(
        DetectionTriageInput(
            source_sensor="thermal",
            thermal_confidence=0.8,
            visual_confidence=0.7,
            sensor_agreement=True,
            observation_count=3,
        ),
        mission_id="m",
        drone_id="d",
    )
    assert outcome.selected == "POSSIBLE_SURVIVOR"
    assert "PERSON_LOCATED" not in outcome.record.probabilities
    assert "human must confirm" in mock.calls[0].question  # type: ignore[union-attr]


async def test_score_and_probability_records() -> None:
    mock = MockDecisionProvider(
        fixed_scores={"zone_priority": 0.83}, fixed_probabilities={"human_review_gate": 0.66}
    )
    zone = await engine(mock).zone_priority(
        ZonePriorityInput(
            zone_id="C4",
            coverage=0.34,
            time_since_last_search_s=720,
            terrain="forest",
            nearby_candidate_detection=True,
            distance_from_last_known_position_m=440,
        ),
        mission_id="m",
    )
    assert zone.result.score == 0.83
    assert zone.record.score == 0.83
    assert zone.record.selected_value == "0.830"
    assert zone.record.policy_value is not None and 0 <= float(zone.record.policy_value) <= 1
    review = await engine(mock).human_review_gate(
        HumanReviewInput(sensors_disagree=True, candidate_count=2), mission_id="m"
    )
    assert review.result.probability == 0.66
    assert review.record.decision_type == "human_review_gate"
    assert review.record.drone_id is None

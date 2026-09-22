import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from aeris.clock import SimClock
from aeris.config import DecisionProviderKind, SafetySettings, load_settings
from aeris.decisions import (
    ChoiceRequest,
    DecisionTimeoutError,
    MalformedResponseError,
    MissingCredentialsError,
    MockDecisionProvider,
    OpenRouterJevProvider,
    ProbabilityRequest,
    ProviderUnavailableError,
    ResilientDecisionProvider,
    RuleBasedDecisionProvider,
    ScoreRequest,
    build_decision_provider,
    hash_state,
)
from aeris.domain.enums import Disposition, TriageDecision

DISPOSITIONS = tuple(d.value for d in Disposition)
TRIAGE = tuple(t.value for t in TriageDecision)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def disposition_request(**state: object) -> ChoiceRequest:
    base: dict[str, object] = {
        "battery_percent": 80,
        "estimated_return_battery_percent": 10,
        "distance_to_base_m": 500,
        "zone_completion": 0.3,
        "connection_quality": 0.95,
        "active_detection": False,
    }
    base.update(state)
    return ChoiceRequest(
        decision_type="drone_disposition",
        question="What should this drone do next?",
        state=base,
        choices=DISPOSITIONS,
        drone_id="drone-01",
    )


# ----------------------------------------------------------------- base


def test_state_hash_is_canonical() -> None:
    assert hash_state({"a": 1, "b": 2}) == hash_state({"b": 2, "a": 1})
    assert hash_state({"a": 1}) != hash_state({"a": 2})


def test_choice_request_rejects_duplicate_choices() -> None:
    with pytest.raises(ValueError, match="unique"):
        ChoiceRequest(decision_type="x", question="q", state={}, choices=("A", "A"))


# ----------------------------------------------------------------- mock


async def test_mock_defaults_to_rule_answer_and_records_calls() -> None:
    mock = MockDecisionProvider()
    result = await mock.choice(disposition_request())
    assert result.selected == "CONTINUE_SEARCH"
    assert result.provider == "mock"
    assert sum(result.probabilities.values()) == pytest.approx(1.0)
    assert (await mock.choice(disposition_request(battery_percent=20))).selected == "RETURN_TO_BASE"
    assert len(mock.calls) == 2
    fixed = await MockDecisionProvider(fixed_choices={"drone_disposition": "HOLD"}).choice(
        disposition_request()
    )
    assert fixed.confidence == pytest.approx(0.7)


async def test_mock_scripts_and_fixed_answers() -> None:
    mock = MockDecisionProvider(
        fixed_choices={"drone_disposition": "RETURN_TO_BASE"},
        fixed_scores={"zone_priority": 0.9},
        fixed_probabilities={"human_review_gate": 0.8},
    )
    assert (await mock.choice(disposition_request())).selected == "RETURN_TO_BASE"
    assert (
        await mock.score(ScoreRequest(decision_type="zone_priority", question="q", state={}))
    ).score == 0.9
    assert (
        await mock.probability(
            ProbabilityRequest(decision_type="human_review_gate", question="q", state={})
        )
    ).probability == 0.8

    scripted = MockDecisionProvider(
        scripts={
            "drone_disposition": lambda r: (
                "HOLD" if r.state["battery_percent"] < 50 else "CONTINUE_SEARCH"
            )
        }
    )
    assert (await scripted.choice(disposition_request(battery_percent=40))).selected == "HOLD"
    assert (
        await scripted.choice(disposition_request(battery_percent=90))
    ).selected == "CONTINUE_SEARCH"


async def test_mock_seeded_choice_is_reproducible() -> None:
    a = [
        (await MockDecisionProvider(seed=7).choice(disposition_request())).selected
        for _ in range(3)
    ]
    b = [
        (await MockDecisionProvider(seed=7).choice(disposition_request())).selected
        for _ in range(3)
    ]
    assert a == b


async def test_mock_can_fail_on_demand() -> None:
    mock = MockDecisionProvider(fail_with=DecisionTimeoutError("mock", 1.0))
    with pytest.raises(DecisionTimeoutError):
        await mock.choice(disposition_request())


# ----------------------------------------------------------------- rules


@pytest.fixture
def rules() -> RuleBasedDecisionProvider:
    return RuleBasedDecisionProvider(safety=SafetySettings())


async def test_rules_disposition_thresholds(rules: RuleBasedDecisionProvider) -> None:
    async def decide(**state: object) -> str:
        return (await rules.choice(disposition_request(**state))).selected

    assert await decide() == "CONTINUE_SEARCH"
    assert await decide(battery_percent=25) == "RETURN_TO_BASE"  # mandatory threshold
    assert await decide(battery_percent=10) == "RETURN_TO_BASE"  # critical
    assert (
        await decide(battery_percent=40, estimated_return_battery_percent=36) == "RETURN_TO_BASE"
    )  # margin
    assert await decide(battery_percent=33, zone_completion=0.2) == "HANDOFF_ZONE"
    assert await decide(battery_percent=33, zone_completion=0.8) == "CONTINUE_SEARCH"
    assert await decide(connection_quality=0.3) == "REQUEST_HUMAN_REVIEW"
    assert (
        await decide(battery_percent=33, zone_completion=0.2, active_detection=True)
        == "CONTINUE_SEARCH"
    )


async def test_rules_never_declare_survivor_found_and_respect_choices(
    rules: RuleBasedDecisionProvider,
) -> None:
    def triage(**state: object) -> ChoiceRequest:
        return ChoiceRequest(
            decision_type="detection_triage", question="q", state=state, choices=TRIAGE
        )

    strong = await rules.choice(
        triage(
            thermal_confidence=0.8,
            visual_confidence=0.75,
            sensor_agreement=True,
            observation_count=3,
        )
    )
    assert strong.selected == "POSSIBLE_SURVIVOR"
    assert "human confirmation required" in str(strong.raw)
    assert (await rules.choice(triage(thermal_confidence=0.8))).selected == "INVESTIGATE"
    assert (
        await rules.choice(triage(thermal_confidence=0.7, visual_confidence=0.2))
    ).selected == "HUMAN_REVIEW"
    assert (await rules.choice(triage(thermal_confidence=0.4))).selected == "RECHECK"
    assert (await rules.choice(triage(thermal_confidence=0.1))).selected == "IGNORE"

    limited = ChoiceRequest(
        decision_type="detection_triage",
        question="q",
        state={"thermal_confidence": 0.8},
        choices=("IGNORE", "RECHECK"),
    )
    assert (await rules.choice(limited)).selected == "IGNORE"


async def test_rules_score_and_probability(rules: RuleBasedDecisionProvider) -> None:
    hot = await rules.score(
        ScoreRequest(
            decision_type="zone_priority",
            question="q",
            state={
                "coverage": 0.0,
                "nearby_candidate_detection": True,
                "distance_from_last_known_position_m": 100,
                "time_since_last_search_s": 3600,
            },
        )
    )
    cold = await rules.score(
        ScoreRequest(
            decision_type="zone_priority",
            question="q",
            state={"coverage": 1.0, "distance_from_last_known_position_m": 5000},
        )
    )
    assert hot.score > cold.score
    assert 0 <= cold.score <= hot.score <= 1

    calm = await rules.probability(
        ProbabilityRequest(decision_type="human_review_gate", question="q", state={})
    )
    messy = await rules.probability(
        ProbabilityRequest(
            decision_type="human_review_gate",
            question="q",
            state={"sensors_disagree": True, "candidate_count": 3, "connection_quality": 0.2},
        )
    )
    assert calm.probability < 0.2
    assert messy.probability > 0.6


async def test_rules_answer_unknown_types_conservatively(rules: RuleBasedDecisionProvider) -> None:
    result = await rules.choice(
        ChoiceRequest(decision_type="mystery", question="q", state={}, choices=("SAFE", "RISKY"))
    )
    assert result.selected == "SAFE"
    assert (
        await rules.score(
            ScoreRequest(decision_type="mystery", question="q", state={}, min_score=0, max_score=10)
        )
    ).score == 5


# ----------------------------------------------------------------- openrouter


def openrouter_response(
    content: object, *, model: str = "typesafe/jev-latest", usage: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "id": "gen-1",
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content if isinstance(content, str) else json.dumps(content),
                }
            }
        ],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 120, "completion_tokens": 20, "cost": 0.00042},
    }


def provider_with(handler: object, **kwargs: object) -> OpenRouterJevProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"
    )  # type: ignore[arg-type]
    return OpenRouterJevProvider(
        api_key="test-key", model="typesafe/jev-latest", client=client, **kwargs
    )  # type: ignore[arg-type]


def test_openrouter_requires_api_key() -> None:
    with pytest.raises(MissingCredentialsError, match="OPENROUTER_API_KEY"):
        OpenRouterJevProvider(api_key=None, model="typesafe/jev-latest")


async def test_openrouter_choice_request_and_parsing() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=openrouter_response(
                {
                    "selected": "RETURN_TO_BASE",
                    "probabilities": {
                        "RETURN_TO_BASE": 0.79,
                        "HANDOFF_ZONE": 0.12,
                        "CONTINUE_SEARCH": 0.06,
                        "HOLD": 0.02,
                        "REQUEST_HUMAN_REVIEW": 0.01,
                    },
                    "reason": "margin approaching",
                }
            ),
        )

    result = await provider_with(handler).choice(disposition_request(battery_percent=22))
    assert result.selected == "RETURN_TO_BASE"
    assert result.provider == "openrouter"
    assert result.model == "typesafe/jev-latest"
    assert result.probabilities["RETURN_TO_BASE"] == pytest.approx(0.79)
    assert sum(result.probabilities.values()) == pytest.approx(1.0)
    assert result.input_tokens == 120
    assert result.estimated_cost_usd == pytest.approx(0.00042)
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.fallback_reason is None

    assert seen["url"].endswith("/chat/completions")  # type: ignore[union-attr]
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "typesafe/jev-latest"  # type: ignore[index]
    assert body["response_format"] == {"type": "json_object"}  # type: ignore[index]
    user = body["messages"][1]["content"]  # type: ignore[index]
    assert "battery_percent" in user and "Allowed choices" in user
    assert "waypoint" not in user.lower()


async def test_openrouter_score_probability_and_fenced_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "zone_priority" in body["messages"][1]["content"]:
            return httpx.Response(200, json=openrouter_response('```json\n{"score": 0.72}\n```'))
        return httpx.Response(200, json=openrouter_response({"probability": 0.9}, usage={}))

    p = provider_with(handler)
    assert (
        await p.score(ScoreRequest(decision_type="zone_priority", question="q", state={}))
    ).score == pytest.approx(0.72)
    prob = await p.probability(
        ProbabilityRequest(decision_type="human_review_gate", question="q", state={})
    )
    assert prob.probability == pytest.approx(0.9)
    assert prob.input_tokens is None


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps(["a", "list"]),
        json.dumps({"selected": "FLY_INTO_TREE"}),
        json.dumps({"selected": "HOLD", "probabilities": "high"}),
        json.dumps({"selected": "HOLD", "probabilities": {"HOLD": "very"}}),
    ],
)
async def test_openrouter_rejects_malformed_choice(content: str) -> None:
    p = provider_with(lambda _: httpx.Response(200, json=openrouter_response(content)))
    with pytest.raises(MalformedResponseError):
        await p.choice(disposition_request())


async def test_openrouter_rejects_out_of_bounds_numbers_and_missing_content() -> None:
    p = provider_with(lambda _: httpx.Response(200, json=openrouter_response({"score": 1.7})))
    with pytest.raises(MalformedResponseError, match="outside bounds"):
        await p.score(ScoreRequest(decision_type="zone_priority", question="q", state={}))
    p = provider_with(lambda _: httpx.Response(200, json={"choices": []}))
    with pytest.raises(MalformedResponseError, match="no message content"):
        await p.probability(ProbabilityRequest(decision_type="x", question="q", state={}))


async def test_openrouter_timeout_and_http_errors() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(DecisionTimeoutError):
        await provider_with(timeout).choice(disposition_request())

    with pytest.raises(ProviderUnavailableError) as retryable:
        await provider_with(lambda _: httpx.Response(503)).choice(disposition_request())
    assert retryable.value.retryable

    with pytest.raises(ProviderUnavailableError) as fatal:
        await provider_with(lambda _: httpx.Response(401)).choice(disposition_request())
    assert not fatal.value.retryable


# ----------------------------------------------------------------- resilience


def resilient(
    primary: MockDecisionProvider, clock: SimClock | None = None, **kwargs: object
) -> ResilientDecisionProvider:
    return ResilientDecisionProvider(
        primary=primary,
        fallback=RuleBasedDecisionProvider(safety=SafetySettings()),
        clock=clock or SimClock(T0),
        timeout_s=0.05,
        max_retries=1,
        circuit_failure_threshold=2,
        circuit_reset_s=30,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_resilient_passes_through_success() -> None:
    primary = MockDecisionProvider(fixed_choices={"drone_disposition": "HOLD"})
    result = await resilient(primary).choice(disposition_request())
    assert result.selected == "HOLD"
    assert result.provider == "mock"
    assert result.fallback_reason is None


async def test_resilient_falls_back_on_error_with_reason() -> None:
    primary = MockDecisionProvider(fail_with=MalformedResponseError("mock", "garbage"))
    result = await resilient(primary).choice(disposition_request(battery_percent=20))
    assert result.provider == "rules"
    assert result.selected == "RETURN_TO_BASE"
    assert result.fallback_reason == "MalformedResponseError"
    assert len(primary.calls) == 1  # non-retryable: no retry


async def test_resilient_retries_retryable_errors_then_falls_back() -> None:
    primary = MockDecisionProvider(fail_with=ProviderUnavailableError("mock", "503"))
    result = await resilient(primary).choice(disposition_request())
    assert result.fallback_reason == "ProviderUnavailableError"
    assert len(primary.calls) == 2  # initial + one retry, bounded


async def test_resilient_times_out_slow_primary() -> None:
    class Slow(MockDecisionProvider):
        async def choice(self, request: ChoiceRequest) -> object:  # type: ignore[override]
            await asyncio.sleep(1)
            return await super().choice(request)

    result = await resilient(Slow()).choice(disposition_request())
    assert result.provider == "rules"
    assert result.fallback_reason == "timeout"


async def test_circuit_breaker_opens_and_half_opens() -> None:
    clock = SimClock(T0)
    primary = MockDecisionProvider(fail_with=ProviderUnavailableError("mock", "down"))
    wrapped = resilient(primary, clock)
    await wrapped.choice(disposition_request())
    assert not wrapped.circuit_open
    await wrapped.choice(disposition_request())
    assert wrapped.circuit_open
    calls_before = len(primary.calls)
    result = await wrapped.choice(disposition_request())
    assert result.fallback_reason == "circuit_open"
    assert len(primary.calls) == calls_before  # primary not touched while open

    clock.advance(31)
    primary.fail_with = None
    primary.fixed_choices = {"drone_disposition": "HOLD"}
    recovered = await wrapped.choice(disposition_request())
    assert recovered.selected == "HOLD" and recovered.fallback_reason is None
    assert not wrapped.circuit_open


# ----------------------------------------------------------------- factory


def test_factory_selects_provider_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimClock(T0)
    assert isinstance(
        build_decision_provider(load_settings(_env_file=None), clock=clock), MockDecisionProvider
    )

    monkeypatch.setenv("AERIS_DECISION_PROVIDER", "rules")
    assert isinstance(
        build_decision_provider(load_settings(_env_file=None), clock=clock),
        RuleBasedDecisionProvider,
    )

    monkeypatch.setenv("AERIS_DECISION_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-2026")
    provider = build_decision_provider(load_settings(_env_file=None), clock=clock)
    assert isinstance(provider, ResilientDecisionProvider)
    assert provider.name == "openrouter"
    assert DecisionProviderKind.OPENROUTER.is_hosted

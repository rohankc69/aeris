import pytest

from aeris.decisions import MockDecisionProvider
from aeris.evaluation import compare_results, load_result, replay_records, run_evaluation
from aeris.simulation import load_scenario

pytestmark = pytest.mark.integration


async def test_run_evaluation_writes_a_complete_result(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result, path = await run_evaluation(
        "low_battery", provider="rules", seed=11, output_dir=tmp_path
    )
    assert path is not None and path.exists()
    assert result.provider == "rules" and result.seed == 11
    assert result.summary.final_status.value == "COMPLETED"
    assert result.decisions and all(d.provider == "rules" for d in result.decisions)
    assert result.safety_events
    metrics = result.summary.metrics
    assert isinstance(metrics, dict) and metrics["coverage_fraction"] == pytest.approx(1.0)
    assert float(metrics["distance_travelled_m"]) > 1000  # type: ignore[arg-type]
    reloaded = load_result(path)
    assert reloaded.key_metrics == result.key_metrics
    assert len(reloaded.decisions) == len(result.decisions)


async def test_compare_reports_agreement_between_providers(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rules, _ = await run_evaluation("basic_search", provider="rules", seed=1, output_dir=tmp_path)
    mock, _ = await run_evaluation("basic_search", provider="mock", seed=1, output_dir=tmp_path)
    comparison = compare_results([rules, mock])
    assert set(comparison["results"]) == {"basic_search/rules", "basic_search/mock"}  # type: ignore[arg-type]
    assert comparison["shared_inputs"] > 0
    # the mock defaults to the rule policy, so on identical inputs they agree fully
    assert comparison["decision_agreement"] == {"mock~rules": 1.0}


async def test_replay_recorded_inputs_through_another_provider() -> None:
    reckless = MockDecisionProvider(fixed_choices={"drone_disposition": "HOLD"})
    recorded, _ = await run_evaluation("basic_search", provider="mock", decision_provider=reckless)
    dispositions = [d for d in recorded.decisions if d.decision_type == "drone_disposition"]
    assert dispositions and all(d.selected_value == "HOLD" for d in dispositions)
    replay = await replay_records(dispositions, provider="rules")
    assert replay.provider == "rules"
    assert replay.records == len(dispositions)
    assert replay.agreement_rate == 0.0  # rules never HOLD a healthy drone
    assert replay.disagreements[0]["recorded"] == "HOLD"


async def test_hosted_provider_requires_credentials() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        await run_evaluation(load_scenario("basic_search"), provider="openrouter")

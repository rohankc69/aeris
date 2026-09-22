from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from aeris.clock import SimClock
from aeris.config import DecisionProviderKind, Settings, load_settings
from aeris.decisions.base import ChoiceRequest, DecisionProvider, ProbabilityRequest, ScoreRequest
from aeris.decisions.factory import build_decision_provider
from aeris.domain.enums import Disposition, TriageDecision
from aeris.domain.models import DecisionRecord
from aeris.simulation.runner import ScenarioRunner, SimulationSummary
from aeris.simulation.scenario import Scenario, load_scenario

CHOICES: dict[str, tuple[str, ...]] = {
    "drone_disposition": tuple(d.value for d in Disposition),
    "detection_triage": tuple(t.value for t in TriageDecision),
}


class EvalResult(BaseModel):
    """One scenario run under one provider. Serialised to ``evals/out``."""

    schema_version: int = 1
    scenario: str
    provider: str
    model: str | None
    seed: int
    started_at: datetime
    summary: SimulationSummary
    decisions: list[DecisionRecord] = Field(default_factory=list)
    safety_events: list[dict[str, object]] = Field(default_factory=list)

    @property
    def key_metrics(self) -> dict[str, object]:
        s = self.summary
        m: dict[str, object] = dict(s.metrics)
        latency_raw = m.get("decision_latency_ms_mean")
        latency: dict[str, float | None] = (
            {str(k): v for k, v in latency_raw.items()} if isinstance(latency_raw, dict) else {}
        )
        cost = m.get("estimated_cost_usd", {})
        return {
            "final_status": s.final_status.value,
            "mission_time_s": s.elapsed_s,
            "person_located_at_s": s.person_located_at_s,
            "coverage": round(s.coverage_fraction, 3),
            "decisions": s.decisions,
            "safety_overrides": s.safety_overrides,
            "ai_overridden": m.get("ai_overridden"),
            "human_reviews": m.get("human_reviews"),
            "reassignments": s.reassignments,
            "latency_ms_mean": {k: round(v, 1) for k, v in latency.items() if v is not None},
            "cost_usd": cost,
        }


def _settings_for(provider: str) -> Settings:
    base = load_settings()
    kind = DecisionProviderKind(provider)
    overrides: dict[str, object] = {"decision_provider": kind}
    if kind.is_hosted and not base.openrouter_api_key:
        msg = f"provider {provider} requires OPENROUTER_API_KEY"
        raise ValueError(msg)
    return base.model_copy(update=overrides)


async def run_evaluation(
    scenario: str | Scenario,
    *,
    provider: str,
    seed: int | None = None,
    output_dir: Path | None = None,
    decision_provider: DecisionProvider | None = None,
) -> tuple[EvalResult, Path | None]:
    spec = scenario if isinstance(scenario, Scenario) else load_scenario(scenario)
    if seed is not None:
        spec = spec.model_copy(update={"seed": seed})
    settings = _settings_for(provider)
    runner = ScenarioRunner(spec, settings=settings, decision_provider=decision_provider)
    summary = await runner.run()
    model = next((r.model for r in runner.world.decisions if r.model), None)
    result = EvalResult(
        scenario=spec.name,
        provider=runner.decision_provider.name,
        model=model,
        seed=spec.seed,
        started_at=datetime.now(tz=UTC),
        summary=summary,
        decisions=list(runner.world.decisions),
        safety_events=[e.model_dump(mode="json") for e in runner.world.safety_events],
    )
    path: Path | None = None
    if output_dir is not None:
        path = await asyncio.to_thread(save_result, result, output_dir)
    return result, path


def save_result(result: EvalResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.started_at.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{result.scenario}_{result.provider}_{stamp}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_result(path: Path) -> EvalResult:
    return EvalResult.model_validate_json(path.read_text(encoding="utf-8"))


def compare_results(results: list[EvalResult]) -> dict[str, object]:
    """Side-by-side key metrics, plus decision agreement between providers on shared inputs."""
    table = {f"{r.scenario}/{r.provider}": r.key_metrics for r in results}
    agreement: dict[str, float] = {}
    by_hash: dict[str, dict[str, str]] = {}
    for r in results:
        for d in r.decisions:
            by_hash.setdefault(f"{d.decision_type}:{d.input_state_hash}", {})[r.provider] = (
                d.selected_value
            )
    providers = sorted({r.provider for r in results})
    for i, a in enumerate(providers):
        for b in providers[i + 1 :]:
            shared = [v for v in by_hash.values() if a in v and b in v]
            if shared:
                agreement[f"{a}~{b}"] = round(sum(v[a] == v[b] for v in shared) / len(shared), 3)
    return {"results": table, "decision_agreement": agreement, "shared_inputs": len(by_hash)}


class ReplayResult(BaseModel):
    provider: str
    records: int
    agreements: int
    disagreements: list[dict[str, object]]

    @property
    def agreement_rate(self) -> float | None:
        return self.agreements / self.records if self.records else None


async def replay_records(
    records: list[DecisionRecord],
    *,
    provider: str,
    decision_provider: DecisionProvider | None = None,
) -> ReplayResult:
    """Re-ask a provider every recorded question, offline, and compare answers."""
    settings = _settings_for(provider)
    prov = decision_provider or build_decision_provider(settings, clock=SimClock())
    agreements = 0
    disagreements: list[dict[str, object]] = []
    for record in records:
        if record.decision_type in CHOICES:
            request = ChoiceRequest(
                decision_type=record.decision_type,
                question="replay",
                state=record.input_state,
                choices=CHOICES[record.decision_type],
                mission_id=record.mission_id,
                drone_id=record.drone_id,
            )
            answer = (await prov.choice(request)).selected
        elif record.decision_type == "zone_priority":
            score_request = ScoreRequest(
                decision_type=record.decision_type,
                question="replay",
                state=record.input_state,
                mission_id=record.mission_id,
            )
            answer = f"{(await prov.score(score_request)).score:.3f}"
        else:
            prob_request = ProbabilityRequest(
                decision_type=record.decision_type,
                question="replay",
                state=record.input_state,
                mission_id=record.mission_id,
            )
            answer = f"{(await prov.probability(prob_request)).probability:.3f}"
        if answer == record.selected_value:
            agreements += 1
        else:
            disagreements.append(
                {
                    "decision_id": record.decision_id,
                    "decision_type": record.decision_type,
                    "recorded": record.selected_value,
                    "recorded_provider": record.provider,
                    "replayed": answer,
                    "input_state_hash": record.input_state_hash,
                }
            )
    return ReplayResult(
        provider=prov.name, records=len(records), agreements=agreements, disagreements=disagreements
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

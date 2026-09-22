"""Scriptable, deterministic provider (**mocked** behavior) for tests, CI, and demos.

Unless scripted, the mock answers exactly like ``RuleBasedDecisionProvider`` so offline
missions behave sensibly, while still recording every call and reporting ``provider="mock"``.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from aeris.config import SafetySettings
from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    DecisionProviderError,
    ProbabilityRequest,
    ProbabilityResult,
    ScoreRequest,
    ScoreResult,
    normalise_probabilities,
)
from aeris.decisions.rules import RuleBasedDecisionProvider

ChoiceScript = Callable[[ChoiceRequest], str]


@dataclass
class MockDecisionProvider:
    """Answers are chosen by, in order: a per-type script, a per-type fixed value, a seeded
    random pick, or the rule-based answer. Every request is recorded for assertions."""

    scripts: dict[str, ChoiceScript] = field(default_factory=dict)
    fixed_choices: dict[str, str] = field(default_factory=dict)
    fixed_scores: dict[str, float] = field(default_factory=dict)
    fixed_probabilities: dict[str, float] = field(default_factory=dict)
    seed: int | None = None
    fail_with: DecisionProviderError | None = None
    model: str = "mock-v1"
    calls: list[ChoiceRequest | ScoreRequest | ProbabilityRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)  # noqa: S311 - simulation, not security
        self._rules = RuleBasedDecisionProvider(safety=SafetySettings(), model=self.model)

    @property
    def name(self) -> str:
        return "mock"

    async def choice(self, request: ChoiceRequest) -> ChoiceResult:
        self._record(request)
        if request.decision_type in self.scripts:
            selected = self.scripts[request.decision_type](request)
        elif request.decision_type in self.fixed_choices:
            selected = self.fixed_choices[request.decision_type]
        elif self.seed is not None:
            selected = self._rng.choice(request.choices)
        else:
            ruled = await self._rules.choice(request)
            return ruled.model_copy(update={"provider": self.name})
        if selected not in request.choices:
            msg = f"mock selected {selected!r}, not in choices"
            raise ValueError(msg)
        probabilities = {
            c: (0.7 if c == selected else 0.3 / (len(request.choices) - 1)) for c in request.choices
        }
        return ChoiceResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            selected=selected,
            probabilities=normalise_probabilities(probabilities, request.choices),
        )

    async def score(self, request: ScoreRequest) -> ScoreResult:
        self._record(request)
        value = self.fixed_scores.get(request.decision_type)
        if value is None and self.seed is not None:
            value = self._rng.uniform(request.min_score, request.max_score)
        if value is None:
            ruled = await self._rules.score(request)
            return ruled.model_copy(update={"provider": self.name})
        return ScoreResult(provider=self.name, model=self.model, latency_ms=0.0, score=value)

    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult:
        self._record(request)
        value = self.fixed_probabilities.get(request.decision_type)
        if value is None and self.seed is not None:
            value = self._rng.random()
        if value is None:
            ruled = await self._rules.probability(request)
            return ruled.model_copy(update={"provider": self.name})
        return ProbabilityResult(
            provider=self.name, model=self.model, latency_ms=0.0, probability=value
        )

    def _record(self, request: ChoiceRequest | ScoreRequest | ProbabilityRequest) -> None:
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with

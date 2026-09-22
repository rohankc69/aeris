"""Decision provider abstraction.

Mission code depends only on ``DecisionProvider``. Whether an answer came from Jev via
OpenRouter, a rule table, or a scripted mock is invisible above this boundary; the provider
name and model id are carried in the result purely for auditing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

JsonDict = dict[str, object]


class DecisionProviderError(Exception):
    """Base class for provider failures. Always structured, never a bare exception."""

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class DecisionTimeoutError(DecisionProviderError):
    def __init__(self, provider: str, timeout_s: float) -> None:
        super().__init__(
            f"{provider} timed out after {timeout_s}s", provider=provider, retryable=True
        )


class MalformedResponseError(DecisionProviderError):
    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"{provider} returned a malformed response: {detail}", provider=provider)


class ProviderUnavailableError(DecisionProviderError):
    def __init__(self, provider: str, detail: str, *, retryable: bool = True) -> None:
        super().__init__(
            f"{provider} unavailable: {detail}", provider=provider, retryable=retryable
        )


class MissingCredentialsError(DecisionProviderError):
    def __init__(self, provider: str, variable: str) -> None:
        super().__init__(f"{provider} requires {variable}", provider=provider)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class _Request(_Frozen):
    decision_type: str = Field(description="Module name, e.g. drone_disposition")
    question: str
    state: JsonDict = Field(description="Compact structured application state")
    mission_id: str | None = None
    drone_id: str | None = None

    @property
    def state_hash(self) -> str:
        return hash_state(self.state)


class ChoiceRequest(_Request):
    choices: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _unique_choices(self) -> ChoiceRequest:
        if len(set(self.choices)) != len(self.choices):
            msg = "choices must be unique"
            raise ValueError(msg)
        return self


class ScoreRequest(_Request):
    min_score: float = 0.0
    max_score: float = 1.0


class ProbabilityRequest(_Request):
    """A yes/no question answered with P(yes)."""


class _Result(_Frozen):
    provider: str
    model: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    fallback_reason: str | None = Field(
        default=None, description="Set when a fallback provider produced this result"
    )
    raw: JsonDict | None = Field(default=None, description="Provider payload for the audit trail")


class ChoiceResult(_Result):
    selected: str
    probabilities: dict[str, float] = Field(default_factory=dict)

    @property
    def confidence(self) -> float | None:
        return self.probabilities.get(self.selected)


class ScoreResult(_Result):
    score: float


class ProbabilityResult(_Result):
    probability: float = Field(ge=0, le=1)


class DecisionProvider(Protocol):
    """Three bounded primitives. Implementations must never raise anything but
    ``DecisionProviderError`` subclasses for provider-side failures."""

    @property
    def name(self) -> str: ...

    async def choice(self, request: ChoiceRequest) -> ChoiceResult: ...

    async def score(self, request: ScoreRequest) -> ScoreResult: ...

    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult: ...


def hash_state(state: JsonDict) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def normalise_probabilities(
    probabilities: dict[str, float], choices: tuple[str, ...]
) -> dict[str, float]:
    """Clamp to the allowed choices, drop negatives, and scale to sum to 1."""
    cleaned = {c: max(0.0, float(probabilities.get(c, 0.0))) for c in choices}
    total = sum(cleaned.values())
    if total <= 0:
        return {c: 1.0 / len(choices) for c in choices}
    return {c: v / total for c, v in cleaned.items()}

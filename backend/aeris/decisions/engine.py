"""Decision engine: asks the configured provider a module's question, always obtains the
deterministic policy opinion alongside it, and produces a ``DecisionRecord``.

The engine does not act. The Mission Manager validates the proposal through the Safety
Governor and completes the record with the override flag and final action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aeris.clock import Clock
from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    DecisionProvider,
    DecisionProviderError,
    ProbabilityRequest,
    ProbabilityResult,
    ScoreRequest,
    ScoreResult,
)
from aeris.decisions.modules import (
    DetectionTriageInput,
    DroneDispositionInput,
    HumanReviewInput,
    ZonePriorityInput,
)
from aeris.decisions.rules import RuleBasedDecisionProvider
from aeris.domain.enums import Disposition, TriageDecision
from aeris.domain.models import DecisionRecord
from aeris.events.events import DecisionRequested

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChoiceOutcome:
    record: DecisionRecord
    result: ChoiceResult

    @property
    def selected(self) -> str:
        return self.result.selected


@dataclass(frozen=True)
class ScoreOutcome:
    record: DecisionRecord
    result: ScoreResult


@dataclass(frozen=True)
class ProbabilityOutcome:
    record: DecisionRecord
    result: ProbabilityResult


class DecisionEngine:
    def __init__(
        self,
        *,
        provider: DecisionProvider,
        policy: RuleBasedDecisionProvider,
        clock: Clock,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._clock = clock

    @property
    def provider_name(self) -> str:
        return self._provider.name

    # ------------------------------------------------------------------ modules

    async def drone_disposition(
        self, inputs: DroneDispositionInput, *, mission_id: str, drone_id: str
    ) -> ChoiceOutcome:
        request = inputs.to_request(mission_id=mission_id, drone_id=drone_id)
        outcome = await self._choice(request)
        Disposition(outcome.selected)  # bounded: raises if a provider leaked an unknown value
        return outcome

    async def detection_triage(
        self, inputs: DetectionTriageInput, *, mission_id: str, drone_id: str | None
    ) -> ChoiceOutcome:
        request = inputs.to_request(mission_id=mission_id, drone_id=drone_id)
        outcome = await self._choice(request)
        TriageDecision(outcome.selected)
        return outcome

    async def zone_priority(self, inputs: ZonePriorityInput, *, mission_id: str) -> ScoreOutcome:
        request = inputs.to_request(mission_id=mission_id)
        policy = await self._policy.score(request)
        result = await self._provider.score(request)
        record = self._record(
            request,
            provider_result=result,
            selected=f"{result.score:.3f}",
            policy_value=f"{policy.score:.3f}",
            policy_reason=_reason(policy),
        )
        return ScoreOutcome(record=record.model_copy(update={"score": result.score}), result=result)

    async def human_review_gate(
        self, inputs: HumanReviewInput, *, mission_id: str, drone_id: str | None = None
    ) -> ProbabilityOutcome:
        request = inputs.to_request(mission_id=mission_id, drone_id=drone_id)
        policy = await self._policy.probability(request)
        result = await self._provider.probability(request)
        record = self._record(
            request,
            provider_result=result,
            selected=f"{result.probability:.3f}",
            policy_value=f"{policy.probability:.3f}",
            policy_reason=_reason(policy),
        )
        return ProbabilityOutcome(
            record=record.model_copy(update={"score": result.probability}), result=result
        )

    # ------------------------------------------------------------------ internals

    async def _choice(self, request: ChoiceRequest) -> ChoiceOutcome:
        policy = await self._policy.choice(request)
        try:
            result = await self._provider.choice(request)
        except DecisionProviderError as exc:
            # A bare (non-resilient) provider failed: the policy answer is the decision.
            logger.warning("provider failed without fallback wrapper: %s", exc)
            result = policy.model_copy(update={"fallback_reason": type(exc).__name__})
        record = self._record(
            request,
            provider_result=result,
            selected=result.selected,
            policy_value=policy.selected,
            policy_reason=_reason(policy),
        ).model_copy(update={"probabilities": result.probabilities})
        return ChoiceOutcome(record=record, result=result)

    def _record(
        self,
        request: ChoiceRequest | ScoreRequest | ProbabilityRequest,
        *,
        provider_result: ChoiceResult | ScoreResult | ProbabilityResult,
        selected: str,
        policy_value: str,
        policy_reason: str | None,
    ) -> DecisionRecord:
        return DecisionRecord(
            timestamp=self._clock.now(),
            mission_id=request.mission_id or "",
            drone_id=request.drone_id,
            decision_type=request.decision_type,
            provider=provider_result.provider,
            model=provider_result.model,
            input_state=request.state,
            input_state_hash=request.state_hash,
            selected_value=selected,
            latency_ms=provider_result.latency_ms,
            input_tokens=provider_result.input_tokens,
            estimated_cost_usd=provider_result.estimated_cost_usd,
            policy_value=policy_value,
            policy_reason=policy_reason,
            final_action=selected,
            fallback_reason=provider_result.fallback_reason,
        )

    def requested_event(
        self, request: ChoiceRequest | ScoreRequest | ProbabilityRequest
    ) -> DecisionRequested:
        return DecisionRequested(
            mission_id=request.mission_id or "",
            decision_type=request.decision_type,
            drone_id=request.drone_id,
            provider=self._provider.name,
        )


def _reason(result: ChoiceResult | ScoreResult | ProbabilityResult) -> str | None:
    raw = result.raw or {}
    reason = raw.get("reason")
    return str(reason) if reason is not None else None

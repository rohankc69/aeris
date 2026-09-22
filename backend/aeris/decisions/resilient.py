"""Timeout, bounded retry, circuit breaker, and fallback around any provider.

    primary (e.g. Jev via OpenRouter)
        ↓ timeout / error / circuit open
    fallback (rules)

Results produced by the fallback carry ``fallback_reason`` so the DecisionRecord shows it.
An unavailable model API therefore degrades to the rule policy; it never raises into
mission logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aeris.clock import Clock
from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    DecisionProvider,
    DecisionProviderError,
    DecisionTimeoutError,
    ProbabilityRequest,
    ProbabilityResult,
    ScoreRequest,
    ScoreResult,
)

logger = logging.getLogger(__name__)

R = TypeVar("R", ChoiceResult, ScoreResult, ProbabilityResult)


class CircuitBreaker:
    """Opens after ``failure_threshold`` consecutive failures; half-opens after ``reset_s``."""

    def __init__(self, *, failure_threshold: int, reset_s: float, clock: Clock) -> None:
        self._threshold = failure_threshold
        self._reset_s = reset_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        elapsed = self._clock.now().timestamp() - self._opened_at
        return elapsed < self._reset_s  # after reset_s the breaker half-opens for one trial call

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock.now().timestamp()


class ResilientDecisionProvider:
    def __init__(
        self,
        *,
        primary: DecisionProvider,
        fallback: DecisionProvider,
        clock: Clock,
        timeout_s: float = 3.0,
        max_retries: int = 1,
        circuit_failure_threshold: int = 3,
        circuit_reset_s: float = 30.0,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold, reset_s=circuit_reset_s, clock=clock
        )

    @property
    def name(self) -> str:
        return self._primary.name

    @property
    def circuit_open(self) -> bool:
        return self._breaker.is_open

    async def choice(self, request: ChoiceRequest) -> ChoiceResult:
        return await self._call(request.decision_type, lambda p: p.choice(request))

    async def score(self, request: ScoreRequest) -> ScoreResult:
        return await self._call(request.decision_type, lambda p: p.score(request))

    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult:
        return await self._call(request.decision_type, lambda p: p.probability(request))

    async def _call(
        self, decision_type: str, invoke: Callable[[DecisionProvider], Awaitable[R]]
    ) -> R:
        if self._breaker.is_open:
            return await self._fallback_call(invoke, "circuit_open")

        reason = "unknown"
        for attempt in range(self._max_retries + 1):
            try:
                result = await asyncio.wait_for(invoke(self._primary), timeout=self._timeout_s)
            except TimeoutError:
                reason = "timeout"
                logger.warning(
                    "decision timeout",
                    extra={
                        "provider": self._primary.name,
                        "attempt": attempt,
                        "decision_type": decision_type,
                    },
                )
            except DecisionTimeoutError:
                reason = "timeout"
            except DecisionProviderError as exc:
                reason = type(exc).__name__
                logger.warning(
                    "decision provider error: %s",
                    exc,
                    extra={
                        "provider": exc.provider,
                        "attempt": attempt,
                        "decision_type": decision_type,
                    },
                )
                if not exc.retryable:
                    break
            else:
                self._breaker.record_success()
                return result
        self._breaker.record_failure()
        return await self._fallback_call(invoke, reason)

    async def _fallback_call(
        self, invoke: Callable[[DecisionProvider], Awaitable[R]], reason: str
    ) -> R:
        result = await invoke(self._fallback)
        return result.model_copy(update={"fallback_reason": reason})

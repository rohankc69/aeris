"""Build the configured decision provider. Mission code never imports concrete providers."""

from __future__ import annotations

from aeris.clock import Clock
from aeris.config import DecisionProviderKind, Settings
from aeris.decisions.base import DecisionProvider
from aeris.decisions.mock import MockDecisionProvider
from aeris.decisions.openrouter import OpenRouterJevProvider
from aeris.decisions.resilient import ResilientDecisionProvider
from aeris.decisions.rules import RuleBasedDecisionProvider


def build_offline_provider(kind: DecisionProviderKind, settings: Settings) -> DecisionProvider:
    if kind is DecisionProviderKind.MOCK:
        return MockDecisionProvider()
    if kind is DecisionProviderKind.RULES:
        return RuleBasedDecisionProvider(safety=settings.safety)
    msg = f"{kind} is not an offline provider"
    raise ValueError(msg)


def build_decision_provider(settings: Settings, *, clock: Clock) -> DecisionProvider:
    """Hosted providers are always wrapped with timeout/retry/circuit-breaker and an offline
    fallback. Offline providers are returned bare."""
    kind = settings.decision_provider
    if not kind.is_hosted:
        return build_offline_provider(kind, settings)

    key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
    primary = OpenRouterJevProvider(
        api_key=key,
        model=settings.jev_model,
        base_url=settings.decision.openrouter_base_url,
        timeout_s=settings.decision.timeout_s,
    )
    fallback = build_offline_provider(settings.decision_fallback, settings)
    return ResilientDecisionProvider(
        primary=primary,
        fallback=fallback,
        clock=clock,
        timeout_s=settings.decision.timeout_s,
        max_retries=settings.decision.max_retries,
        circuit_failure_threshold=settings.decision.circuit_breaker_failures,
        circuit_reset_s=settings.decision.circuit_breaker_reset_s,
    )

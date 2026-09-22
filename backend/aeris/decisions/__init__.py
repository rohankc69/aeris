"""Bounded AI decisions.

Mission code depends only on ``DecisionProvider``. Concrete providers: ``MockDecisionProvider``
(offline, scriptable), ``RuleBasedDecisionProvider`` (offline policy and fallback),
``OpenRouterJevProvider`` (Jev reached through OpenRouter as a gateway), wrapped by
``ResilientDecisionProvider`` for timeout/retry/circuit-breaker/fallback.
"""

from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    DecisionProvider,
    DecisionProviderError,
    DecisionTimeoutError,
    MalformedResponseError,
    MissingCredentialsError,
    ProbabilityRequest,
    ProbabilityResult,
    ProviderUnavailableError,
    ScoreRequest,
    ScoreResult,
    hash_state,
)
from aeris.decisions.factory import build_decision_provider
from aeris.decisions.mock import MockDecisionProvider
from aeris.decisions.openrouter import OpenRouterJevProvider
from aeris.decisions.resilient import ResilientDecisionProvider
from aeris.decisions.rules import RuleBasedDecisionProvider

__all__ = [
    "ChoiceRequest",
    "ChoiceResult",
    "DecisionProvider",
    "DecisionProviderError",
    "DecisionTimeoutError",
    "MalformedResponseError",
    "MissingCredentialsError",
    "MockDecisionProvider",
    "OpenRouterJevProvider",
    "ProbabilityRequest",
    "ProbabilityResult",
    "ProviderUnavailableError",
    "ResilientDecisionProvider",
    "RuleBasedDecisionProvider",
    "ScoreRequest",
    "ScoreResult",
    "build_decision_provider",
    "hash_state",
]

"""Jev via OpenRouter.

OpenRouter is only the model gateway: this provider formats a bounded question into a chat
completion request addressed to the configured Jev model id, and parses a strict JSON reply.
The decision is Jev's. Everything provider-specific stays in this file.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    DecisionTimeoutError,
    JsonDict,
    MalformedResponseError,
    MissingCredentialsError,
    ProbabilityRequest,
    ProbabilityResult,
    ProviderUnavailableError,
    ScoreRequest,
    ScoreResult,
    normalise_probabilities,
)

PROVIDER_NAME = "openrouter"

SYSTEM_PROMPT = (
    "You are Jev, a bounded decision model inside AERIS, a civilian search-and-rescue drone "
    "coordination system. You answer one narrow question at a time from compact structured "
    "state. You never control aircraft, generate waypoints, or override safety rules; "
    "deterministic code and a human operator retain final authority. A detection is only ever "
    "a candidate; only a human confirms a survivor. Reply with a single JSON object and nothing "
    "else."
)


class OpenRouterJevProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_s: float = 3.0,
        client: httpx.AsyncClient | None = None,
        app_name: str = "AERIS",
    ) -> None:
        if not api_key:
            raise MissingCredentialsError(PROVIDER_NAME, "OPENROUTER_API_KEY")
        self._model = model
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        # Headers are applied to injected clients too, so tests exercise the real auth path.
        self._client.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/rohankc69/aeris",
                "X-Title": app_name,
            }
        )

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ primitives

    async def choice(self, request: ChoiceRequest) -> ChoiceResult:
        schema = {
            "selected": "one of the allowed choices",
            "probabilities": {c: "0..1" for c in request.choices},
            "reason": "one short sentence",
        }
        user = _user_message(
            request.decision_type, request.question, request.state, schema, choices=request.choices
        )
        payload, meta = await self._complete(user)
        selected = payload.get("selected")
        if not isinstance(selected, str) or selected not in request.choices:
            raise MalformedResponseError(
                PROVIDER_NAME, f"selected={selected!r} not in {request.choices}"
            )
        probs_raw = payload.get("probabilities", {})
        if not isinstance(probs_raw, dict):
            raise MalformedResponseError(PROVIDER_NAME, "probabilities must be an object")
        try:
            probabilities = {str(k): float(v) for k, v in probs_raw.items()}
        except (TypeError, ValueError) as exc:
            raise MalformedResponseError(PROVIDER_NAME, "non-numeric probability") from exc
        if not probabilities:
            probabilities = {selected: 1.0}
        return ChoiceResult(
            selected=selected,
            probabilities=normalise_probabilities(probabilities, request.choices),
            raw={"reason": payload.get("reason")},
            **meta,
        )

    async def score(self, request: ScoreRequest) -> ScoreResult:
        schema = {
            "score": f"number in [{request.min_score}, {request.max_score}]",
            "reason": "one short sentence",
        }
        user = _user_message(request.decision_type, request.question, request.state, schema)
        payload, meta = await self._complete(user)
        value = _as_float(payload.get("score"), "score")
        if not request.min_score <= value <= request.max_score:
            raise MalformedResponseError(PROVIDER_NAME, f"score {value} outside bounds")
        return ScoreResult(score=value, raw={"reason": payload.get("reason")}, **meta)

    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult:
        schema = {
            "probability": "number in [0, 1] that the answer is yes",
            "reason": "one short sentence",
        }
        user = _user_message(request.decision_type, request.question, request.state, schema)
        payload, meta = await self._complete(user)
        value = _as_float(payload.get("probability"), "probability")
        if not 0 <= value <= 1:
            raise MalformedResponseError(PROVIDER_NAME, f"probability {value} outside [0, 1]")
        return ProbabilityResult(probability=value, raw={"reason": payload.get("reason")}, **meta)

    # ------------------------------------------------------------------ transport

    async def _complete(self, user_message: str) -> tuple[JsonDict, dict[str, Any]]:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "usage": {"include": True},
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(
                "/chat/completions", json=body, timeout=self._timeout_s
            )
        except httpx.TimeoutException as exc:
            raise DecisionTimeoutError(PROVIDER_NAME, self._timeout_s) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(PROVIDER_NAME, str(exc)) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        if response.status_code in {401, 403}:
            raise ProviderUnavailableError(
                PROVIDER_NAME, f"HTTP {response.status_code} (credentials)", retryable=False
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailableError(PROVIDER_NAME, f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                PROVIDER_NAME,
                f"HTTP {response.status_code}: {response.text[:200]}",
                retryable=False,
            )

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MalformedResponseError(PROVIDER_NAME, "no message content") from exc
        payload = _parse_json_object(content)
        usage = data.get("usage") or {}
        meta: dict[str, Any] = {
            "provider": PROVIDER_NAME,
            "model": data.get("model") or self._model,
            "latency_ms": latency_ms,
            "input_tokens": _opt_int(usage.get("prompt_tokens")),
            "output_tokens": _opt_int(usage.get("completion_tokens")),
            "estimated_cost_usd": _opt_float(usage.get("cost")),
        }
        return payload, meta


def _user_message(
    decision_type: str,
    question: str,
    state: JsonDict,
    schema: Mapping[str, object],
    *,
    choices: tuple[str, ...] | None = None,
) -> str:
    parts = [
        f"Decision type: {decision_type}",
        f"Question: {question}",
        "State:",
        json.dumps(state, sort_keys=True, default=str),
    ]
    if choices:
        parts.append("Allowed choices: " + ", ".join(choices))
    parts.append("Respond with JSON matching: " + json.dumps(schema))
    return "\n".join(parts)


def _parse_json_object(content: object) -> JsonDict:
    if not isinstance(content, str):
        raise MalformedResponseError(PROVIDER_NAME, "content is not a string")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise MalformedResponseError(PROVIDER_NAME, "content is not JSON") from exc
    if not isinstance(parsed, dict):
        raise MalformedResponseError(PROVIDER_NAME, "content is not a JSON object")
    return parsed


def _as_float(value: object, field: str) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MalformedResponseError(PROVIDER_NAME, f"{field}={value!r} is not a number") from exc


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None

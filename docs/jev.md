# Jev in AERIS

AERIS uses TypeSafe AI's **Jev** as a bounded decision model. Jev answers narrow typed
questions from compact structured state. It never controls aircraft.

## Access path

Jev is reached through **OpenRouter**, a model gateway. OpenRouter routes the request to the
configured Jev model id and returns Jev's reply. **OpenRouter is only the gateway. Jev is the
decision model.**

```
World State
   ↓
DecisionProvider          ← the only thing mission code depends on
   ↓
OpenRouterJevProvider     ← formats the bounded question, parses strict JSON
   ↓
OpenRouter                ← gateway; routes to the configured model id
   ↓
Jev                       ← makes the bounded decision
   ↓
Typed bounded decision
   ↓
Mission Planner → Safety Governor → Fleet Adapter
```

Mission code never sees which provider answered. `OpenRouterJevProvider`,
`RuleBasedDecisionProvider` and `MockDecisionProvider` implement the same three primitives:

```python
class DecisionProvider(Protocol):
    async def choice(self, request: ChoiceRequest) -> ChoiceResult: ...
    async def score(self, request: ScoreRequest) -> ScoreResult: ...
    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult: ...
```

A direct TypeSafe API provider is **planned**; it would sit behind the same abstraction.

## Configuration

```env
AERIS_DECISION_PROVIDER=openrouter      # mock | rules | openrouter
AERIS_DECISION_FALLBACK=rules           # must be offline
OPENROUTER_API_KEY=                     # never committed
JEV_MODEL=typesafe/jev-latest           # configurable; surfaced in every DecisionRecord
AERIS_DECISION__TIMEOUT_S=3
AERIS_DECISION__MAX_RETRIES=1
AERIS_DECISION__CIRCUIT_BREAKER_FAILURES=3
AERIS_DECISION__CIRCUIT_BREAKER_RESET_S=30
AERIS_DECISION__DISPOSITION_INTERVAL_S=15
```

Offline development uses `mock` (scriptable, deterministic, the CI default) or `rules`
(hand-written policy). No API key is needed for either. Hosted calls are never part of CI.

## What Jev is asked

Four narrow modules. There is deliberately no "what should the fleet do?" question.

| Module | Primitive | Bounded answer |
|---|---|---|
| `drone_disposition` | choice | `CONTINUE_SEARCH · RETURN_TO_BASE · HANDOFF_ZONE · HOLD · REQUEST_HUMAN_REVIEW` |
| `detection_triage` | choice | `IGNORE · RECHECK · INVESTIGATE · HUMAN_REVIEW · POSSIBLE_SURVIVOR` |
| `zone_priority` | score | `[0, 1]` |
| `human_review_gate` | probability | P(human intervention needed) |

`POSSIBLE_SURVIVOR` is the strongest thing Jev can say. It never means a person was found;
only an operator action moves a mission to `PERSON_LOCATED`.

## What Jev receives

A compact JSON state per question, for example for `drone_disposition`:

```json
{
  "battery_percent": 31,
  "estimated_return_battery_percent": 21,
  "distance_to_base_m": 1800,
  "zone_completion": 0.72,
  "connection_quality": 0.84,
  "link_state": "CONNECTED",
  "telemetry_age_s": 0.4,
  "active_detection": false
}
```

The system prompt states Jev's role and limits (no flight control, no waypoints, no safety
override, detections are candidates only) and requires a single JSON object as reply. The
request uses `response_format: json_object`, `temperature: 0` and asks OpenRouter to include
usage so tokens and cost land in the record.

## What Jev never decides

Motors, attitude, raw flight commands, waypoint generation, collision avoidance, geofencing,
emergency failsafes, hard battery limits, lost-link behaviour, and whether a person has been
found. These are deterministic code or human decisions.

## Failure handling

```
OpenRouter/Jev
    ↓ timeout · HTTP error · malformed JSON · circuit open
RuleBasedDecisionProvider
    ↓
Safety Governor
```

`ResilientDecisionProvider` wraps the hosted provider with a per-call timeout, a bounded
retry (retryable errors only), a circuit breaker, and the offline fallback. The result's
`fallback_reason` (`timeout`, `ProviderUnavailableError`, `MalformedResponseError`,
`circuit_open`, ...) is written into the `DecisionRecord`. Safety behaviour does not depend on
any provider, so an unavailable model API never prevents a mandatory return.

## Audit

Every call produces a `DecisionRecord`:

```
provider: openrouter
model: typesafe/jev-latest
decision_type: drone_disposition
selected_value: RETURN_TO_BASE
probabilities: {RETURN_TO_BASE: 0.79, HANDOFF_ZONE: 0.12, ...}
latency_ms: 143
input_tokens / estimated_cost_usd
input_state / input_state_hash
policy_value: RETURN_TO_BASE   policy_reason: at mandatory return threshold
fallback_reason: null
safety_override: false
final_action: RETURN_TO_BASE
```

The dashboard's Decision Inspector renders these, and `GET /api/v1/missions/{id}/decisions`
returns them. See `docs/decisions.md`.

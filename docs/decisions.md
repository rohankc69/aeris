# Decisions

How a bounded decision moves through AERIS, and how to read the record it leaves behind.

## Pipeline

```mermaid
sequenceDiagram
    participant MM as MissionManager
    participant DE as DecisionEngine
    participant POL as RuleBasedDecisionProvider (policy)
    participant DP as DecisionProvider (configured)
    participant SG as SafetyGovernor
    participant WS as WorldState

    MM->>DE: drone_disposition(input)
    DE->>POL: choice(request)          # deterministic second opinion, always
    DE->>DP: choice(request)           # mock | rules | Jev via OpenRouter (+fallback)
    DE-->>MM: ChoiceOutcome(record, selected)
    MM->>SG: validate_disposition(selected, drone, snapshot)
    SG-->>MM: verdict(action, event?)
    MM->>WS: record_decision(record + override + final_action)
    MM->>WS: record_safety_event(event)   # only on override
    MM->>MM: apply(action)
```

Order inside one Mission Manager tick:

1. telemetry → world state, link states derived from age
2. coverage advanced from position against each plan
3. zones released from drones that cannot work them
4. **dispositions**: each due searching drone is asked; each proposal validated
5. **safety enforcement**: rules run on every drone whether or not AI is configured
6. assignment, settling, completion

Step 5 exists so that a drone that was not asked this tick is still recalled the moment a
rule fires. The AI cadence is `AERIS_DECISION__DISPOSITION_INTERVAL_S` (default 15 s).

## Reading a DecisionRecord

| Field | Answers |
|---|---|
| `input_state`, `input_state_hash` | What did the model see? |
| `selected_value`, `probabilities` / `score` | What did it decide, how certain was it? |
| `provider`, `model`, `latency_ms`, `input_tokens`, `estimated_cost_usd` | Who answered, how fast, what did it cost? |
| `policy_value`, `policy_reason` | What did the deterministic policy say? |
| `fallback_reason` | Did the hosted provider fail and the fallback answer instead? |
| `safety_override`, `safety_event_id` | Did the Safety Governor replace the answer? Which rule? |
| `final_action` | What was executed? |
| `outcome` | Reserved for later linkage to what happened next |

`provider` is the provider that actually produced the answer. After a fallback it reads
`rules` with `fallback_reason` set, not `openrouter`.

## Safety Governor rules (Phase 2)

| Rule | Fires when | Required action |
|---|---|---|
| `critical_battery` | battery ≤ `critical_battery_percent` | RETURN_TO_BASE |
| `mandatory_return_battery` | battery ≤ `min_return_battery_percent` | RETURN_TO_BASE |
| `return_margin` | battery ≤ estimated return cost + `return_battery_margin_percent` | RETURN_TO_BASE |
| `link_lost` | link state LOST | RETURN_TO_BASE (preconfigured lost-link behaviour) |
| `mission_not_active` | mission paused/aborted while searching | HOLD |
| `telemetry_missing` | no telemetry ever received | HOLD |

A proposal that already satisfies the required action (or is more conservative than a HOLD
requirement) passes. Anything else is replaced and produces a `SafetyEvent` whose
`proposed_action` is `<provider>:<proposal>`. Jev may therefore recommend an *earlier* return
but can never delay a mandatory one.

## Inspecting decisions

- Dashboard → bottom panel → **Decisions** tab. Click a row for input, probabilities, policy
  opinion, override rule and final action. Selecting a drone filters the list.
- `GET /api/v1/missions/{id}/decisions` and `GET /api/v1/missions/{id}/safety-events`.
- `aeris sim run --scenario low_battery` prints decision and override counts in its summary.

## Evaluation

`aeris eval` (Phase 6) replays scenarios through different providers and compares these
records. Because the record format is provider-independent, Jev, rules, and future models
are compared without changing mission logic.

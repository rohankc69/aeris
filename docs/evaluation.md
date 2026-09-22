# Evaluation

AERIS does not merely call Jev; it evaluates Jev. The same scenario, seed and mission logic
run under different decision providers, and their `DecisionRecord`s, safety events and metrics
are stored as JSON so contributors can compare Jev, the rule policy and future models without
changing mission code.

## Commands

```bash
cd backend

# one scenario, one provider -> evals/out/<scenario>_<provider>_<timestamp>.json
uv run aeris eval run --scenario low_battery --provider rules
uv run aeris eval run --scenario low_battery --provider openrouter    # needs OPENROUTER_API_KEY

# every scenario under several providers, plus a comparison.json
uv run aeris eval matrix --scenarios all --providers mock,rules

# compare stored results side by side
uv run aeris eval compare evals/out/low_battery_rules_*.json evals/out/low_battery_openrouter_*.json

# replay the recorded decision inputs of one run through another provider, offline
uv run aeris eval replay evals/out/low_battery_openrouter_*.json --provider rules
```

Seeds are explicit (`--seed`), the simulation clock is deterministic, and the mock provider is
deterministic, so an evaluation is reproducible from its result file alone.

## What is compared

| metric | source |
|---|---|
| final mission status, mission time, time to survivor | `SimulationSummary` |
| coverage fraction, zones completed, reassignments | `SimulationSummary` |
| decisions by provider and type | `MissionMetrics` |
| decision latency (mean, histogram), input tokens, estimated cost | `DecisionRecord`s via `MissionMetrics` |
| safety overrides by rule; AI proposals overridden | `SafetyEvent`s / `DecisionCompleted` |
| human review requests, candidates escalated | events |
| distance travelled, battery consumed per drone | telemetry deltas |
| decision agreement between providers on identical inputs | `input_state_hash` join across result files |

`aeris eval replay` answers a narrower question: given exactly what one provider saw, what
would another provider have said? Disagreements are listed with both answers and the input
hash so they can be inspected in the Decision Inspector or the result JSON.

## Result file

`EvalResult` (schema version 1): scenario, provider, model, seed, summary (with metrics),
every `DecisionRecord`, every `SafetyEvent`. Files are ignored by git (`evals/out/`).

## Observability underneath

`aeris.telemetry` provides structured logging (structlog; JSON in production) with
`mission_id`, `drone_id`, `event_type`, `decision_id`, `provider`, `model`, `latency_ms`
lifted from log context, and an in-process `MetricsRegistry` (counters, gauges, histograms)
with a Prometheus text renderer. Per mission:

- `GET /api/v1/missions/{id}/metrics` returns the summary
- `GET /api/v1/missions/{id}/metrics/prometheus` returns exposition text

A process-wide Prometheus exporter is a later addition; nothing in mission code would change.

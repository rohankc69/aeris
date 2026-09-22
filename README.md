# AERIS

**Autonomous Emergency Response & Intelligent Search**

AERIS is an open-source decision and coordination layer for multi-drone search-and-rescue.
It maintains a shared view of a mission, coordinates search assignments, reacts to detections
and vehicle failures, and uses bounded AI decisions while deterministic safety systems retain
final authority.

> Status: **MVP demo complete (Phases 0 to 3 and 5).** `aeris sim run --scenario forest_search`
> plays the full mission: three simulated drones, a degrading battery, a weak-then-strong
> detection, automatic reassignment, bounded AI triage, a deterministic Safety Governor, and a
> human confirming the survivor. No ROS, PX4, database or AI key required. See [Roadmap](#roadmap).

---

## Why AERIS exists

Wilderness and disaster search is a coordination problem. Several aircraft, one missing
person, a large area, limited battery, imperfect sensors, and an operator who cannot watch
every screen at once. Existing tools either fly one drone well or leave coordination to
humans with a radio.

AERIS asks a narrower question than "let an AI fly the fleet": *what small decisions, if made
well and continuously, produce a coordinated rescue mission?* Which zone next. Whether this
drone should keep searching or come home. Whether a weak thermal blob deserves a second
aircraft. When a human needs to look.

The answer combines three kinds of intelligence, each kept in its lane:

| | Used for |
|---|---|
| **Deterministic code** | battery minimums, geofences, timeouts, lost-link behavior, emergency stop |
| **Algorithms** | zone partitioning, coverage paths, task assignment, distance math |
| **Bounded AI (Jev)** | detection triage, zone priority, uncertain disposition, review escalation |

AI decides bounded mission questions. Algorithms plan. PX4 flies. Safety code has final
authority. Humans can always intervene.

## What it demonstrates

The MVP is one reproducible simulated mission:

1. Operator defines a search region. AERIS divides it into zones.
2. Three simulated drones are assigned and begin lawnmower search patterns.
3. Live positions and coverage appear on the dashboard.
4. One drone approaches its safe battery limit. AERIS returns it to base and reassigns its
   unfinished zone.
5. Another drone reports a possible survivor detection. Jev triages it. A drone investigates.
6. AERIS asks the operator to confirm. The operator confirms. Mission status becomes
   `PERSON_LOCATED`.
7. The decision inspector shows exactly why every AI-assisted action happened.

## Architecture

```
Human Operator
      │
      ▼
Mission Manager
      │
      ▼
World State ──────────────┐
      │                   │
      ▼                   ▼
Jev Decision         Deterministic
Engine               Policy Engine
      │                   │
      └────────┬──────────┘
               ▼
        Mission Planner
               │
               ▼
        Safety Governor      ← final authority, deterministic
               │
               ▼
         Drone Adapter       ← Fake (default) or PX4
               │
               ▼
        ROS 2 / PX4 / Gazebo
```

AERIS is not a flight controller. PX4 handles flight. Jev never touches flight controls:

```
WORLD STATE → JEV → TYPED DECISION → MISSION LOGIC → SAFETY GOVERNOR → COMMAND
```

Full detail in [docs/architecture.md](docs/architecture.md).

## Features (planned for MVP)

- Shared world state with explicit telemetry timestamps and staleness detection
- Grid partitioning and deterministic boustrophedon coverage paths
- Weighted greedy assignment with pluggable strategies
- Dynamic reassignment when a drone returns, investigates, or drops off the link
- Narrow typed AI decisions: drone disposition, detection triage, zone priority, human-review gate
- Mock, rule-based, and Jev decision providers behind one interface, with timeout, retry,
  circuit breaker, and fallback
- Deterministic Safety Governor with audited overrides
- Full `DecisionRecord` audit trail and a decision inspector UI
- Scenario-driven simulation that runs without ROS or PX4
- Evaluation harness to compare Jev against rule-based policy on identical scenarios
- Operations-console dashboard with live map (MapLibre GL) and WebSocket telemetry

## Screenshots

_Placeholder. Dashboard screenshots will land with Phase 1._

## Quick start (no Docker, no ROS, no API key)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, pnpm.

```bash
git clone https://github.com/rohankc69/aeris.git
cd aeris

# backend
cd backend
uv sync --all-extras
uv run pytest
uv run uvicorn aeris.api.app:create_app --factory --reload    # http://localhost:8000

# dashboard, in another terminal
cd dashboard
pnpm install
pnpm dev                                                       # http://localhost:3000
```

Open the dashboard, pick a scenario, set a time scale (simulated seconds per real second),
click **New mission**, then **Start**. API docs are at `http://localhost:8000/docs`.

Defaults are `AERIS_FLEET_PROVIDER=fake` and `AERIS_DECISION_PROVIDER=mock`, so everything
runs in-process with no external services.

## Simulation quick start

```bash
cd backend
uv run aeris sim list
uv run aeris sim run --scenario basic_search
uv run aeris sim run --scenario lost_connection --quiet --output out.json
```

The run prints per-minute fleet status and ends with a JSON summary (final status, coverage,
zones completed, reassignments, event counts).

Scenarios are YAML files under `sim/scenarios/`. They define the fleet, search area, base,
missing person, and a timeline of events (battery anomalies, thermal candidates, link loss).
See [docs/simulation.md](docs/simulation.md).

## Jev configuration

AERIS uses TypeSafe AI's Jev as a bounded decision engine, reached through OpenRouter as the
model gateway. **OpenRouter only routes the request; Jev is the decision model.**

```
World State → DecisionProvider → OpenRouter → Jev → typed bounded decision
```

```bash
cp .env.example .env
# edit .env
AERIS_DECISION_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key
JEV_MODEL=typesafe/jev-latest
```

A direct TypeSafe provider can sit behind the same `DecisionProvider` interface. The provider
and model id appear in every `DecisionRecord` and in telemetry. Never commit `.env`. See
[docs/jev.md](docs/jev.md).

## Offline mode

Offline is the default. `mock` gives deterministic, scriptable answers for tests and demos;
`rules` gives a hand-written heuristic baseline and is also the fallback when Jev times out
or errors. CI never calls Jev.

## Docker (optional)

Docker is not needed for local development. It provides:

- `docker compose up` for containerised backend and dashboard (fake fleet, mock AI)
- `docker compose --profile postgres up -d postgres` for an optional durable PostgreSQL/PostGIS store
- `docker compose --profile px4 up` for headless ROS 2 Jazzy + PX4 SITL + Gazebo with three
  vehicles and the `aeris_px4_bridge` node; see [docs/px4_bridge.md](docs/px4_bridge.md)

## Safety philosophy

- The Safety Governor runs last and can block or replace any action from any source.
- Hard limits (battery, geofence, altitude, separation, timeouts) are deterministic code with
  named thresholds, never model outputs.
- Missing or stale telemetry is treated as unsafe, not normal.
- Lost-link behavior is preconfigured and deterministic.
- AI never declares a survivor found. A human confirms every candidate.
- Simulation is the default. Real aircraft would require an explicit switch and are outside
  MVP scope.

**AERIS is a civilian search-and-rescue research project.** It contains no weapons, targeting,
fire-control, payload-release, attack, or engagement functionality, and contributions adding
such functionality will be rejected. Person detection exists solely to locate people who may
need rescue. See [docs/safety.md](docs/safety.md).

## Roadmap

| Phase | Goal |
|---|---|
| 0 | Foundation: docs, skeleton, tooling ✅ |
| 1 | Local simulation: three fake drones search a region, REST + WebSocket, basic dashboard ✅ |
| 2 | Decision providers (Mock, Rules, Jev via OpenRouter), DecisionRecord, decision inspector, fallback ✅ |
| 3 | Safety Governor extended (geofence, altitude, separation, plan validation, restricted regions), e-stop, dynamic reassignment ✅ |
| 4 | ROS 2 / PX4 SITL / Gazebo with three vehicles ← **next** |
| 5 | Detection simulation, complete forest-search scenario ✅ |
| 6 | Evaluation harness, docs, screenshots, contributor experience |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Development conventions for humans and AI assistants
are in [CLAUDE.md](CLAUDE.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

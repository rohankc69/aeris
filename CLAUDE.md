# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.
Read this before touching code. Update it whenever the architecture changes materially.

## Project identity

**AERIS** (Autonomous Emergency Response & Intelligent Search) is an open-source
autonomous coordination platform for **civilian multi-drone search-and-rescue**.

AERIS is a *decision and coordination layer*, not a flight controller. It sits above the
autopilot (PX4), maintains one shared world state for a mission, divides a search region into
zones, assigns drones, reacts to detections and vehicle failures, and asks a bounded AI model
(TypeSafe AI's Jev) narrow typed questions. Deterministic safety code always has final authority,
and a human operator can always intervene.

## Core architectural principle

```
AI decides bounded mission questions.
Algorithms plan.
PX4 flies.
Safety code has final authority.
Humans can always intervene.
```

Every component belongs to exactly one of three kinds of intelligence:

| Kind | Used for | Examples in this repo |
|---|---|---|
| **Deterministic code** | hard limits and emergency behavior | battery minimums, geofence, telemetry timeout, lost-link behavior, waypoint validation, emergency stop |
| **Algorithms** | planning and optimization | grid partitioning, boustrophedon coverage paths, greedy assignment, distance math |
| **Jev (bounded AI)** | fuzzy judgments where hand-written rules become brittle | detection triage, zone priority scoring, uncertain drone disposition, human-review escalation |

If you are unsure which kind a new behavior belongs to, it is **not** Jev. Jev is only ever
asked narrow, typed questions and its answer is only ever a *proposal*.

## Non-negotiable rules

1. **Do not put Jev directly in the flight-control loop.** Jev never produces waypoints,
   velocities, attitudes, or raw commands. Pipeline is always
   `WorldState → Jev → TypedDecision → MissionLogic → SafetyGovernor → Command`.
2. **Do not let AI bypass the Safety Governor.** Every proposed action, whatever its source,
   passes `SafetyGovernor.validate()` before reaching a `FleetAdapter`. Overrides emit a
   `SafetyEvent`.
3. **Do not implement weaponization or offensive targeting.** No weapons, fire control, harmful
   payload release, attack behavior, combat engagement logic, military target classification,
   or autonomous decisions to harm people. Person detection exists solely to locate people who
   may need rescue. Reject and flag any request to add such functionality.
4. **Keep the domain model independent of ROS/PX4.** Nothing under `backend/aeris/domain/`
   imports ROS, PX4, or any transport library. Translation happens only inside
   `backend/aeris/fleet/px4/` and `ros_ws/`.
5. **All AI functionality must have an offline/mock path.** The whole project (tests, demos,
   CI, dashboard) runs with `AERIS_DECISION_PROVIDER=mock` and no API key.
6. **Every Jev decision must be observable and auditable.** Every call produces a
   `DecisionRecord` capturing input state hash, provider, model, probabilities, policy opinion,
   safety override, and final action.
7. **Prefer narrow typed Jev decisions to giant prompts.** One decision module per question
   (`drone_disposition`, `detection_triage`, `zone_priority`, `human_review_gate`). Never
   "what should the fleet do?".
8. **Do not expose secrets.** Credentials come only from environment variables
   (`TYPESAFE_API_KEY`, `JEV_MODEL`, `DATABASE_URL`). Never commit them; `.env.example`
   contains placeholders only.
9. **Add tests for safety-critical logic.** Any change to `safety/`, battery/return logic,
   lost-link behavior, geofence, or reassignment requires tests in the same PR.
10. **Maintain simulation-first development.** Default config is `AERIS_FLEET_PROVIDER=fake`.
    A real-aircraft adapter, if ever added, must require an explicit config switch and is out
    of scope for the MVP.

Additional rules that follow from the above:

- A Jev decision must **never** declare that a human has definitively been found. Use
  *candidate detection*, *possible survivor*, *human confirmation required*. Only an
  `OperatorAction` moves a mission to `PERSON_LOCATED`.
- Missing or stale information is **never** treated as "normal". A drone whose telemetry is
  older than the configured timeout is `STALE`/`LOST` until proven otherwise.
- Lost-link behavior is preconfigured deterministic code. Jev is never consulted for it.
- Search paths are deterministic and unit-tested. Jev never generates waypoints.

## Repository layout

```
aeris/
├── CLAUDE.md                 ← you are here
├── README.md, LICENSE (Apache-2.0), CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
├── .env.example
├── docker-compose.yml
├── docs/                     architecture, decisions, safety, jev, simulation, development
├── backend/
│   ├── pyproject.toml        Python 3.12+, managed with uv
│   ├── aeris/
│   │   ├── domain/           Pydantic models + enums. No I/O, no ROS, no framework imports.
│   │   ├── events/           Domain events + in-process async EventBus abstraction
│   │   ├── world/            WorldStateService: authoritative mission snapshot, staleness
│   │   ├── decisions/        DecisionProvider protocol, Jev/Mock/RuleBased providers,
│   │   │                     decision modules, DecisionRecord, fallback/circuit breaker
│   │   ├── planning/         grid partition, coverage paths, AssignmentStrategy, MissionPlanner
│   │   ├── safety/           SafetyGovernor + deterministic rules + SafetyEvent
│   │   ├── fleet/            FleetAdapter protocol, FakeFleetAdapter, px4/ adapter
│   │   ├── mission/          MissionManager: orchestrates the control loop
│   │   ├── api/              FastAPI routers, WebSocket, schemas
│   │   ├── persistence/      SQLAlchemy models/repositories (PostgreSQL + PostGIS)
│   │   ├── telemetry/        structured logging + metrics registry
│   │   └── config.py         pydantic-settings; all env vars defined here
│   └── tests/                unit/, integration/, live/ (live needs --run-live flag)
├── dashboard/                Next.js + React + TypeScript + MapLibre GL
├── ros_ws/src/aeris_px4_bridge/   ROS 2 Jazzy package; PX4 ↔ AERIS translation only
├── sim/                      scenarios/ (YAML), worlds/, fixtures/, scripts/
├── evals/                    evaluation harness outputs and configs
└── scripts/                  developer helper scripts
```

Directories are created when they become necessary, not to satisfy the diagram.
See `docs/architecture.md` for how the pieces interact.

## Coding philosophy

Prefer: simple modules · typed interfaces (`Protocol`s) · dependency injection via constructor
args · explicit state passed as snapshots · testability · deterministic safety rules · small
commits · clear documentation.

Avoid: global mutable state · giant service classes · magic thresholds (all thresholds live in
`config.py` or scenario files with names) · hardcoded credentials · direct infrastructure
coupling in domain/mission logic · premature microservices · unnecessary abstractions.

Conventions:

- Python 3.12+, fully typed, `mypy --strict` clean, Ruff formatted and linted.
- Pydantic v2 models for domain objects; frozen where practical.
- `async` for I/O boundaries (adapters, providers, API); pure sync functions for algorithms.
- Every threshold has a name and a unit in its identifier (`min_return_battery_percent`,
  `telemetry_stale_after_s`).
- Label behavior honestly in docstrings and docs: **real**, **simulated**, **mocked**, or
  **planned**. Never silently fake functionality.
- Conventional commits: `feat(safety): ...`, `fix(planning): ...`, `docs: ...`, `test: ...`.
- Do not claim something works unless it has been run or tested in this session.

## Commands

All backend commands run from `backend/`; all dashboard commands from `dashboard/`.
(Exact commands are kept current as tooling lands. Items marked *planned* do not exist yet.)

### Backend

```bash
cd backend
uv sync --all-extras                 # install deps into .venv
uv run uvicorn aeris.api.app:create_app --factory --reload   # dev server on :8000
uv run aeris --help                  # CLI (mission, sim, eval subcommands)
```

### Tests

```bash
cd backend
uv run pytest                        # unit + integration, mock provider, fake fleet
uv run pytest -m "not integration"   # unit only
uv run pytest --run-live             # live Jev tests; requires TYPESAFE_API_KEY (never in CI)
```

### Lint / type-check / format

```bash
cd backend
uv run ruff format .
uv run ruff check . --fix
uv run mypy aeris
pre-commit run --all-files           # from repo root, after `pre-commit install`
```

### Frontend

```bash
cd dashboard
pnpm install
pnpm dev                             # Next.js on :3000, expects backend on :8000
pnpm test
pnpm lint && pnpm typecheck
```

### Simulation (no ROS/PX4 required)

```bash
cd backend
uv run aeris sim run --scenario forest_search           # full local mission, fake fleet
uv run aeris sim run --scenario low_battery --seed 42
uv run aeris sim list
```

### PX4 bridge (Phase 4, Docker-hosted, *planned*)

```bash
docker compose --profile px4 up      # ROS 2 Jazzy + PX4 SITL + Gazebo + 3 vehicles
# then: AERIS_FLEET_PROVIDER=px4 uv run uvicorn ...
```

### Evaluation (Phase 6, *planned*)

```bash
cd backend
uv run aeris eval --scenario low_battery --provider rules
uv run aeris eval --scenario low_battery --provider jev
uv run aeris eval compare evals/out/*.json
```

### Docker environment (optional)

Docker is never required for local development. Everything through the full MVP demo runs
natively with `uv` and `pnpm`.

```bash
docker compose up -d postgres        # optional durable store; omit DATABASE_URL to stay in-memory
docker compose --profile px4 up      # Phase 4 (planned): headless ROS 2 / PX4 SITL / Gazebo
```

## Configuration

Environment variables (see `.env.example`):

```
AERIS_ENV=development
AERIS_FLEET_PROVIDER=fake            # fake | px4
AERIS_DECISION_PROVIDER=mock         # mock | rules | jev
AERIS_DECISION_FALLBACK=rules
TYPESAFE_API_KEY=                    # only needed for jev
JEV_MODEL=                           # surfaced in every DecisionRecord and telemetry
DATABASE_URL=postgresql+asyncpg://aeris:aeris@localhost:5432/aeris
```

## Development phases

| Phase | Goal | Status |
|---|---|---|
| 0 | CLAUDE.md, README, architecture doc, skeleton, dev environment | done |
| 1 | Local simulation without PX4: domain, WorldState, FakeFleetAdapter, grid, assignment, events, REST API, basic dashboard | next |
| 2 | Decision providers (Mock, RuleBased, Jev), DecisionRecord, inspector, fallback | planned |
| 3 | SafetyGovernor, battery/timeout/geofence rules, operator overrides, dynamic reassignment | planned |
| 4 | ROS 2 + PX4 SITL + Gazebo, PX4FleetAdapter, 3 vehicles | planned |
| 5 | Detection simulation, complete forest-search scenario | planned |
| 6 | Evaluation harness, docs, screenshots, contributor experience | planned |

At the end of each phase: run tests, run lint, fix failures, update docs (including this file),
show what actually works, and name the next smallest useful milestone.

## Domain terminology

- **Mission** — one search-and-rescue operation with a search area, fleet, base, and status.
- **SearchArea / SearchZone** — the operator polygon and its grid partition. Zones carry
  `UNSEARCHED → ASSIGNED → SEARCHING → PARTIAL | COMPLETE | REQUIRES_RECHECK`.
- **Drone / DroneState** — a vehicle and its latest known state (position, battery, link,
  status, assignment, coverage, sensors) with an explicit timestamp.
- **Link state** — `CONNECTED → DEGRADED → STALE → LOST`, derived from telemetry age.
- **Detection / DetectionObservation / CandidateSurvivor** — a sensor hit, its individual
  observations, and an escalated candidate awaiting human confirmation.
- **DecisionRecord** — the audit record for one AI-assisted decision.
- **SafetyEvent** — the audit record for one Safety Governor override or block.
- **OperatorAction** — a human command (confirm, reject, return, hold, pause, abort, e-stop).
- **Proposal** — an action suggested by a decision provider or policy, not yet validated.
- **Disposition** — what a drone should do next: `CONTINUE_SEARCH | RETURN_TO_BASE |
  HANDOFF_ZONE | HOLD | REQUEST_HUMAN_REVIEW`.

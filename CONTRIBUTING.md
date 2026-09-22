# Contributing to AERIS

Thanks for your interest. AERIS is a civilian search-and-rescue research platform, and we
welcome contributions from robotics, backend, frontend, and AI-evaluation backgrounds.

## Scope and safety boundary

AERIS coordinates drones to **find people who need rescue**. We will not accept contributions
that add weapons, weapon targeting, fire control, harmful payload release, autonomous attack or
engagement behavior, military target classification, or autonomous decisions to harm people.
Pull requests adding such functionality will be closed. Person detection exists exclusively to
locate people who may need help.

Architectural rules that every PR must respect are listed in [CLAUDE.md](CLAUDE.md) under
"Non-negotiable rules". In short: AI never touches flight controls, nothing bypasses the
Safety Governor, the domain model stays ROS-free, everything works offline, and every AI
decision is auditable.

## Development setup

No Docker, ROS, or API key is needed.

```bash
git clone https://github.com/rohankc69/aeris.git
cd aeris
pre-commit install                 # pip install pre-commit  /  brew install pre-commit

cd backend
uv sync --all-extras
uv run pytest

cd ../dashboard                    # Phase 1+
pnpm install && pnpm dev
```

See [docs/development.md](docs/development.md) for more.

## Branch workflow

- `main` is always green and releasable.
- Branch from `main`: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
- Open a pull request early; draft PRs are welcome.
- Rebase on `main` before requesting review.

## Commits

Conventional commit style, scoped to the package where practical:

```
feat(planning): boustrophedon planner resumes from partial coverage
fix(safety): treat missing battery telemetry as critical
docs: explain Jev fallback pipeline
test(world): stale telemetry marks drone LOST
```

Small commits with a clear message beat large ones.

## Testing

- `uv run pytest` runs unit and integration tests with the mock decision provider and fake
  fleet. They must pass with no network access.
- Changes to `safety/`, battery or return logic, link-loss handling, geofence, or
  reassignment **must** include tests in the same PR.
- Live Jev tests run only with `uv run pytest --run-live` and a `TYPESAFE_API_KEY`. They are
  never required to merge and never run in CI.
- Simulation scenarios under `sim/scenarios/` are test assets. When you add a behavior, add or
  extend a scenario that exercises it.

## Formatting and linting

```bash
cd backend
uv run ruff format .
uv run ruff check . --fix
uv run mypy aeris
```

`pre-commit` runs these automatically. CI runs them on every PR.

## Pull requests

A good PR:

- does one thing and says what it does in the title (conventional style);
- explains *why* in the description, linking any issue;
- includes tests for behavior changes;
- updates docs (`docs/`, `README.md`, `CLAUDE.md`) when architecture or commands change;
- labels new behavior honestly as real, simulated, mocked, or planned.

## Architecture expectations

- Put pure models in `domain/`; put I/O in adapters, providers, API, and persistence.
- Add new thresholds to `config.py` with a name and unit, never inline.
- New AI questions are new decision modules with a typed input, a bounded output, and a
  rule-based counterpart. Do not widen an existing question.
- New fleet backends implement `FleetAdapter`; new decision backends implement
  `DecisionProvider`; new assignment algorithms implement `AssignmentStrategy`.
- Prefer dependency injection over global state.

## Reporting security issues

See [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

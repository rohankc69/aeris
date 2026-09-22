# Development

## What you need

| Tool | Needed for | Required? |
|---|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | backend, tests, simulation, eval | yes |
| Node 20+ and pnpm | dashboard | for UI work |
| pre-commit | formatting and secret scanning on commit | recommended |
| Docker | optional Postgres/PostGIS; headless PX4/Gazebo (Phase 4) | no |
| ROS 2 / PX4 / Gazebo | Phase 4 only, inside Docker | no |
| Jev API key | live decisions; everything else uses mock/rules | no |

The whole MVP mission runs natively with the fake fleet adapter, the mock decision
provider, and the in-memory repository.

## Setup

```bash
git clone https://github.com/rohankc69/aeris.git
cd aeris
pre-commit install

cd backend
uv sync --all-extras
uv run pytest
uv run aeris config          # prints the effective configuration, secrets redacted
```

## Daily commands

From `backend/`:

| Task | Command |
|---|---|
| tests | `uv run pytest` |
| unit tests only | `uv run pytest -m "not integration"` |
| live Jev tests | `uv run pytest --run-live` (needs `TYPESAFE_API_KEY`; never in CI) |
| format | `uv run ruff format .` |
| lint | `uv run ruff check . --fix` |
| type-check | `uv run mypy aeris` |
| dev server | `uv run uvicorn aeris.api.app:create_app --factory --reload` (Phase 1) |
| simulation | `uv run aeris sim run --scenario forest_search` (Phase 1) |
| evaluation | `uv run aeris eval --scenario low_battery --provider rules` (Phase 6) |

From `dashboard/` (Phase 1): `pnpm install`, `pnpm dev`, `pnpm test`, `pnpm lint`.

## Configuration

Copy `.env.example` to `.env`. All variables are documented there and defined in
`backend/aeris/config.py`. Nested groups use `__`:

```
AERIS_SAFETY__MIN_RETURN_BATTERY_PERCENT=30
AERIS_DECISION__TIMEOUT_S=2
```

Tests force `AERIS_ENV=test`, the fake fleet, and the mock provider through an autouse fixture,
so a developer's `.env` never leaks into the test run.

## Optional: PostgreSQL / PostGIS

Only needed if you want a durable record of missions and decisions across restarts.

```bash
docker compose up -d postgres            # or: brew install postgresql@16 postgis
export DATABASE_URL=postgresql+asyncpg://aeris:aeris@localhost:5432/aeris
```

Without `DATABASE_URL`, AERIS uses an in-memory repository.

## Optional: PX4 / Gazebo (Phase 4, planned)

Runs headless in Docker: `docker compose --profile px4 up`. Gazebo's server runs without a GUI
and PX4 SITL instances communicate with the `aeris_px4_bridge` ROS 2 node over uXRCE-DDS.

Resource note for laptops: three PX4 SITL instances plus a headless Gazebo world plus ROS 2
comfortably need 4 to 6 GB of memory inside the Docker VM. On an 8 GB machine, allocate at
least 5 GB to the VM (Docker Desktop, OrbStack, or Colima), close other heavy apps, and start
with one or two vehicles. Use native arm64 images on Apple Silicon; x86 images under emulation
are several times slower. If it is still too tight, run the PX4 profile on a Linux machine or a
small cloud VM and point `AERIS_FLEET_PROVIDER=px4` at it. This affects Phase 4 only; the full
MVP demo runs natively.

## Project conventions

See `CLAUDE.md` for the architectural rules, coding philosophy, and terminology, and
`CONTRIBUTING.md` for branch, commit, and PR conventions.

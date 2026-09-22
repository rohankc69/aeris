"""AERIS command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from aeris import __version__
from aeris.config import load_settings
from aeris.simulation import ScenarioRunner, list_scenarios, load_scenario

app = typer.Typer(help="AERIS: multi-drone search-and-rescue coordination.", no_args_is_help=True)
sim = typer.Typer(
    help="Run file-defined scenarios against the simulated fleet.", no_args_is_help=True
)
app.add_typer(sim, name="sim")


@app.command()
def version() -> None:
    """Print the AERIS version."""
    typer.echo(f"aeris {__version__}")


@app.command()
def config() -> None:
    """Print the effective configuration (secrets redacted)."""
    typer.echo(load_settings().model_dump_json(indent=2))


@sim.command("list")
def sim_list() -> None:
    """List available scenarios."""
    for name in list_scenarios():
        typer.echo(name)


@sim.command("run")
def sim_run(
    scenario: Annotated[str, typer.Option(help="Scenario name or path to a YAML file")],
    seed: Annotated[int | None, typer.Option(help="Override the scenario seed")] = None,
    max_sim_seconds: Annotated[
        float | None, typer.Option(help="Stop after this much simulated time")
    ] = None,
    output: Annotated[Path | None, typer.Option(help="Write the summary JSON to this file")] = None,
    quiet: Annotated[bool, typer.Option(help="Suppress per-minute progress lines")] = False,
) -> None:
    """Run a scenario headless with the fake fleet and print a summary."""
    spec = load_scenario(scenario)
    overrides: dict[str, object] = {}
    if seed is not None:
        overrides["seed"] = seed
    if max_sim_seconds is not None:
        overrides["max_duration_s"] = max_sim_seconds
    if overrides:
        spec = spec.model_copy(update=overrides)

    runner = ScenarioRunner(spec)
    last_minute = -1

    def progress(snap: object) -> None:
        nonlocal last_minute
        minute = int(runner.elapsed_s // 60)
        if quiet or minute == last_minute:
            return
        last_minute = minute
        s = runner.world.snapshot()
        fleet = " ".join(
            f"{v.drone.drone_id}:{v.state.battery_percent:.0f}%/{v.state.status.value[:4]}"
            if v.state
            else f"{v.drone.drone_id}:--"
            for v in s.drones
        )
        typer.echo(f"T+{minute:02d}m coverage={s.coverage_fraction:5.1%} {fleet}")

    summary = asyncio.run(runner.run(on_tick=progress))
    payload = summary.model_dump_json(indent=2)
    if output:
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(json.dumps(json.loads(payload), indent=2))

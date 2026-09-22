"""AERIS command-line interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Annotated

import typer

from aeris import __version__
from aeris.config import load_settings
from aeris.evaluation import (
    compare_results,
    load_result,
    replay_records,
    run_evaluation,
)
from aeris.evaluation.harness import EvalResult, write_json
from aeris.eventfeed import clock, describe
from aeris.simulation import ScenarioRunner, list_scenarios, load_scenario

app = typer.Typer(help="AERIS: multi-drone search-and-rescue coordination.", no_args_is_help=True)
sim = typer.Typer(
    help="Run file-defined scenarios against the simulated fleet.", no_args_is_help=True
)
app.add_typer(sim, name="sim")
evaluate = typer.Typer(help="Evaluate decision providers on scenarios.", no_args_is_help=True)
app.add_typer(evaluate, name="eval")

DEFAULT_EVAL_DIR = Path(__file__).resolve().parents[2] / "evals" / "out"


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
    events: Annotated[
        bool, typer.Option(help="Stream every decision, safety and mission event as it happens")
    ] = False,
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

    if events:

        async def feed(event: object) -> None:
            data = event.model_dump(mode="json")  # type: ignore[attr-defined]
            line = describe(event.type_name, data)  # type: ignore[attr-defined]
            if line:
                typer.echo(f"{clock(runner.elapsed_s)} {line}")

        runner.bus.subscribe(None, feed)

    def progress(snap: object) -> None:
        nonlocal last_minute
        minute = int(runner.elapsed_s // 60)
        if quiet or events or minute == last_minute:
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


@evaluate.command("run")
def eval_run(
    scenario: Annotated[str, typer.Option(help="Scenario name or YAML path")],
    provider: Annotated[str, typer.Option(help="mock | rules | openrouter")] = "rules",
    seed: Annotated[int | None, typer.Option(help="Override the scenario seed")] = None,
    output_dir: Annotated[
        Path, typer.Option(help="Where to write the result JSON")
    ] = DEFAULT_EVAL_DIR,
) -> None:
    """Run one scenario under one provider and store DecisionRecords, safety events and metrics."""
    result, path = asyncio.run(
        run_evaluation(scenario, provider=provider, seed=seed, output_dir=output_dir)
    )
    typer.echo(json.dumps(result.key_metrics, indent=2))
    typer.echo(f"wrote {path}")


@evaluate.command("matrix")
def eval_matrix(
    scenarios: Annotated[
        str, typer.Option(help="Comma-separated scenario names, or 'all'")
    ] = "all",
    providers: Annotated[str, typer.Option(help="Comma-separated providers")] = "mock,rules",
    seed: Annotated[int | None, typer.Option()] = None,
    output_dir: Annotated[Path, typer.Option()] = DEFAULT_EVAL_DIR,
) -> None:
    """Run every scenario under every provider and print a comparison."""
    names = list_scenarios() if scenarios == "all" else [s.strip() for s in scenarios.split(",")]
    kinds = [p.strip() for p in providers.split(",")]

    async def go() -> list[EvalResult]:
        results: list[EvalResult] = []
        for name in names:
            for kind in kinds:
                result, path = await run_evaluation(
                    name, provider=kind, seed=seed, output_dir=output_dir
                )
                status = str(result.key_metrics["final_status"])
                typer.echo(
                    f"{name:22s} {kind:10s} {status:15s} "
                    f"t={result.summary.elapsed_s:.0f}s -> {path}"
                )
                results.append(result)
        return results

    results = asyncio.run(go())
    comparison = compare_results(results)
    out = output_dir / "comparison.json"
    write_json(out, comparison)
    typer.echo(json.dumps(comparison["decision_agreement"], indent=2))
    typer.echo(f"wrote {out}")


@evaluate.command("compare")
def eval_compare(paths: Annotated[list[Path], typer.Argument(help="Result JSON files")]) -> None:
    """Compare stored evaluation results side by side."""
    results = [load_result(p) for p in paths]
    typer.echo(json.dumps(compare_results(results), indent=2))


@evaluate.command("replay")
def eval_replay(
    result: Annotated[Path, typer.Argument(help="A stored evaluation result JSON")],
    provider: Annotated[
        str, typer.Option(help="Provider to replay the recorded inputs through")
    ] = "rules",
) -> None:
    """Re-ask a provider every recorded decision input and report agreement."""
    stored = load_result(result)
    replay = asyncio.run(replay_records(stored.decisions, provider=provider))
    typer.echo(
        json.dumps(
            {
                "recorded_provider": stored.provider,
                "replayed_provider": replay.provider,
                "records": replay.records,
                "agreement_rate": replay.agreement_rate,
                "disagreements": replay.disagreements[:20],
            },
            indent=2,
        )
    )


@app.command()
def watch(
    mission_id: Annotated[str, typer.Argument(help="Mission id from POST /api/v1/missions")],
    url: Annotated[str, typer.Option(help="Backend base URL")] = "http://localhost:8000",
) -> None:
    """Tail a running mission's events from the terminal (dashboard or PX4 missions alike)."""
    import websockets  # noqa: PLC0415

    ws_url = url.replace("http", "ws", 1) + f"/api/v1/missions/{mission_id}/ws"

    async def tail() -> None:
        async with websockets.connect(ws_url) as conn:
            typer.echo(f"watching {mission_id} at {ws_url}")
            async for raw in conn:
                message = json.loads(raw)
                if message.get("kind") != "event":
                    continue
                data = message["data"]
                line = describe(str(data.get("event_type")), data)
                if line:
                    stamp = str(data.get("timestamp", ""))[11:19]
                    typer.echo(f"{stamp} {line}")

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(tail())

"""AERIS command-line interface.

Subcommands land with their phases: ``sim`` (Phase 1), ``eval`` (Phase 6).
"""

from __future__ import annotations

import typer

from aeris import __version__
from aeris.config import load_settings

app = typer.Typer(help="AERIS: multi-drone search-and-rescue coordination.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the AERIS version."""
    typer.echo(f"aeris {__version__}")


@app.command()
def config() -> None:
    """Print the effective configuration (secrets redacted)."""
    settings = load_settings()
    typer.echo(settings.model_dump_json(indent=2))

"""Shared pytest configuration.

Live tests (marked ``live``) call external services and are skipped unless ``--run-live`` is
passed. CI never passes it.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that call live services"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every test to run with the offline providers regardless of the developer's .env."""
    monkeypatch.setenv("AERIS_ENV", "test")
    monkeypatch.setenv("AERIS_FLEET_PROVIDER", "fake")
    monkeypatch.setenv("AERIS_DECISION_PROVIDER", "mock")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

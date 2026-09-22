"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aeris import __version__
from aeris.api.routes import router
from aeris.api.runtime import MissionRegistry
from aeris.config import Environment, Settings, load_settings
from aeris.telemetry.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(json_output=settings.env is Environment.PRODUCTION)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.registry = MissionRegistry(settings)
        try:
            yield
        finally:
            await app.state.registry.shutdown()

    app = FastAPI(
        title="AERIS",
        version=__version__,
        description="Decision and coordination layer for civilian multi-drone search-and-rescue.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app

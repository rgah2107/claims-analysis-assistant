"""ASGI application.

The lifespan opens telemetry and creates tables. Routes stay in ``api`` so this
file remains the process entrypoint and nothing else.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.api import router
from app.config import get_settings
from app.db import init_db
from app.observability import setup_logging, setup_telemetry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepare logs, traces, and the schema before serving traffic."""
    setup_logging()
    setup_telemetry("claims-api")
    init_db()
    yield


def create_app() -> FastAPI:
    """Build the app. Tests call this so they can construct a client."""
    app = FastAPI(title="Norte Seguros claims assistant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    FastAPIInstrumentor.instrument_app(app)
    return app


app = create_app()

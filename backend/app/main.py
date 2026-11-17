"""FastAPI app factory. Run: ``uvicorn app.main:create_app --factory --reload`` (from ``backend/``)."""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.adapters.registry import Platform, build_platform
from app.api import (
    ai_runtime,
    analysis,
    auth,
    candidates,
    configuration,
    evidence,
    health,
    mappings,
    me,
    metadata,
    readiness,
    reconciliation,
    registries,
    runs,
    evidence_requests,
)
from app.config import Settings, get_settings
from app.db.session import make_sessionmaker
from app.services.errors import ServiceError

API_PREFIX = "/api/rationalisation"
MANIFEST_PATH = Path(__file__).resolve().parents[2] / "manifest.json"


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_app(
    settings: Settings | None = None,
    *,
    platform: Platform | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(
        title="Product Rationalisation Agent API",
        version=__version__,
        description="DeepKorce · Telco · Agentstudio. The agent recommends and explains; people decide and approve.",
    )
    app.state.settings = settings
    app.state.platform = platform or build_platform(settings)
    app.state.sessionmaker = session_factory or make_sessionmaker(settings.database_url)
    app.state.manifest = load_manifest()

    def check_db() -> None:
        with app.state.sessionmaker() as s:
            s.execute(text("SELECT 1"))

    def check_redis() -> None:
        app.state.platform.redis.ping()

    app.state.readiness_checks = {"database": check_db, "redis": check_redis}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(metadata.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)
    app.include_router(registries.router, prefix=API_PREFIX)
    app.include_router(evidence.router, prefix=API_PREFIX)
    app.include_router(mappings.router, prefix=API_PREFIX)
    app.include_router(reconciliation.router, prefix=API_PREFIX)
    app.include_router(readiness.router, prefix=API_PREFIX)
    app.include_router(ai_runtime.router, prefix=API_PREFIX)
    app.include_router(configuration.router, prefix=API_PREFIX)
    app.include_router(analysis.router, prefix=API_PREFIX)
    app.include_router(candidates.router, prefix=API_PREFIX)
    app.include_router(evidence_requests.router, prefix=API_PREFIX)

    @app.exception_handler(ServiceError)
    def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": {
            "code": exc.code, "message": exc.message,
            "errors": [{"field": e.field, "message": e.message} for e in exc.errors],
        }})

    if settings.is_standalone:
        app.include_router(auth.router, prefix=API_PREFIX)
    return app

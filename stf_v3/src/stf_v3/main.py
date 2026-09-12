"""FastAPI application assembly for STF V3.

Author: Xiangzhu Yan
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from stf_v3.auth.router import router as auth_router
from stf_v3.db import engine
from stf_v3.errors import install_error_handlers
from stf_v3.logging_config import configure_logging
from stf_v3.settings import settings
from stf_v3.vehicles.router import router as vehicles_router

log = structlog.get_logger("stf_v3")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup guardrails: logging, secret check, DB reachability."""
    configure_logging(settings.log_level)
    if settings.environment == "prod" and settings.jwt_secret == "change-me-in-deployment":
        raise RuntimeError("STF_V3_JWT_SECRET must be set in prod")
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("startup", environment=settings.environment)
    yield
    await engine.dispose()


app = FastAPI(
    title="STF V3 API",
    version="0.1.0",
    description="Vehicle-anchored AI diagnosis backend (Stage 1).",
    lifespan=lifespan,
    openapi_url="/v3/openapi.json",
    docs_url="/v3/docs",
    redoc_url=None,
)
install_error_handlers(app)
app.include_router(auth_router)
app.include_router(vehicles_router)


@app.get("/health", tags=["system"])
async def health() -> Dict[str, Any]:
    """Liveness + DB reachability + queue backlog."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        backlog = (
            await conn.execute(
                text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'")
            )
        ).scalar()
    return {"status": "ok", "db": "ok", "queue_backlog": backlog}

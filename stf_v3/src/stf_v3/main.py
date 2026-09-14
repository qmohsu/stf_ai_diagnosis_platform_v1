"""FastAPI application assembly for STF V3.

Author: Xiangzhu Yan
"""

import datetime as dt
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from stf_v3.auth.router import router as auth_router
from stf_v3.db import engine
from stf_v3.errors import install_error_handlers
from stf_v3.ingest.router import router as ingest_router
from stf_v3.ingest.storage import LogStorage
from stf_v3.jobs.app import app as queue_app
from stf_v3.knowledge.router import router as knowledge_router
from stf_v3.knowledge.tasks import status_file_path
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
    # PROD-05: the raw-log root must exist and be writable, or uploads
    # would fail at first use instead of at startup.
    storage_root = LogStorage(settings.obd_log_storage_path).root
    storage_root.mkdir(parents=True, exist_ok=True)
    probe = storage_root / ".writable"
    probe.touch()
    probe.unlink()
    # PROD-06: manual library root (shared with the host GPU worker) and the
    # queue connector the API uses to defer ingest jobs.
    manual_root = Path(settings.manual_storage_path).resolve()
    (manual_root / "uploads").mkdir(parents=True, exist_ok=True)
    await queue_app.open_async()
    log.info(
        "startup", environment=settings.environment, storage=str(storage_root),
        manuals=str(manual_root),
    )
    yield
    await queue_app.close_async()
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
app.include_router(ingest_router)
app.include_router(knowledge_router)


def gpu_worker_status() -> Dict[str, Any]:
    """Reads the host GPU worker's heartbeat file (PROD-06, FM-18).

    Returns ``{"alive": bool, "age_s": int|None, "commit": str|None}``;
    alive means a heartbeat younger than 3 minutes.
    """
    try:
        raw = json.loads(status_file_path().read_text(encoding="utf-8"))
        ts = dt.datetime.fromisoformat(raw["ts"])
        age = int((dt.datetime.now(dt.timezone.utc) - ts).total_seconds())
        return {"alive": age < 180, "age_s": age, "commit": raw.get("commit")}
    except (OSError, ValueError, KeyError):
        return {"alive": False, "age_s": None, "commit": None}


@app.get("/health", tags=["system"])
@app.get("/v3/health", tags=["system"])
async def health() -> Dict[str, Any]:
    """Liveness + DB + queue backlog + host GPU worker + disk + commit.

    Served at both ``/health`` (container healthcheck) and ``/v3/health``
    (through nginx, where bare ``/health`` belongs to V1).
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        rows = (
            await conn.execute(
                text("SELECT queue_name, count(*) FROM procrastinate_jobs "
                     "WHERE status = 'todo' GROUP BY queue_name")
            )
        ).all()
    backlog = {q: n for q, n in rows}
    free_gb = round(shutil.disk_usage(Path(settings.manual_storage_path).resolve()).free / 1e9, 1)
    return {
        "status": "ok",
        "db": "ok",
        "queue_backlog": sum(backlog.values()),
        "queue_backlog_by_queue": backlog,
        "gpu_worker": gpu_worker_status(),
        "disk_free_gb": free_gb,
        "commit": settings.git_commit,
    }

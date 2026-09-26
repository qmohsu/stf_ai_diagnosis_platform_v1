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
from stf_v3.diagnosis.router import router as diagnosis_router
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
    version="0.2.0",
    description=(
        "Vehicle-anchored AI diagnosis backend (Stage 1). Contract v2 (PROD-11): all Stage 1 "
        "endpoints — register → create a vehicle → upload a log → POST /v3/vehicles/{id}/diagnose "
        "→ follow GET /v3/conversations/{id}/events → read GET /v3/conversations/{id}/report. "
        "Authorize with the token from POST /v3/auth/login (the Authorize button)."
    ),
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
app.include_router(diagnosis_router)


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
    """Liveness + DB + queue backlog + host GPU worker + disk + commit,
    plus (PROD-11) unfinished diagnoses and the on-demand model service.

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
        diag = (await conn.execute(text(
            "SELECT count(*) FILTER (WHERE status = 'queued'), count(*) FILTER (WHERE status = 'running'), "
            "EXTRACT(EPOCH FROM now() - min(created_at)) FROM diagnosis_conversations "
            "WHERE status IN ('queued','running')"))).one()
        llm = (await conn.execute(text(
            "SELECT state, blocked_reason, started_by_us, "
            "EXTRACT(EPOCH FROM now() - controller_seen_at), "
            "EXTRACT(EPOCH FROM now() - GREATEST(last_used_at, ready_at)) "
            "FROM model_service_state WHERE id = 1"))).first()
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
        # FM-31: how long the oldest unfinished diagnosis has waited.
        "diagnosis": {
            "queued": int(diag[0] or 0), "running": int(diag[1] or 0),
            "oldest_unfinished_s": int(diag[2]) if diag[2] is not None else None,
        },
        # FM-31 / FM-32: the controller's view; ``idle_s`` while ready says
        # how long vLLM has been idle (it should stop after llm_idle_stop_s).
        "model_service": None if llm is None else {
            "state": llm[0], "blocked_reason": llm[1], "started_by_us": llm[2],
            "controller_seen_s": int(llm[3]) if llm[3] is not None else None,
            "idle_s": int(llm[4]) if (llm[0] == "ready" and llm[4] is not None) else None,
        },
    }

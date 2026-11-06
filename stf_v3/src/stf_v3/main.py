"""FastAPI application assembly for STF V3.

Author: Xiangzhu Yan
"""

import datetime as dt
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from stf_v3.auth.router import router as auth_router
from stf_v3.db import engine
from stf_v3.diagnosis.model_service import blocked_by
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
    configure_logging(settings.log_level,
                      process=os.environ.get("STF_V3_PROCESS", "api"))
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


# PROD-15A: the host backup service writes its status into a read-only volume
# of this container.  Read from the environment here, not from Settings:
# settings.py is a golden-gate managed path and this is not an agent change.
BACKUP_STATUS_PATH = os.environ.get("STF_V3_BACKUP_STATUS_PATH", "./data/backup_state/status.json")
BACKUP_STALE_HOURS = float(os.environ.get("STF_V3_BACKUP_STALE_HOURS", "36"))


def backup_status() -> Dict[str, Any]:
    """Reads the host backup service's status file (PROD-15A, FM-40).

    Judged by the time of the last SUCCESSFUL backup, never by the absence
    of failures: ``state`` is ``ok`` (younger than ``backup_stale_hours``),
    ``stale`` or ``unknown`` (no status file yet).  Threshold: 36 h.  The offsite copy on the
    network share is reported the same way.
    """
    def judge(ts: Any) -> Dict[str, Any]:
        try:
            when = dt.datetime.fromisoformat(str(ts))
        except ValueError:
            return {"state": "unknown", "last_success_at": None, "age_h": None}
        age_h = round((dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600, 1)
        return {"state": "ok" if age_h < BACKUP_STALE_HOURS else "stale",
                "last_success_at": when.isoformat(), "age_h": age_h}

    try:
        raw = json.loads(Path(BACKUP_STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown", "last_success_at": None, "age_h": None, "last_result": None,
                "offsite": {"state": "unknown", "last_success_at": None, "age_h": None}}
    out = judge(raw.get("last_success_at"))
    out["last_result"] = raw.get("last_result")
    out["offsite"] = judge((raw.get("offsite") or {}).get("last_success_at"))
    out["last_verify"] = raw.get("last_verify")     # PROD-15A ②: weekly check {at, ok}
    return out


@app.get("/health", tags=["system"])
@app.get("/v3/health", tags=["system"])
async def health() -> Dict[str, Any]:
    """Liveness + DB + queue backlog + host GPU worker + disk + commit,
    plus (PROD-11) unfinished diagnoses and the on-demand model service,
    plus (PROD-15A) the time of the last successful backup and the
    runtime role's connections against its limit.

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
            "EXTRACT(EPOCH FROM now() - GREATEST(last_used_at, ready_at)), gpu_snapshot "
            "FROM model_service_state WHERE id = 1"))).first()
        # PROD-15A FM-45: this role's connections vs its limit, and the whole
        # shared instance (V1/V2 included) vs max_connections.
        conns = (await conn.execute(text(
            "SELECT count(*) FILTER (WHERE usename = current_user), "
            "(SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user), "
            "count(*), current_setting('max_connections')::int "
            "FROM pg_stat_activity WHERE backend_type = 'client backend'"))).one()
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
            # #255 FM-21: every kind holding memory when blocked (the reason
            # above is the highest-priority one).
            "blocked_by": blocked_by(llm[5]) if llm[0] == "blocked" else [],
        },
        # PROD-15A FM-40: last SUCCESSFUL backup (stale after 36 h).
        "backup": backup_status(),
        "db_connections": {
            "role": int(conns[0]), "role_limit": None if conns[1] in (None, -1) else int(conns[1]),
            "instance": int(conns[2]), "instance_max": int(conns[3]),
        },
    }

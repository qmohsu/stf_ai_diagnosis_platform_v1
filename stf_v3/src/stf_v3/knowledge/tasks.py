"""Queue tasks of the knowledge module (PROD-06).

Two tasks live on the ``gpu`` queue, consumed ONLY by the host GPU worker
(same package, Python 3.11 venv on the host; see ``stf_v3/gpu_worker/``):

* ``knowledge.ingest_manual`` — the one-step pipeline (MinerU → build →
  index → summaries → gates → install).  Retry up to 2 more times for
  transient errors; permanent errors (bad worker, preflight refusal,
  cancelled) are never retried (FM-8, FM-13).
* ``knowledge.gpu_heartbeat`` — once a minute writes a status file into
  the manual volume so ``/v3/health`` and ``deploy_check.sh`` can see the
  host worker is alive and on which commit (FM-16, FM-18).

The API container only DEFERS these by name and never imports the heavy
pipeline package (FM-29): the pipeline import happens inside the task
body, i.e. only in the worker process.

Author: Xiangzhu Yan
"""

import datetime as dt
import json
import os
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import procrastinate
import structlog
from procrastinate import RetryDecision
from procrastinate.retry import BaseRetryStrategy

from stf_v3.jobs.app import app
from stf_v3.settings import settings

log = structlog.get_logger("stf_v3.knowledge.tasks")

INGEST_TASK = "knowledge.ingest_manual"
HEARTBEAT_TASK = "knowledge.gpu_heartbeat"
GPU_QUEUE = "gpu"
STATUS_FILE = ".gpu_worker_status.json"


class PermanentIngestError(Exception):
    """Raised for failures that a retry cannot fix (never retried)."""


class IngestRetry(BaseRetryStrategy):
    """Retry transient failures with a growing delay; never permanent ones."""

    def __init__(self, max_attempts: int = 3, wait_s: int = 60) -> None:
        self.max_attempts = max_attempts
        self.wait_s = wait_s

    def get_retry_decision(  # type: ignore[override]
        self, *, exception: BaseException, job: procrastinate.jobs.Job
    ) -> Optional[RetryDecision]:
        if isinstance(exception, PermanentIngestError):
            return None
        if job.attempts >= self.max_attempts:
            return None
        delay = self.wait_s * (2 ** (job.attempts - 1))
        return RetryDecision(retry_in={"seconds": delay})


def worker_commit() -> str:
    """Git commit of the checkout the host worker runs from."""
    repo = settings.repo_dir or os.environ.get("STF_V3_REPO_DIR", "")
    if repo:
        try:
            return subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "HEAD"], text=True, timeout=5
            ).strip()
        except Exception:  # noqa: BLE001 - fall through to the env stamp
            pass
    return settings.git_commit


def status_file_path() -> Path:
    """Where the heartbeat status file lives (inside the manual volume)."""
    return Path(settings.manual_storage_path).resolve() / STATUS_FILE


def write_status(busy_with: Optional[str] = None, scheduled: Optional[int] = None) -> None:
    """Atomically writes the host worker's liveness + commit status file.

    Called by the periodic heartbeat job AND, while an ingest job occupies
    the single-concurrency gpu queue, by a ticker thread inside that job —
    otherwise the heartbeat jobs would queue behind the ingest and the
    worker would look dead for the whole conversion.
    """
    payload = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scheduled": scheduled,
        "busy_with": busy_with,
        "commit": worker_commit(),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "procrastinate": procrastinate.__version__,
        "mineru_bin": settings.mineru_bin,
    }
    path = status_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


@app.periodic(cron="* * * * *")
@app.task(name=HEARTBEAT_TASK, queue=GPU_QUEUE, pass_context=False)
def gpu_heartbeat(timestamp: int) -> None:
    """Writes the host worker's liveness + commit into the manual volume."""
    write_status(scheduled=timestamp)
    log.info("gpu_worker.heartbeat", commit=worker_commit()[:12])


@app.task(
    name=INGEST_TASK, queue=GPU_QUEUE, pass_context=True,
    retry=IngestRetry(max_attempts=3, wait_s=60),
)
def ingest_manual(context: procrastinate.JobContext, manual_id: str) -> None:
    """Runs the one-step ingest pipeline for one manual (sync, in worker).

    The runner is loaded by name on purpose: the import-linter contract
    forbids any static path from the API modules to the heavy pipeline,
    and this task module IS imported by the API (to defer by name).  The
    dynamic import keeps the heavy code out of the API process; the unit
    test ``test_api_process_never_imports_pipeline_or_fitz`` proves it.
    """
    import importlib

    runner = importlib.import_module("stf_v3.knowledge.ingest")
    runner.run_ingest(uuid.UUID(manual_id), context)


async def defer_ingest(manual_id: uuid.UUID) -> int:
    """Queues an ingest job for ``manual_id`` and returns its job id."""
    return await ingest_manual.defer_async(manual_id=str(manual_id))


async def cancel_ingest(job_id: int) -> None:
    """Cancels a queued job or asks a running one to abort (FM-10)."""
    try:
        await app.job_manager.cancel_job_by_id_async(job_id, abort=True)
    except Exception as exc:  # noqa: BLE001 - job may already be finished
        log.info("manual.cancel_noop", job_id=job_id, reason=str(exc))

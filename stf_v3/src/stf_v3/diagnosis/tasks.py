"""Queue tasks of the diagnosis module (PROD-11).

* ``diagnosis.run`` (queue ``diagnosis``, container worker): one
  conversation.  Every job carries the lock ``diagnosis-model`` so the
  worker runs ONE diagnosis at a time whatever its concurrency (dev plan
  §4 FM-11, round-2 FM-16); retries are off — an interrupted diagnosis
  is closed as ``error``, never silently re-run (blueprint §6.2, FM-17).
* ``llm.reconcile`` (queue ``llm``, host GPU worker only): the on-demand
  model controller (``model_service.reconcile``).  Periodic every minute
  plus on demand from waiting diagnoses (``queueing_lock`` keeps at most
  one on-demand pass waiting); the lock ``llm-control`` serialises passes
  so a start is never issued twice (FM-20).  No arguments (FM-40).
* ``diagnosis.sweep`` (queue ``default``): closes unfinished conversations
  whose job never existed or already ended (FM-9 / FM-12).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import uuid
from typing import Optional

import procrastinate
import structlog
from procrastinate.exceptions import AlreadyEnqueued

from stf_v3.jobs.app import app

log = structlog.get_logger(__name__)

DIAGNOSIS_TASK = "diagnosis.run"
DIAGNOSIS_QUEUE = "diagnosis"
DIAGNOSIS_LOCK = "diagnosis-model"
RECONCILE_TASK = "llm.reconcile"
LLM_QUEUE = "llm"
RECONCILE_LOCK = "llm-control"
RECONCILE_QUEUEING_LOCK = "llm-reconcile"
SWEEP_TASK = "diagnosis.sweep"


async def request_reconcile() -> None:
    """Asks the host controller for a pass now (deduplicated)."""
    try:
        await llm_reconcile.defer_async()
    except AlreadyEnqueued:
        pass


@app.task(name=DIAGNOSIS_TASK, queue=DIAGNOSIS_QUEUE, pass_context=True, lock=DIAGNOSIS_LOCK)
async def run_diagnosis_job(context: procrastinate.JobContext, conversation_id: str) -> None:
    """Runs one queued conversation (see ``diagnosis.job``)."""
    from stf_v3.diagnosis.job import run_conversation

    job_id = context.job.id if context.job is not None else None
    await run_conversation(uuid.UUID(conversation_id), job_id, request_reconcile=request_reconcile)


async def defer_diagnosis(conversation_id: uuid.UUID) -> int:
    """Queues ``diagnosis.run`` for a conversation; returns the job id."""
    return await run_diagnosis_job.defer_async(conversation_id=str(conversation_id))


# queueing_lock: at most ONE pass waits in the queue — while the host worker
# is down the per-minute periodic jobs do not pile up (the periodic deferrer
# skips a duplicate), and it resumes with a single pass.
@app.periodic(cron="* * * * *")
@app.task(name=RECONCILE_TASK, queue=LLM_QUEUE, pass_context=False, lock=RECONCILE_LOCK,
          queueing_lock=RECONCILE_QUEUEING_LOCK)
async def llm_reconcile(timestamp: Optional[int] = None) -> None:
    """One pass of the on-demand model controller (host GPU worker only)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.model_service import HostIO, reconcile
    from stf_v3.settings import settings

    await reconcile(SessionLocal, settings, HostIO(settings))


async def cancel_job(job_id: int) -> None:
    """Cancels a queued diagnosis job (a running one stops via its flag)."""
    try:
        await app.job_manager.cancel_job_by_id_async(job_id, abort=True)
    except Exception as exc:  # noqa: BLE001 — the job may already have finished
        log.info("diagnosis.cancel_job_noop", job_id=job_id, reason=type(exc).__name__)


async def job_status(job_id: int) -> Optional[str]:
    """The queue status of a job, or None when it no longer exists."""
    try:
        status = await app.job_manager.get_job_status_async(job_id)
    except (TypeError, KeyError):
        return None
    return status.value


@app.periodic(cron="*/2 * * * *")
@app.task(name=SWEEP_TASK, queue="default", pass_context=False)
async def sweep_conversations(timestamp: int) -> None:
    """Closes conversations nobody will finish (FM-9 / FM-12)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.store import sweep
    from stf_v3.settings import settings

    await sweep(SessionLocal, job_status, stale_s=settings.diagnosis_queue_stale_s,
                locale=settings.default_locale)

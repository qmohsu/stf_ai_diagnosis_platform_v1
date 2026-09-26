"""Queue self-maintenance (PROD-06 T-18, FM-7): recover stalled jobs.

procrastinate marks a job ``doing`` while a worker runs it and records
worker heartbeats; if the worker dies (kill -9, container restart past
the grace period) the job stays ``doing`` forever unless someone re-queues
it.  This periodic task runs on the ``default`` queue every two minutes:
any job whose worker has not heart-beaten for ``STALL_SECONDS`` is put
back to ``todo`` (its retry strategy still applies: manual ingests are
re-run from their persisted work dir, the 60 s drill job simply reruns).

PROD-11 (round-2 FM-17): a stalled **diagnosis** job is never re-queued —
re-running it would replay a conversation that already has events.  Its
job is marked failed and its conversation closed as ``error``
``diagnosis_interrupted`` (with ``error`` + ``done`` events), so the user
sees what happened and can start again.  Stalled is decided from worker
heartbeats only, never from how long a job has run (FM-35).

Author: Xiangzhu Yan
"""

import inspect

import structlog
from procrastinate.jobs import Status

from stf_v3.jobs.app import app

log = structlog.get_logger("stf_v3.jobs")

STALL_SECONDS = 120


async def _maybe_await(value):  # type: ignore[no-untyped-def]
    return await value if inspect.isawaitable(value) else value


@app.periodic(cron="*/2 * * * *")
@app.task(name="jobs.recover_stalled", queue="default", pass_context=False)
async def recover_stalled(timestamp: int) -> None:
    """Re-queues stalled jobs; closes stalled diagnoses instead (PROD-11)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.store import close_for_job
    from stf_v3.diagnosis.tasks import DIAGNOSIS_TASK
    from stf_v3.settings import settings

    manager = app.job_manager
    stalled = await _maybe_await(manager.get_stalled_jobs(seconds_since_heartbeat=STALL_SECONDS))
    count = 0
    for job in stalled:
        if job.task_name == DIAGNOSIS_TASK:
            await manager.finish_job_by_id_async(job.id, Status.FAILED, delete_job=False)
            closed = await close_for_job(SessionLocal, job.id, "diagnosis_interrupted",
                                         locale=settings.default_locale)
            log.warning("queue.stalled_diagnosis_closed", job_id=job.id, conversation_closed=closed)
            continue
        await _maybe_await(manager.retry_job(job))
        count += 1
        log.warning("queue.stalled_job_requeued", job_id=job.id, task=job.task_name, attempts=job.attempts)
    await _maybe_await(manager.prune_stalled_workers(seconds_since_heartbeat=STALL_SECONDS))
    if count:
        log.info("queue.recover_stalled", requeued=count, at=timestamp)

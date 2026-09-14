"""Queue self-maintenance (PROD-06 T-18, FM-7): recover stalled jobs.

procrastinate marks a job ``doing`` while a worker runs it and records
worker heartbeats; if the worker dies (kill -9, container restart past
the grace period) the job stays ``doing`` forever unless someone re-queues
it.  This periodic task runs on the ``default`` queue every two minutes:
any job whose worker has not heart-beaten for ``STALL_SECONDS`` is put
back to ``todo`` (its retry strategy still applies: manual ingests are
re-run from their persisted work dir, the 60 s drill job simply reruns).

Author: Xiangzhu Yan
"""

import inspect

import structlog

from stf_v3.jobs.app import app

log = structlog.get_logger("stf_v3.jobs")

STALL_SECONDS = 120


@app.periodic(cron="*/2 * * * *")
@app.task(name="jobs.recover_stalled", queue="default", pass_context=False)
async def recover_stalled(timestamp: int) -> None:
    """Re-queues jobs whose worker heartbeat is older than STALL_SECONDS."""
    manager = app.job_manager
    stalled = manager.get_stalled_jobs(seconds_since_heartbeat=STALL_SECONDS)
    if inspect.isawaitable(stalled):
        stalled = await stalled
    count = 0
    for job in stalled:
        result = manager.retry_job(job)
        if inspect.isawaitable(result):
            await result
        count += 1
        log.warning("queue.stalled_job_requeued", job_id=job.id, task=job.task_name, attempts=job.attempts)
    pruned = manager.prune_stalled_workers(seconds_since_heartbeat=STALL_SECONDS)
    if inspect.isawaitable(pruned):
        await pruned
    if count:
        log.info("queue.recover_stalled", requeued=count, at=timestamp)

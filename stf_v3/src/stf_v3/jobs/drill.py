"""Operational drill task (PROD-06 §2.4 运维演练).

``jobs.drill_sleep`` just sleeps; ``scripts/queue_ops.sh drill`` defers it,
restarts the container worker mid-run and proves the job is recovered
and completed (procrastinate's stalled-job handling).  Never used by
product code.

Author: Xiangzhu Yan
"""

import asyncio

import structlog

from stf_v3.jobs.app import app

log = structlog.get_logger("stf_v3.jobs")


@app.task(name="jobs.drill_sleep", queue="default", pass_context=False, retry=3)
async def sleep_task(seconds: int = 60) -> None:
    """Sleeps ``seconds`` seconds in 5 s ticks (visible in the worker log)."""
    for tick in range(0, seconds, 5):
        log.info("drill.tick", elapsed=tick, total=seconds)
        await asyncio.sleep(5)
    log.info("drill.done", total=seconds)

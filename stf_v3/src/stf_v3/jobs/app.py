"""procrastinate application (queue on Postgres, code design §6).

PROD-04 ships the app with a single periodic ``heartbeat`` task so the
worker container is live and observable; PROD-06 adds the real tasks
(manual conversion on the ``gpu`` queue, diagnosis, maintenance).

Worker command (same image as the API)::

    procrastinate --app stf_v3.jobs.app.app worker -q default --concurrency 2

Author: Xiangzhu Yan
"""

import procrastinate
import structlog

from stf_v3.settings import settings

log = structlog.get_logger("stf_v3.jobs")


def conninfo_from_sqlalchemy_url(url: str) -> str:
    """Strips the SQLAlchemy driver suffix so psycopg can use the URL.

    Args:
        url: e.g. ``postgresql+psycopg://user:pw@host:5432/db``.

    Returns:
        ``postgresql://user:pw@host:5432/db``.
    """
    scheme, rest = url.split("://", 1)
    return f"{scheme.split('+', 1)[0]}://{rest}"


app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(
        conninfo=conninfo_from_sqlalchemy_url(settings.database_url)
    ),
    import_paths=[],
)


@app.periodic(cron="* * * * *")
@app.task(name="jobs.heartbeat", queue="default", pass_context=False)
async def heartbeat(timestamp: int) -> None:
    """Logs one line per minute so ``deploy_check.sh`` can see the worker.

    Args:
        timestamp: Scheduled Unix time supplied by procrastinate.
    """
    log.info("worker.heartbeat", timestamp=timestamp)

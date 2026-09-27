"""Worker entry point: logging first, then the unchanged procrastinate CLI.

``python -m stf_v3.jobs.worker_main <procrastinate arguments>`` behaves like
the ``procrastinate`` command, except that business and framework logs go
through ``configure_logging`` — to stdout and to ``<STF_V3_LOG_DIR>/
<STF_V3_PROCESS>.log`` (PROD-15A).  procrastinate's own ``basicConfig`` is a
no-op once the root logger has handlers.

Author: Xiangzhu Yan
"""

import os

from procrastinate import cli

from stf_v3.logging_config import configure_logging


def main() -> None:
    """Configures logging from the environment, then runs the procrastinate CLI."""
    configure_logging(os.environ.get("STF_V3_LOG_LEVEL", "INFO"),
                      process=os.environ.get("STF_V3_PROCESS", "worker"))
    cli.main()


if __name__ == "__main__":
    main()

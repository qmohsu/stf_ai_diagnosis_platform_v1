"""structlog configuration: JSON lines to stdout (container log).

Author: Xiangzhu Yan
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configures stdlib + structlog for JSON output.

    Args:
        level: Log level name (``DEBUG``, ``INFO`` ...).
    """
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=level.upper()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

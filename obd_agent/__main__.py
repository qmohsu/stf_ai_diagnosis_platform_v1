"""CLI entry point: ``python -m obd_agent [--dry-run] [--once]``."""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog


def _configure_logging(level: str, fmt: str) -> None:
    """Set up structlog with console or JSON rendering."""
    import logging

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="obd_agent",
        description="OBD-II telemetry agent for STF diagnostic platform",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Validate snapshots locally; never POST to API",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Capture a single snapshot then exit",
    )
    args = parser.parse_args()

    # Load settings from env / .env file first, then override with CLI flags.
    from obd_agent.config import AgentSettings

    settings = AgentSettings()
    if args.dry_run is True:
        settings.dry_run = True

    _configure_logging(settings.log_level, settings.log_format)

    logger = structlog.get_logger("obd_agent")
    logger.info(
        "agent_starting",
        version=__import__("obd_agent").__version__,
        mode="simulation" if settings.is_simulation else "live",
        dry_run=settings.dry_run,
        once=args.once,
        port=settings.obd_port,
        vehicle_id=settings.vehicle_id,
    )

    from obd_agent.agent_loop import run_agent

    try:
        asyncio.run(run_agent(settings, once=args.once))
    except KeyboardInterrupt:
        logger.info("agent_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()

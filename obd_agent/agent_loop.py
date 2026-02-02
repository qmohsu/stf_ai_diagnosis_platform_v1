"""Main asyncio polling loop for the OBD agent."""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from obd_agent.api_poster import APIPoster
from obd_agent.config import AgentSettings
from obd_agent.reader.base import OBDReader
from obd_agent.snapshot_builder import build_snapshot

logger = structlog.get_logger(__name__)


def create_reader(settings: AgentSettings) -> OBDReader:
    """Factory: return the right reader for the current config.

    ``LiveReader`` is imported lazily so simulation mode works without
    the GPL-licensed ``python-obd`` package installed.
    """
    if settings.is_simulation:
        from obd_agent.reader.simulation import SimulationReader

        return SimulationReader(scenario=settings.obd_sim_scenario)

    # Lazy import keeps GPL dependency out of sim/CI environments.
    from obd_agent.reader.live import LiveReader

    return LiveReader(port=settings.obd_port, baudrate=settings.obd_baudrate)


async def run_agent(
    settings: AgentSettings,
    *,
    once: bool = False,
) -> None:
    """Run the OBD agent loop.

    Parameters
    ----------
    settings:
        Fully-resolved agent configuration.
    once:
        If ``True``, capture a single snapshot then exit.
    """
    shutdown_event = asyncio.Event()

    # --- signal handling ---------------------------------------------------
    def _request_shutdown() -> None:
        logger.info("shutdown_requested")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown)
    # On Windows, SIGINT is handled by the default KeyboardInterrupt.

    reader = create_reader(settings)
    poster = APIPoster(settings)

    await poster.start()
    try:
        await _loop(reader, poster, settings, shutdown_event, once=once)
    finally:
        await reader.disconnect()
        await poster.close()


async def _loop(
    reader: OBDReader,
    poster: APIPoster,
    settings: AgentSettings,
    shutdown_event: asyncio.Event,
    *,
    once: bool,
) -> None:
    """Core read-post-sleep loop with auto-reconnect."""

    while not shutdown_event.is_set():
        # --- connect (or reconnect) ----------------------------------------
        if not reader.is_connected():
            try:
                await reader.connect()
                logger.info(
                    "reader_connected",
                    mode="simulation" if settings.is_simulation else "live",
                    port=settings.obd_port,
                )
            except Exception:
                logger.exception("reader_connect_failed")
                if once:
                    return
                await _interruptible_sleep(
                    settings.snapshot_interval_seconds, shutdown_event
                )
                continue

        # --- snapshot ------------------------------------------------------
        try:
            snapshot = await build_snapshot(reader, settings)
        except Exception:
            logger.exception("snapshot_build_failed")
            if once:
                return
            await _interruptible_sleep(
                settings.snapshot_interval_seconds, shutdown_event
            )
            continue

        # --- post ----------------------------------------------------------
        try:
            await poster.post_snapshot(snapshot)
        except Exception:
            logger.exception("snapshot_post_failed")

        if once:
            return

        await _interruptible_sleep(
            settings.snapshot_interval_seconds, shutdown_event
        )


async def _interruptible_sleep(
    seconds: float, event: asyncio.Event
) -> None:
    """Sleep for *seconds* but wake early if *event* is set."""
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass

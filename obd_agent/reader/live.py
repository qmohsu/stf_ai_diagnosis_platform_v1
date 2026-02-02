"""LiveReader -- python-OBD hardware wrapper.

``python-OBD`` is imported lazily inside methods so that simulation mode
works without the GPL-licensed dependency installed.  All blocking I/O
is offloaded to a thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import structlog

from obd_agent.reader.base import OBDReader

logger = structlog.get_logger(__name__)


class LiveReader(OBDReader):
    """Wraps ``obd.OBD`` for real ELM327 adapter communication."""

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self._port = port
        self._baudrate = baudrate
        self._connection: Any = None  # obd.OBD instance (lazy)

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        obd = _import_obd()
        self._connection = await asyncio.to_thread(
            obd.OBD, portstr=self._port, baudrate=self._baudrate
        )
        status = self._connection.status()
        logger.info("live_reader_connected", port=self._port, status=str(status))
        if str(status) not in ("Car Connected",):
            logger.warning(
                "obd_status_limited",
                status=str(status),
                hint="some commands may not be available",
            )

    async def disconnect(self) -> None:
        if self._connection is not None:
            await asyncio.to_thread(self._connection.close)
            self._connection = None

    def is_connected(self) -> bool:
        if self._connection is None:
            return False
        obd = _import_obd()
        return str(self._connection.status()) != str(obd.OBDStatus.NOT_CONNECTED)

    # -- data reads ---------------------------------------------------------

    async def read_dtcs(self) -> List[Tuple[str, str]]:
        self._check_connected()
        obd = _import_obd()
        response = await asyncio.to_thread(
            self._connection.query, obd.commands.GET_DTC
        )
        if response.is_null():
            return []
        return [
            (code, desc) for code, desc in response.value
        ]

    async def read_pid(self, name: str) -> Optional[Tuple[float, str]]:
        self._check_connected()
        obd = _import_obd()
        cmd = _resolve_command(obd, name)
        if cmd is None:
            return None
        response = await asyncio.to_thread(self._connection.query, cmd)
        if response.is_null():
            return None
        val = response.value
        # python-OBD returns pint Quantity objects
        try:
            return (float(val.magnitude), str(val.units))
        except AttributeError:
            return (float(val), "")

    async def read_supported_pids(self) -> List[str]:
        self._check_connected()
        supported = await asyncio.to_thread(
            lambda: self._connection.supported_commands
        )
        return [cmd.name for cmd in supported if cmd.name]

    async def read_freeze_frame(self) -> Dict[str, Tuple[float, str]]:
        self._check_connected()
        obd = _import_obd()
        response = await asyncio.to_thread(
            self._connection.query, obd.commands.FREEZE_DTC
        )
        if response.is_null():
            return {}
        # FREEZE_DTC returns DTC list; actual freeze frame PIDs must be
        # read individually via Mode 02.  For Phase 1 we return an empty
        # dict if individual FF reads aren't available.
        logger.info(
            "freeze_frame_not_implemented",
            hint="Mode 02 individual PID reads not yet supported; "
            "returning empty freeze frame",
        )
        return {}

    # -- internal -----------------------------------------------------------

    def _check_connected(self) -> None:
        if self._connection is None:
            raise RuntimeError("LiveReader is not connected")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_obd() -> Any:
    """Lazy-import python-OBD so it's only needed in live mode."""
    try:
        import obd  # type: ignore[import-untyped]
        return obd
    except ImportError as exc:
        raise ImportError(
            "python-OBD is required for live mode. "
            "Install it with: pip install obd"
        ) from exc


def _resolve_command(obd: Any, name: str) -> Any:
    """Map a PID name string to an ``obd.commands`` entry."""
    cmd = obd.commands.get(name)
    if cmd is not None:
        return cmd
    # Fallback: try upper-case
    cmd = obd.commands.get(name.upper())
    return cmd

"""Assembles an ``OBDSnapshot`` from a connected reader."""

from __future__ import annotations

from typing import Dict

import structlog

from obd_agent.config import AgentSettings
from obd_agent.reader.base import OBDReader
from obd_agent.schemas import AdapterInfo, DTCEntry, OBDSnapshot, PIDValue

logger = structlog.get_logger(__name__)

# Baseline PIDs we always attempt to read.
_BASELINE_PID_NAMES = [
    "RPM",
    "COOLANT_TEMP",
    "SHORT_FUEL_TRIM_1",
    "LONG_FUEL_TRIM_1",
    "INTAKE_PRESSURE",
    "SPEED",
    "THROTTLE_POS",
    "ENGINE_LOAD",
]


async def build_snapshot(
    reader: OBDReader,
    settings: AgentSettings,
) -> OBDSnapshot:
    """Read all OBD-II data from *reader* and return a validated snapshot."""

    if not reader.is_connected():
        raise RuntimeError("Reader is not connected")

    # 1. DTCs
    raw_dtcs = await reader.read_dtcs()
    dtc_entries = [DTCEntry(code=code, desc=desc) for code, desc in raw_dtcs]

    # 2. Freeze frame
    raw_ff = await reader.read_freeze_frame()
    freeze_frame = {
        pid: PIDValue(value=val, unit=unit)
        for pid, (val, unit) in raw_ff.items()
    }

    # 3. Supported PIDs
    supported = await reader.read_supported_pids()

    # 4. Baseline PID readings
    baseline: Dict[str, PIDValue] = {}
    for pid_name in _BASELINE_PID_NAMES:
        result = await reader.read_pid(pid_name)
        if result is not None:
            value, unit = result
            baseline[pid_name] = PIDValue(value=value, unit=unit)

    adapter_port = settings.obd_port if not settings.is_simulation else "sim"

    snapshot = OBDSnapshot(
        vehicle_id=settings.vehicle_id,
        adapter=AdapterInfo(type="ELM327", port=adapter_port),
        dtc=dtc_entries,
        freeze_frame=freeze_frame,
        supported_pids=supported,
        baseline_pids=baseline,
    )

    logger.info(
        "snapshot_built",
        vehicle_id=snapshot.vehicle_id,
        dtc_count=len(snapshot.dtc),
        baseline_pids=len(snapshot.baseline_pids),
    )
    return snapshot

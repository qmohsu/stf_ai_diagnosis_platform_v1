"""Tests for obd_agent.snapshot_builder."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from obd_agent.config import AgentSettings
from obd_agent.reader.base import OBDReader
from obd_agent.schemas import OBDSnapshot
from obd_agent.snapshot_builder import build_snapshot


class FakeReader(OBDReader):
    """Minimal in-memory reader for testing."""

    def __init__(
        self,
        *,
        connected: bool = True,
        dtcs: List[Tuple[str, str]] | None = None,
        pids: Dict[str, Tuple[float, str]] | None = None,
        supported: List[str] | None = None,
        freeze: Dict[str, Tuple[float, str]] | None = None,
    ) -> None:
        self._connected = connected
        self._dtcs = dtcs or []
        self._pids = pids or {}
        self._supported = supported or []
        self._freeze = freeze or {}

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def read_dtcs(self) -> List[Tuple[str, str]]:
        return self._dtcs

    async def read_pid(self, name: str) -> Optional[Tuple[float, str]]:
        return self._pids.get(name)

    async def read_supported_pids(self) -> List[str]:
        return self._supported

    async def read_freeze_frame(self) -> Dict[str, Tuple[float, str]]:
        return self._freeze


@pytest.fixture()
def settings() -> AgentSettings:
    return AgentSettings(obd_port="sim", vehicle_id="V-TEST-001")


@pytest.mark.asyncio
async def test_build_snapshot_known_data(settings: AgentSettings) -> None:
    reader = FakeReader(
        dtcs=[("P0301", "Cylinder 1 misfire")],
        pids={"RPM": (780.0, "rpm"), "COOLANT_TEMP": (90.0, "degC")},
        supported=["RPM", "COOLANT_TEMP"],
        freeze={"RPM": (850.0, "rpm")},
    )
    snap = await build_snapshot(reader, settings)

    assert isinstance(snap, OBDSnapshot)
    assert snap.vehicle_id == "V-TEST-001"
    assert len(snap.dtc) == 1
    assert snap.dtc[0].code == "P0301"
    assert snap.baseline_pids["RPM"].value == 780.0
    assert snap.freeze_frame["RPM"].value == 850.0
    assert "RPM" in snap.supported_pids


@pytest.mark.asyncio
async def test_disconnected_reader_raises(settings: AgentSettings) -> None:
    reader = FakeReader(connected=False)
    with pytest.raises(RuntimeError, match="not connected"):
        await build_snapshot(reader, settings)


@pytest.mark.asyncio
async def test_empty_dtcs_valid(settings: AgentSettings) -> None:
    reader = FakeReader(
        dtcs=[],
        pids={"RPM": (780.0, "rpm")},
        supported=["RPM"],
    )
    snap = await build_snapshot(reader, settings)
    assert snap.dtc == []


@pytest.mark.asyncio
async def test_missing_pids_skipped(settings: AgentSettings) -> None:
    """PIDs not available on the vehicle are silently skipped."""
    reader = FakeReader(
        pids={"RPM": (800.0, "rpm")},
        supported=["RPM"],
    )
    snap = await build_snapshot(reader, settings)
    assert "RPM" in snap.baseline_pids
    assert "COOLANT_TEMP" not in snap.baseline_pids

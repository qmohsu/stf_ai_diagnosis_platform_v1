"""Tests for obd_agent.reader.simulation -- SimulationReader."""

from __future__ import annotations

import pytest

from obd_agent.reader.simulation import SimulationReader


@pytest.mark.asyncio
async def test_connect_disconnect_lifecycle() -> None:
    reader = SimulationReader(scenario="healthy")
    assert not reader.is_connected()

    await reader.connect()
    assert reader.is_connected()

    await reader.disconnect()
    assert not reader.is_connected()


@pytest.mark.asyncio
async def test_unknown_scenario_raises() -> None:
    reader = SimulationReader(scenario="nonexistent")
    with pytest.raises(ValueError, match="Unknown simulation scenario"):
        await reader.connect()


@pytest.mark.asyncio
async def test_read_dtcs_misfire() -> None:
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    dtcs = await reader.read_dtcs()
    codes = [code for code, _ in dtcs]
    assert "P0301" in codes
    assert "P0171" in codes
    await reader.disconnect()


@pytest.mark.asyncio
async def test_read_dtcs_healthy_empty() -> None:
    reader = SimulationReader(scenario="healthy")
    await reader.connect()
    dtcs = await reader.read_dtcs()
    assert dtcs == []
    await reader.disconnect()


@pytest.mark.asyncio
async def test_read_pid_returns_value_in_range() -> None:
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    result = await reader.read_pid("RPM")
    assert result is not None
    value, unit = result
    assert unit == "rpm"
    # Base 780, noise 40 -> value should be roughly in range
    assert 600 < value < 1000
    await reader.disconnect()


@pytest.mark.asyncio
async def test_read_pid_unknown_returns_none() -> None:
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    result = await reader.read_pid("NONEXISTENT_PID")
    assert result is None
    await reader.disconnect()


@pytest.mark.asyncio
async def test_noise_varies_between_reads() -> None:
    """Consecutive reads should produce different values (noise)."""
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    values = set()
    for _ in range(20):
        result = await reader.read_pid("RPM")
        assert result is not None
        values.add(result[0])
    # With noise=40 and 20 reads, we expect variation
    assert len(values) > 1
    await reader.disconnect()


@pytest.mark.asyncio
async def test_supported_pids() -> None:
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    pids = await reader.read_supported_pids()
    assert "RPM" in pids
    assert "COOLANT_TEMP" in pids
    await reader.disconnect()


@pytest.mark.asyncio
async def test_freeze_frame() -> None:
    reader = SimulationReader(scenario="misfire")
    await reader.connect()
    ff = await reader.read_freeze_frame()
    assert "RPM" in ff
    value, unit = ff["RPM"]
    assert isinstance(value, float)
    assert unit == "rpm"
    await reader.disconnect()


@pytest.mark.asyncio
async def test_read_while_disconnected_raises() -> None:
    reader = SimulationReader(scenario="misfire")
    with pytest.raises(RuntimeError, match="not connected"):
        await reader.read_dtcs()

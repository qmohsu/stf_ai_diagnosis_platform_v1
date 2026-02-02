"""Tests for obd_agent.agent_loop -- factory and single iteration."""

from __future__ import annotations

import pytest

from obd_agent.agent_loop import create_reader, run_agent
from obd_agent.config import AgentSettings
from obd_agent.reader.simulation import SimulationReader


def test_factory_returns_simulation_reader() -> None:
    settings = AgentSettings(obd_port="sim")
    reader = create_reader(settings)
    assert isinstance(reader, SimulationReader)


def test_factory_simulation_case_insensitive() -> None:
    settings = AgentSettings(obd_port="SIM")
    reader = create_reader(settings)
    assert isinstance(reader, SimulationReader)


@pytest.mark.asyncio
async def test_single_iteration_dry_run() -> None:
    """A single once + dry_run iteration should complete without error."""
    settings = AgentSettings(
        obd_port="sim",
        vehicle_id="V-TEST-001",
        dry_run=True,
        obd_sim_scenario="misfire",
    )
    # Should not raise
    await run_agent(settings, once=True)


@pytest.mark.asyncio
async def test_single_iteration_healthy_scenario() -> None:
    settings = AgentSettings(
        obd_port="sim",
        vehicle_id="V-TEST-002",
        dry_run=True,
        obd_sim_scenario="healthy",
    )
    await run_agent(settings, once=True)

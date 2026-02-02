"""Shared pytest fixtures for OBD agent tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Generator

import pytest

from obd_agent.config import AgentSettings
from obd_agent.schemas import OBDSnapshot

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_scenario_cache() -> Generator[None, None, None]:
    """Clear the simulation scenario cache between tests.

    Prevents mutable global state from leaking across tests if any
    test were to mutate the loaded scenario data.
    """
    from obd_agent.reader import simulation

    simulation._scenarios_cache = None
    yield
    simulation._scenarios_cache = None


@pytest.fixture()
def sample_snapshot_dict() -> Dict[str, Any]:
    """Load the canonical sample snapshot as a plain dict."""
    path = _FIXTURES_DIR / "obd_snapshot.sample.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def sample_snapshot(sample_snapshot_dict: Dict[str, Any]) -> OBDSnapshot:
    """Parse the canonical sample snapshot into a validated model."""
    return OBDSnapshot.model_validate(sample_snapshot_dict)

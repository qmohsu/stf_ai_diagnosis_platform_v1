"""Shared pytest fixtures for OBD agent tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from obd_agent.schemas import OBDSnapshot

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


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

"""Fixture-based simulation reader (no hardware required).

Loads scenarios from ``fixtures/simulation_scenarios.json`` and applies
Gaussian noise to each PID read so consecutive snapshots vary
realistically.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from obd_agent.reader.base import OBDReader

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class SimulationReader(OBDReader):
    """Reads synthetic OBD-II data from JSON fixture scenarios."""

    def __init__(self, scenario: str = "misfire") -> None:
        self._scenario_name = scenario
        self._scenario: Dict[str, Any] = {}
        self._connected = False

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        scenarios = _load_scenarios()
        if self._scenario_name not in scenarios:
            available = ", ".join(sorted(scenarios))
            raise ValueError(
                f"Unknown simulation scenario '{self._scenario_name}'. "
                f"Available: {available}"
            )
        self._scenario = scenarios[self._scenario_name]
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # -- data reads ---------------------------------------------------------

    async def read_dtcs(self) -> List[Tuple[str, str]]:
        self._check_connected()
        return [
            (entry["code"], entry.get("desc", ""))
            for entry in self._scenario.get("dtc", [])
        ]

    async def read_pid(self, name: str) -> Optional[Tuple[float, str]]:
        self._check_connected()
        pid_def = self._scenario.get("baseline_pids", {}).get(name)
        if pid_def is None:
            return None
        value = _apply_noise(pid_def["base"], pid_def.get("noise", 0.0))
        return (value, pid_def["unit"])

    async def read_supported_pids(self) -> List[str]:
        self._check_connected()
        return list(self._scenario.get("supported_pids", []))

    async def read_freeze_frame(self) -> Dict[str, Tuple[float, str]]:
        self._check_connected()
        result: Dict[str, Tuple[float, str]] = {}
        for pid_name, pid_def in self._scenario.get("freeze_frame", {}).items():
            value = _apply_noise(pid_def["base"], pid_def.get("noise", 0.0))
            result[pid_name] = (value, pid_def["unit"])
        return result

    # -- internal -----------------------------------------------------------

    def _check_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("SimulationReader is not connected")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_scenarios_cache: Optional[Dict[str, Any]] = None


def _load_scenarios() -> Dict[str, Any]:
    global _scenarios_cache
    if _scenarios_cache is None:
        path = _FIXTURES_DIR / "simulation_scenarios.json"
        with open(path, encoding="utf-8") as fh:
            _scenarios_cache = json.load(fh)
    return _scenarios_cache


def _apply_noise(base: float, noise: float) -> float:
    """Apply Gaussian noise (std-dev = noise) to a base value.

    Result is clamped to >= 0 since OBD PID values are non-negative.
    """
    if noise <= 0:
        return base
    return round(max(0.0, base + random.gauss(0, noise)), 2)

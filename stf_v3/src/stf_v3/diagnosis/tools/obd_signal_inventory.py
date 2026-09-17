"""Signal inventory + classification for the OBD tools (copied from V2).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from stf_v3.ingest.loader import PID_UNITS, OBDLogData, try_float

Subsystem = Literal["engine", "abs", "other"]

_YAMAHA_UNITS: Dict[str, str] = {
    "A_KL_RPM": "rpm", "A_KL_SPEED": "km/h", "A_KL_COOLANT_TEMP": "°C",
    "A_KL_IAT": "°C", "A_KL_MAP": "kPa", "A_KL_BARO": "kPa",
    "A_KL_TIMING_ADV": "deg", "A_KL_TPS": "%", "A_KL_REL_TPS": "%",
    "A_KL_ENGINE_LOAD": "%", "A_KL_CTRL_VOLT": "V",
    "A_YAM_BARO_REF": "kPa", "A_YAM_BATT_V": "V", "A_YAM_CHT": "°C",
    "A_YAM_ECT": "°C", "A_YAM_IAT": "°C", "A_YAM_IGN": "deg",
    "A_YAM_INJ_MS": "ms", "A_YAM_INJ_US": "us", "A_YAM_ISC": "step",
    "A_YAM_RPM": "rpm", "A_YAM_VVA": "deg",
    "A_YAM_BATT_RAW": "raw", "A_YAM_MAP_RAW": "raw",
    "A_YAM_O2_FB_RAW": "raw", "A_YAM_TPS_RAW": "raw",
}

_STANDARD_PID_NAMES = frozenset({
    "RPM", "SPEED", "COOLANT_TEMP", "INTAKE_TEMP", "IAT",
    "MAP", "BARO", "BAROMETRIC_PRESSURE", "INTAKE_PRESSURE",
    "ENGINE_LOAD", "ABSOLUTE_LOAD",
    "THROTTLE_POS", "THROTTLE_POS_B", "RELATIVE_THROTTLE_POS",
    "TIMING_ADVANCE", "MAF", "FUEL_RAIL_PRESSURE_DIRECT",
    "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
    "O2_B1S2", "O2_S1_WR_CURRENT",
    "CONTROL_MODULE_VOLTAGE", "ELM_VOLTAGE",
    "ACCELERATOR_POS_D", "ACCELERATOR_POS_E",
    "RUN_TIME", "DISTANCE_W_MIL", "DISTANCE_SINCE_DTC_CLEAR",
    "COMMANDED_EQUIV_RATIO", "CATALYST_TEMP_B1S1",
})


@dataclass(frozen=True)
class SignalDescriptor:
    """Typed view of one signal column."""

    name: str
    units: str
    subsystem: Subsystem
    density: float
    valid_count: int
    total_count: int

    @property
    def density_label(self) -> str:
        if self.density >= 0.99:
            return "dense"
        if self.density >= 0.5:
            return f"sparse ({int(self.density * 100)}%)"
        if self.density > 0:
            return f"very sparse ({int(self.density * 100)}%)"
        return "all N/A"


def classify_subsystem(col: str) -> Subsystem:
    """Tag a column with its originating ECU subsystem."""
    if col.startswith("A_KL_") or col.startswith("A_YAM_"):
        return "engine"
    if col.startswith("B_"):
        return "abs"
    if col.upper() in _STANDARD_PID_NAMES:
        return "engine"
    return "other"


def units_for(col: str) -> str:
    """Unit lookup: Yamaha curated map, then standard PIDs, else unknown."""
    if col in _YAMAHA_UNITS:
        return _YAMAHA_UNITS[col]
    return PID_UNITS.get(col, "unknown")


def _density(rows: List[Dict[str, str]], col: str) -> Tuple[int, int]:
    total = len(rows)
    if total == 0:
        return 0, 0
    valid = sum(1 for r in rows if try_float(r.get(col, "")) is not None)
    return valid, total


def build_inventory(data: OBDLogData) -> List[SignalDescriptor]:
    """One descriptor per signal column."""
    out: List[SignalDescriptor] = []
    for col in data.columns:
        valid, total = _density(data.rows, col)
        out.append(SignalDescriptor(
            name=col, units=units_for(col), subsystem=classify_subsystem(col),
            density=(valid / total) if total else 0.0,
            valid_count=valid, total_count=total,
        ))
    return out


def filter_inventory(
    inventory: List[SignalDescriptor],
    pattern: Optional[str],
    subsystem: Literal["engine", "abs", "all"],
) -> List[SignalDescriptor]:
    """Glob + subsystem filter (case-insensitive)."""
    out = list(inventory)
    if subsystem != "all":
        out = [d for d in out if d.subsystem == subsystem]
    if pattern:
        pat = pattern.lower()
        out = [d for d in out if fnmatch.fnmatchcase(d.name.lower(), pat)]
    return out


def resolve_signal_name(name: str, inventory: List[SignalDescriptor]) -> Optional[str]:
    """Exact → case-insensitive → suffix match (shortest wins)."""
    if not name:
        return None
    names = [d.name for d in inventory]
    if name in names:
        return name
    upper = name.upper()
    for col in names:
        if col.upper() == upper:
            return col
    candidates = [c for c in names if c.upper().endswith(upper)]
    if candidates:
        return min(candidates, key=len)
    return None


def fuzzy_suggestions(
    name: str, inventory: List[SignalDescriptor], max_suggestions: int = 3,
) -> List[str]:
    """Close-match suggestions for an unknown signal name."""
    if not inventory:
        return []
    query = (name or "").lower()
    scored: List[Tuple[int, str]] = []
    for d in inventory:
        col_lower = d.name.lower()
        if query and query in col_lower:
            scored.append((0, d.name))
            continue
        shared = len(set(col_lower) & set(query))
        if shared:
            scored.append((10 - shared, d.name))
    scored.sort(key=lambda t: (t[0], len(t[1])))
    suggestions = [n for _, n in scored[:max_suggestions]]
    return suggestions or [d.name for d in inventory[:max_suggestions]]

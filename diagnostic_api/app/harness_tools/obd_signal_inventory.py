"""Signal inventory + classification for the OBD investigation tools.

Builds a typed view of the columns present in a parsed ``OBDLogData``
so the agent tools (``list_signals``, ``read_window``,
``get_signal_stats``, ``find_events``) can reason about which signals
are dense vs sparse, which ECU they came from, and what units they
carry.

Units for ``A_KL_*`` and ``A_YAM_*`` columns come from a hand-curated
map below (HARNESS-19 locked decision: expose Yamaha proprietary
columns under their original names, with best-effort unit annotations
where we know them).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from app.harness_tools.obd_loader import OBDLogData, try_float


Subsystem = Literal["engine", "abs", "other"]
"""Subsystem tag for a single signal."""


# ── Curated unit map ─────────────────────────────────────────────


_YAMAHA_UNITS: Dict[str, str] = {
    # K-Line canonical PIDs (Channel A, engine ECU)
    "A_KL_RPM": "rpm",
    "A_KL_SPEED": "km/h",
    "A_KL_COOLANT_TEMP": "°C",
    "A_KL_IAT": "°C",
    "A_KL_MAP": "kPa",
    "A_KL_BARO": "kPa",
    "A_KL_TIMING_ADV": "deg",
    "A_KL_TPS": "%",
    "A_KL_REL_TPS": "%",
    "A_KL_ENGINE_LOAD": "%",
    "A_KL_CTRL_VOLT": "V",
    # Yamaha proprietary confirmed
    "A_YAM_BARO_REF": "kPa",
    "A_YAM_BATT_V": "V",
    "A_YAM_CHT": "°C",
    "A_YAM_ECT": "°C",
    "A_YAM_IAT": "°C",
    "A_YAM_IGN": "deg",
    "A_YAM_INJ_MS": "ms",
    "A_YAM_INJ_US": "us",
    "A_YAM_ISC": "step",
    "A_YAM_RPM": "rpm",
    "A_YAM_VVA": "deg",
    # Yamaha proprietary provisional (raw bytes — unit unknown)
    "A_YAM_BATT_RAW": "raw",
    "A_YAM_MAP_RAW": "raw",
    "A_YAM_O2_FB_RAW": "raw",
    "A_YAM_TPS_RAW": "raw",
}
"""Curated unit annotations for Yamaha-format columns.

For standard-OBD-TSV columns we fall back to the unit map in
``obd_agent.log_parser._PID_UNITS``.
"""


def _standard_unit(col: str) -> Optional[str]:
    """Look up the unit for a standard OBD-II PID column.

    Imports lazily to avoid a top-level circular dependency with
    ``obd_agent``.
    """
    from obd_agent.log_parser import _PID_UNITS
    return _PID_UNITS.get(col)


# ── Signal descriptor ────────────────────────────────────────────


@dataclass(frozen=True)
class SignalDescriptor:
    """Typed view of one column in an OBD log.

    Attributes:
        name: Column name as it appears in the file (e.g. ``RPM``
            or ``A_YAM_INJ_MS``).
        units: Engineering units string when known, else
            ``"unknown"``.  ``"raw"`` for Yamaha provisional bytes.
        subsystem: ECU subsystem tag.
        density: Fraction of rows with a parseable numeric value.
            ``0.0`` means the column is all N/A.
        valid_count: Absolute count of parseable numeric samples.
        total_count: Total row count.
    """

    name: str
    units: str
    subsystem: Subsystem
    density: float
    valid_count: int
    total_count: int

    @property
    def density_label(self) -> str:
        """Coarse density label for human-friendly output."""
        if self.density >= 0.99:
            return "dense"
        if self.density >= 0.5:
            return f"sparse ({int(self.density * 100)}%)"
        if self.density > 0:
            return f"very sparse ({int(self.density * 100)}%)"
        return "all N/A"


# ── Classification + inventory build ─────────────────────────────


_STANDARD_PID_NAMES = frozenset({
    "RPM", "SPEED", "COOLANT_TEMP", "INTAKE_TEMP", "IAT",
    "MAP", "BARO", "BAROMETRIC_PRESSURE",
    "ENGINE_LOAD", "ABSOLUTE_LOAD",
    "THROTTLE_POS", "THROTTLE_POS_B", "RELATIVE_THROTTLE_POS",
    "TIMING_ADVANCE", "MAF", "FUEL_RAIL_PRESSURE_DIRECT",
    "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
    "O2_B1S2", "O2_S1_WR_CURRENT",
    "CONTROL_MODULE_VOLTAGE", "ELM_VOLTAGE",
    "ACCELERATOR_POS_D", "ACCELERATOR_POS_E",
    "RUN_TIME", "DISTANCE_W_MIL", "DISTANCE_SINCE_DTC_CLEAR",
    "COMMANDED_EQUIV_RATIO",
})
"""Known standard OBD-II PID column names (no prefix).

Used by ``classify_subsystem`` to recognise pre-normalised
columns (RPM, COOLANT_TEMP, etc.) as engine signals.
"""


def classify_subsystem(col: str) -> Subsystem:
    """Tag a column with its originating ECU subsystem.

    Heuristic based on the Yamaha-dual prefix convention
    (``A_*`` = Channel A engine, ``B_*`` = Channel B ABS) plus
    a curated list of standard OBD-II PID names that have no
    prefix and represent engine signals.

    Args:
        col: Column name.

    Returns:
        Subsystem tag.
    """
    if col.startswith("A_KL_") or col.startswith("A_YAM_"):
        return "engine"
    if col.startswith("B_"):
        return "abs"
    if col.upper() in _STANDARD_PID_NAMES:
        return "engine"
    return "other"


def units_for(col: str) -> str:
    """Resolve the engineering unit for a column.

    Order of lookup:
    1. Yamaha-format curated map.
    2. Standard OBD-II ``_PID_UNITS`` map (via lazy import).
    3. Fallback ``"unknown"``.
    """
    if col in _YAMAHA_UNITS:
        return _YAMAHA_UNITS[col]
    std = _standard_unit(col)
    if std is not None:
        return std
    return "unknown"


def _density(rows: List[Dict[str, str]], col: str) -> tuple:
    """Compute (valid_count, total_count) for a column.

    ``valid_count`` counts cells parseable as float (per
    ``try_float`` — treats ``N/A`` as missing).
    """
    total = len(rows)
    if total == 0:
        return 0, 0
    valid = sum(
        1 for r in rows if try_float(r.get(col, "")) is not None
    )
    return valid, total


def build_inventory(
    data: OBDLogData,
) -> List[SignalDescriptor]:
    """Build a ``SignalDescriptor`` list for every signal column.

    ``Timestamp`` and any all-zero-length string columns are
    excluded.  Density is computed by scanning the rows once per
    column — O(rows * cols) but the fixture is small.

    Args:
        data: Parsed log.

    Returns:
        Ordered list of descriptors matching ``data.columns``.
    """
    out: List[SignalDescriptor] = []
    for col in data.columns:
        valid, total = _density(data.rows, col)
        density = (valid / total) if total else 0.0
        out.append(SignalDescriptor(
            name=col,
            units=units_for(col),
            subsystem=classify_subsystem(col),
            density=density,
            valid_count=valid,
            total_count=total,
        ))
    return out


# ── Lookup helpers used by the signal tools ──────────────────────


def filter_inventory(
    inventory: List[SignalDescriptor],
    pattern: Optional[str],
    subsystem: Literal["engine", "abs", "all"],
) -> List[SignalDescriptor]:
    """Filter an inventory by glob pattern and subsystem.

    Pattern matching is case-insensitive against the column name.
    Use shell-style globs (``*TEMP*``, ``A_YAM_*``, ``RPM``).

    Args:
        inventory: Full inventory from ``build_inventory``.
        pattern: Optional glob pattern.
        subsystem: Subsystem filter (``"all"`` = no filter).

    Returns:
        Filtered list.
    """
    out = list(inventory)
    if subsystem != "all":
        out = [d for d in out if d.subsystem == subsystem]
    if pattern:
        pat = pattern.lower()
        out = [
            d for d in out
            if fnmatch.fnmatchcase(d.name.lower(), pat)
        ]
    return out


def resolve_signal_name(
    name: str,
    inventory: List[SignalDescriptor],
) -> Optional[str]:
    """Resolve a user-supplied signal name to a canonical column.

    Matching strategy:
    1. Exact match against an inventory column name.
    2. Case-insensitive match.
    3. Suffix match — useful when the LLM passes ``RPM`` and the
       column is ``A_YAM_RPM``.  Returns the shortest matching
       column name to bias toward canonical ``A_KL_`` columns over
       proprietary ``A_YAM_`` ones.

    Args:
        name: Caller-supplied signal name.
        inventory: Full inventory.

    Returns:
        Canonical column name or ``None`` if no match.
    """
    if not name:
        return None
    names = [d.name for d in inventory]
    if name in names:
        return name
    upper = name.upper()
    for col in names:
        if col.upper() == upper:
            return col
    # Suffix match — prefer shortest (canonical K-Line over Yamaha
    # proprietary when both are present).
    candidates = [c for c in names if c.upper().endswith(upper)]
    if candidates:
        return min(candidates, key=len)
    return None


def fuzzy_suggestions(
    name: str,
    inventory: List[SignalDescriptor],
    max_suggestions: int = 3,
) -> List[str]:
    """Suggest close-match signal names for an unknown input.

    Cheap substring + edit-distance hybrid — returns names that
    share a common substring with the input, ranked by length
    proximity to the query.  Falls back to first-N inventory
    columns if nothing matches.

    Args:
        name: Unknown signal name from the caller.
        inventory: Full inventory.
        max_suggestions: Maximum suggestions to return.

    Returns:
        Up to ``max_suggestions`` candidate column names.
    """
    if not inventory:
        return []
    query = (name or "").lower()
    scored: List[tuple] = []
    for d in inventory:
        col_lower = d.name.lower()
        # Substring hit gets best score.
        if query and query in col_lower:
            scored.append((0, d.name))
            continue
        # Otherwise score by shared characters.
        shared = len(set(col_lower) & set(query))
        if shared:
            scored.append((10 - shared, d.name))
    scored.sort(key=lambda t: (t[0], len(t[1])))
    suggestions = [name for _, name in scored[:max_suggestions]]
    if suggestions:
        return suggestions
    return [d.name for d in inventory[:max_suggestions]]

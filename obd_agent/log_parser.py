"""Parse OBD-II TSV log files into ``OBDSnapshot`` objects.

Handles the tab-separated log format produced by python-OBD data loggers:
- 4-line header (title, start time, interval, separator)
- Column header row
- Separator row
- Data rows (tab-separated values)
- Footer (blank, separator, end time)
"""

from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from obd_agent.schemas import AdapterInfo, DTCEntry, OBDSnapshot, PIDValue

# PID name -> engineering unit.  Only PIDs with numeric float values.
_PID_UNITS: Dict[str, str] = {
    "RPM": "rpm",
    "SPEED": "km/h",
    "THROTTLE_POS": "percent",
    "THROTTLE_POS_B": "percent",
    "ENGINE_LOAD": "percent",
    "ABSOLUTE_LOAD": "percent",
    "RELATIVE_THROTTLE_POS": "percent",
    "THROTTLE_ACTUATOR": "percent",
    "COOLANT_TEMP": "degC",
    "INTAKE_TEMP": "degC",
    "CATALYST_TEMP_B1S1": "degC",
    "MAF": "g/s",
    "INTAKE_PRESSURE": "kPa",
    "BAROMETRIC_PRESSURE": "kPa",
    "FUEL_RAIL_PRESSURE_DIRECT": "kPa",
    "SHORT_FUEL_TRIM_1": "percent",
    "LONG_FUEL_TRIM_1": "percent",
    "TIMING_ADVANCE": "degree",
    "O2_B1S2": "volt",
    "O2_S1_WR_CURRENT": "mA",
    "EGR_ERROR": "percent",
    "COMMANDED_EGR": "percent",
    "EVAPORATIVE_PURGE": "percent",
    "RUN_TIME": "second",
    "WARMUPS_SINCE_DTC_CLEAR": "count",
    "DISTANCE_W_MIL": "km",
    "DISTANCE_SINCE_DTC_CLEAR": "km",
    "CONTROL_MODULE_VOLTAGE": "volt",
    "ELM_VOLTAGE": "volt",
    "ACCELERATOR_POS_D": "percent",
    "ACCELERATOR_POS_E": "percent",
    "COMMANDED_EQUIV_RATIO": "ratio",
}

# Columns that are non-numeric / metadata -- skip when building PID dicts.
_SKIP_COLUMNS = {
    "Timestamp",
    "FUEL_TYPE",
    "FUEL_STATUS",
    "O2_SENSORS",
    "VIN",
    "CALIBRATION_ID",
    "CVN",
    "OBD_COMPLIANCE",
    "STATUS",
    "GET_DTC",
    "GET_CURRENT_DTC",
    "CLEAR_DTC",
    "ELM_VERSION",
}

_BYTEARRAY_RE = re.compile(r"bytearray\(b'([^']*)'\)")
_DTC_CODE_RE = re.compile(r"[PCBU][0-9A-Fa-f]{4}")


def pseudonymise_vin(raw_vin: str) -> str:
    """Derive a pseudonymous vehicle ID from a raw VIN.

    Uses a truncated SHA-256 hash prefix so the original VIN cannot be
    recovered, but the same VIN always produces the same ID.
    """
    digest = hashlib.sha256(raw_vin.encode()).hexdigest()[:8]
    return f"V-{digest.upper()}"


def parse_log_file(path: str | Path) -> List[Dict[str, str]]:
    """Parse an OBD TSV log file into a list of row dicts.

    Each dict maps column name -> raw string value for one data row.
    Header/footer lines are skipped automatically.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    # Find column header: first line with "Timestamp" and tabs.
    header_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.startswith("Timestamp\t") or line.startswith("Timestamp\t\t"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not find column header in {path}")

    columns = [c.strip() for c in lines[header_idx].split("\t") if c.strip()]

    # Data rows start after the separator line following the header.
    data_start = header_idx + 2  # skip header + separator

    rows: List[Dict[str, str]] = []
    for line in lines[data_start:]:
        line = line.rstrip("\n\r")
        if not line or line.startswith("---") or line.startswith("Log "):
            continue
        parts = line.split("\t")
        if len(parts) < len(columns):
            continue
        row = {columns[i]: parts[i].strip() for i in range(len(columns))}
        rows.append(row)

    return rows


def _extract_vin(raw: str) -> Optional[str]:
    """Extract VIN string from ``bytearray(b'...')`` repr."""
    m = _BYTEARRAY_RE.search(raw)
    if m:
        return m.group(1)
    # Might be a plain string already.
    stripped = raw.strip()
    if stripped and stripped != "N/A":
        return stripped
    return None


def _parse_dtc_list(raw: str) -> List[Tuple[str, str]]:
    """Parse the GET_DTC / GET_CURRENT_DTC column value.

    Possible formats from python-OBD logs:
    - ``[]``
    - ``[('P0301', 'Cylinder 1 Misfire Detected')]``
    - ``N/A``
    """
    raw = raw.strip()
    if raw in ("[]", "N/A", ""):
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [(code, desc) for code, desc in parsed]
    except (ValueError, SyntaxError):
        pass
    # Fallback: extract DTC codes via regex.
    codes = _DTC_CODE_RE.findall(raw)
    return [(c.upper(), "") for c in codes]


def _try_float(raw: str) -> Optional[float]:
    """Try to parse a string as float, return None on failure."""
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def row_to_snapshot(
    row: Dict[str, str],
    *,
    vehicle_id: Optional[str] = None,
    adapter_port: str = "log-replay",
) -> OBDSnapshot:
    """Convert a single parsed log row into a validated ``OBDSnapshot``.

    Parameters
    ----------
    row:
        Column-name → raw-string dict from ``parse_log_file()``.
    vehicle_id:
        Override vehicle ID.  If ``None``, the VIN column is hashed
        into a pseudonymous ID via ``pseudonymise_vin()``.
    adapter_port:
        Value for ``AdapterInfo.port``.
    """
    # --- timestamp ---------------------------------------------------------
    ts_raw = row.get("Timestamp", "")
    try:
        ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        ts = datetime.now(timezone.utc)

    # --- vehicle ID (pseudonymised) ----------------------------------------
    if vehicle_id is None:
        raw_vin = _extract_vin(row.get("VIN", ""))
        if raw_vin:
            vehicle_id = pseudonymise_vin(raw_vin)
        else:
            vehicle_id = "V-UNKNOWN"

    # --- adapter info ------------------------------------------------------
    elm_version = row.get("ELM_VERSION", "ELM327").strip()
    adapter = AdapterInfo(type=elm_version, port=adapter_port)

    # --- DTCs --------------------------------------------------------------
    dtc_tuples = _parse_dtc_list(row.get("GET_DTC", "[]"))
    dtc_tuples += _parse_dtc_list(row.get("GET_CURRENT_DTC", "[]"))
    # Deduplicate by code.
    seen_codes: set[str] = set()
    dtc_entries: List[DTCEntry] = []
    for code, desc in dtc_tuples:
        code = code.upper()
        if code not in seen_codes and _DTC_CODE_RE.fullmatch(code):
            dtc_entries.append(DTCEntry(code=code, desc=desc))
            seen_codes.add(code)

    # --- numeric PIDs → PIDValue dicts -------------------------------------
    baseline_pids: Dict[str, PIDValue] = {}
    supported_pids: List[str] = []

    for col_name, raw_val in row.items():
        if col_name in _SKIP_COLUMNS:
            continue
        unit = _PID_UNITS.get(col_name)
        if unit is None:
            continue
        val = _try_float(raw_val)
        if val is not None:
            supported_pids.append(col_name)
            baseline_pids[col_name] = PIDValue(value=val, unit=unit)

    return OBDSnapshot(
        vehicle_id=vehicle_id,
        ts=ts,
        adapter=adapter,
        dtc=dtc_entries,
        freeze_frame={},  # TSV logs don't carry freeze-frame data
        supported_pids=sorted(supported_pids),
        baseline_pids=baseline_pids,
    )


def log_file_to_snapshots(
    path: str | Path,
    *,
    vehicle_id: Optional[str] = None,
    adapter_port: str = "log-replay",
) -> List[OBDSnapshot]:
    """Parse an entire log file and return one ``OBDSnapshot`` per row."""
    rows = parse_log_file(path)
    return [
        row_to_snapshot(row, vehicle_id=vehicle_id, adapter_port=adapter_port)
        for row in rows
    ]

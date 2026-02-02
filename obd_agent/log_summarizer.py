"""Aggregate OBDSnapshots into a compact LLM-friendly summary.

Converts the full per-row snapshot list (e.g. 158 snapshots / 600KB JSON)
into a single compact summary (~50-80 lines JSON) suitable as prompt context
for the cloud diagnosis model.  See design_doc sections 8.1 and 9.5.
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from obd_agent.log_parser import log_file_to_snapshots
from obd_agent.schemas import OBDSnapshot

# The 8 critical PIDs from the snapshot builder -- core health indicators.
CRITICAL_PIDS = (
    "RPM",
    "COOLANT_TEMP",
    "SHORT_FUEL_TRIM_1",
    "LONG_FUEL_TRIM_1",
    "INTAKE_PRESSURE",
    "SPEED",
    "THROTTLE_POS",
    "ENGINE_LOAD",
)

# Human-readable unit abbreviations for anomaly messages.
_UNIT_SYMBOLS: Dict[str, str] = {
    "percent": "%",
    "rpm": " rpm",
    "degC": "\u00b0C",
    "km/h": " km/h",
    "kPa": " kPa",
}

# Typical operating ranges for anomaly detection.
_OPERATING_RANGES: Dict[str, Tuple[float, float]] = {
    "RPM": (0.0, 8000.0),
    "COOLANT_TEMP": (-40.0, 110.0),
    "SHORT_FUEL_TRIM_1": (-25.0, 25.0),
    "LONG_FUEL_TRIM_1": (-25.0, 25.0),
    "INTAKE_PRESSURE": (0.0, 255.0),
    "SPEED": (0.0, 250.0),
    "THROTTLE_POS": (0.0, 100.0),
    "ENGINE_LOAD": (0.0, 100.0),
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TimeRange(BaseModel):
    """Time span covered by the log."""

    start: str
    end: str
    duration_seconds: int
    sample_count: int


class PIDStatModel(BaseModel):
    """JSON-serialisable PID statistics."""

    min: float
    max: float
    mean: float
    latest: float
    unit: str


class LogSummary(BaseModel):
    """Compact log summary for LLM prompt context."""

    vehicle_id: str
    adapter: str
    time_range: TimeRange
    dtc_codes: List[str] = Field(default_factory=list)
    pid_summary: Dict[str, PIDStatModel] = Field(default_factory=dict)
    anomalies: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Summarisation logic
# ---------------------------------------------------------------------------

def _collect_pid_values(
    snapshots: List[OBDSnapshot],
    pid: str,
) -> Tuple[List[float], str]:
    """Extract all values and unit for *pid* across snapshots (in order)."""
    values: List[float] = []
    unit = ""
    for snap in snapshots:
        pv = snap.baseline_pids.get(pid)
        if pv is not None:
            values.append(pv.value)
            if not unit:
                unit = pv.unit
    return values, unit


def _detect_anomalies(
    pid: str,
    values: List[float],
    unit: str,
) -> List[str]:
    """Apply simple heuristics to detect anomalous patterns for a PID.

    Heuristics are mutually exclusive where they describe the same root
    cause: if range-shift fires, the constant-then-change check is skipped
    to avoid duplicate anomalies for a single phenomenon.
    """
    if not values:
        return []

    u = _UNIT_SYMBOLS.get(unit, f" {unit}")  # human-readable unit suffix
    anomalies: List[str] = []
    mean = statistics.mean(values)
    range_shift_fired = False

    # --- Range shift: first value differs from mean by > 2 std deviations ---
    if len(values) >= 3:
        stdev = statistics.stdev(values)
        if stdev > 0 and abs(values[0] - mean) > 2 * stdev:
            rest_mean = round(statistics.mean(values[1:]), 2)
            anomalies.append(
                f"{pid}: initial={values[0]}{u}, "
                f"stabilized to {rest_mean}{u} after first sample"
            )
            range_shift_fired = True

    # --- Out-of-range: any value outside typical operating range -----------
    bounds = _OPERATING_RANGES.get(pid)
    if bounds is not None:
        lo, hi = bounds
        for v in values:
            if v < lo or v > hi:
                anomalies.append(
                    f"{pid}: value {v}{u} outside typical range "
                    f"[{lo}, {hi}]"
                )
                break  # report once per PID

    # --- Constant-then-change: constant for >=90% of readings then shifts --
    # Skipped if range-shift already fired (same root cause).
    if not range_shift_fired and len(values) >= 5:
        (mode_val, mode_count), = Counter(values).most_common(1)
        if mode_count >= 0.9 * len(values) and mode_count < len(values):
            outliers = [v for v in values if v != mode_val]
            if outliers:
                anomalies.append(
                    f"{pid}: predominantly {mode_val}{u} "
                    f"({mode_count}/{len(values)} samples), "
                    f"exceptions: {sorted(set(outliers))}"
                )

    return anomalies


def summarize_snapshots(snapshots: List[OBDSnapshot]) -> LogSummary:
    """Aggregate a list of OBDSnapshots into a single compact summary.

    Parameters
    ----------
    snapshots:
        Non-empty list of OBDSnapshot objects (e.g. from ``log_file_to_snapshots``).

    Returns
    -------
    LogSummary with aggregated PID stats, deduped DTCs, time range, and anomalies.
    """
    if not snapshots:
        raise ValueError("Cannot summarize an empty snapshot list")

    first = snapshots[0]

    # --- vehicle / adapter meta -------------------------------------------
    vehicle_id = first.vehicle_id
    adapter = first.adapter.type

    # --- time range -------------------------------------------------------
    all_ts = [s.ts for s in snapshots]
    start_ts = min(all_ts)
    end_ts = max(all_ts)
    duration = int((end_ts - start_ts).total_seconds())

    time_range = TimeRange(
        start=start_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=end_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_seconds=duration,
        sample_count=len(snapshots),
    )

    # --- DTC codes (deduplicated, insertion-ordered) -----------------------
    seen_dtc: Dict[str, None] = {}
    for snap in snapshots:
        for dtc in snap.dtc:
            seen_dtc.setdefault(dtc.code, None)
    dtc_codes: List[str] = list(seen_dtc)

    # --- PID summary (8 critical PIDs only) -------------------------------
    pid_summary: Dict[str, PIDStatModel] = {}
    all_anomalies: List[str] = []

    for pid in CRITICAL_PIDS:
        values, unit = _collect_pid_values(snapshots, pid)
        if not values:
            continue

        pid_summary[pid] = PIDStatModel(
            min=round(min(values), 2),
            max=round(max(values), 2),
            mean=round(statistics.mean(values), 2),
            latest=round(values[-1], 2),
            unit=unit,
        )

        all_anomalies.extend(_detect_anomalies(pid, values, unit))

    return LogSummary(
        vehicle_id=vehicle_id,
        adapter=adapter,
        time_range=time_range,
        dtc_codes=dtc_codes,
        pid_summary=pid_summary,
        anomalies=all_anomalies,
    )


def summarize_log_file(
    path: str | Path,
    *,
    vehicle_id: Optional[str] = None,
    adapter_port: str = "log-replay",
) -> LogSummary:
    """Convenience wrapper: parse a TSV log file and return its summary."""
    snapshots = log_file_to_snapshots(
        path, vehicle_id=vehicle_id, adapter_port=adapter_port,
    )
    return summarize_snapshots(snapshots)

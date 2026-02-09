"""Convert parsed OBD-II log rows into a uniformly-sampled pandas DataFrame.

This module provides a **parallel path** alongside the existing
``OBDSnapshot`` pipeline.  It takes the ``List[Dict[str, str]]`` output of
:func:`log_parser.parse_log_file` and produces a :class:`NormalizedTimeSeries`
containing a pandas DataFrame with:

* A uniform ``DatetimeIndex`` (UTC) at a configurable interval
* Semantic (snake_case) column names instead of raw PID names
* Proper numeric conversion with ``NaN`` for missing / non-numeric values

**PID-to-semantic-name mapping (32 PIDs):**

==============================  ==========================  =======
PID name                        Semantic name               Unit
==============================  ==========================  =======
RPM                             engine_rpm                  rpm
SPEED                           vehicle_speed               km/h
THROTTLE_POS                    throttle_position           percent
THROTTLE_POS_B                  throttle_position_b         percent
ENGINE_LOAD                     engine_load                 percent
ABSOLUTE_LOAD                   absolute_load               percent
RELATIVE_THROTTLE_POS           relative_throttle_pos       percent
THROTTLE_ACTUATOR               throttle_actuator           percent
COOLANT_TEMP                    coolant_temperature         degC
INTAKE_TEMP                     intake_temperature          degC
CATALYST_TEMP_B1S1              catalyst_temp_b1s1          degC
MAF                             mass_airflow                g/s
INTAKE_PRESSURE                 intake_pressure             kPa
BAROMETRIC_PRESSURE             barometric_pressure         kPa
FUEL_RAIL_PRESSURE_DIRECT       fuel_rail_pressure_direct   kPa
SHORT_FUEL_TRIM_1               short_fuel_trim_1           percent
LONG_FUEL_TRIM_1                long_fuel_trim_1            percent
TIMING_ADVANCE                  timing_advance              degree
O2_B1S2                         o2_b1s2                     volt
O2_S1_WR_CURRENT                o2_s1_wr_current            mA
EGR_ERROR                       egr_error                   percent
COMMANDED_EGR                   commanded_egr               percent
EVAPORATIVE_PURGE               evaporative_purge           percent
RUN_TIME                        run_time                    second
WARMUPS_SINCE_DTC_CLEAR         warmups_since_dtc_clear     count
DISTANCE_W_MIL                  distance_w_mil              km
DISTANCE_SINCE_DTC_CLEAR        distance_since_dtc_clear    km
CONTROL_MODULE_VOLTAGE          control_module_voltage      volt
ELM_VOLTAGE                     elm_voltage                 volt
ACCELERATOR_POS_D               accelerator_pos_d           percent
ACCELERATOR_POS_E               accelerator_pos_e           percent
COMMANDED_EQUIV_RATIO           commanded_equiv_ratio       ratio
==============================  ==========================  =======

Downstream consumers (APP-14 statistics, APP-15 anomaly detection, APP-16 clue
generation) depend on this mapping being stable.

.. todo:: APP-16 cleanup
   Once APP-16 (clue generation) is complete, retire the legacy
   ``parse_log_file() → row_to_snapshot() → List[OBDSnapshot] → LogSummary``
   path in ``log_parser.py`` / ``log_summarizer.py`` and make this
   ``NormalizedTimeSeries`` path the sole pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

from obd_agent.log_parser import (
    _PID_UNITS,
    _extract_vin,
    _parse_dtc_list,
    parse_log_file,
    pseudonymise_vin,
)

# ---------------------------------------------------------------------------
# PID <-> semantic name mappings
# ---------------------------------------------------------------------------

_PID_SEMANTIC_NAMES: Dict[str, str] = {
    "RPM": "engine_rpm",
    "SPEED": "vehicle_speed",
    "THROTTLE_POS": "throttle_position",
    "THROTTLE_POS_B": "throttle_position_b",
    "ENGINE_LOAD": "engine_load",
    "ABSOLUTE_LOAD": "absolute_load",
    "RELATIVE_THROTTLE_POS": "relative_throttle_pos",
    "THROTTLE_ACTUATOR": "throttle_actuator",
    "COOLANT_TEMP": "coolant_temperature",
    "INTAKE_TEMP": "intake_temperature",
    "CATALYST_TEMP_B1S1": "catalyst_temp_b1s1",
    "MAF": "mass_airflow",
    "INTAKE_PRESSURE": "intake_pressure",
    "BAROMETRIC_PRESSURE": "barometric_pressure",
    "FUEL_RAIL_PRESSURE_DIRECT": "fuel_rail_pressure_direct",
    "SHORT_FUEL_TRIM_1": "short_fuel_trim_1",
    "LONG_FUEL_TRIM_1": "long_fuel_trim_1",
    "TIMING_ADVANCE": "timing_advance",
    "O2_B1S2": "o2_b1s2",
    "O2_S1_WR_CURRENT": "o2_s1_wr_current",
    "EGR_ERROR": "egr_error",
    "COMMANDED_EGR": "commanded_egr",
    "EVAPORATIVE_PURGE": "evaporative_purge",
    "RUN_TIME": "run_time",
    "WARMUPS_SINCE_DTC_CLEAR": "warmups_since_dtc_clear",
    "DISTANCE_W_MIL": "distance_w_mil",
    "DISTANCE_SINCE_DTC_CLEAR": "distance_since_dtc_clear",
    "CONTROL_MODULE_VOLTAGE": "control_module_voltage",
    "ELM_VOLTAGE": "elm_voltage",
    "ACCELERATOR_POS_D": "accelerator_pos_d",
    "ACCELERATOR_POS_E": "accelerator_pos_e",
    "COMMANDED_EQUIV_RATIO": "commanded_equiv_ratio",
}

_SEMANTIC_TO_PID: Dict[str, str] = {v: k for k, v in _PID_SEMANTIC_NAMES.items()}

_SEMANTIC_UNITS: Dict[str, str] = {
    _PID_SEMANTIC_NAMES[pid]: unit for pid, unit in _PID_UNITS.items()
}

FillMethod = Literal["interpolate", "ffill", "bfill", "none"]

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedTimeSeries:
    """Uniformly-sampled time-series DataFrame with metadata.

    Attributes
    ----------
    df : pd.DataFrame
        DatetimeIndex (UTC), columns are semantic snake_case PID names.
    vehicle_id : str
        Pseudonymised vehicle identifier (``V-XXXXXXXX``).
    time_range : tuple[datetime, datetime]
        ``(start, end)`` of the resampled index, both UTC.
    dtc_codes : list[str]
        Deduplicated DTC codes found across all rows.
    column_units : dict[str, str]
        Semantic column name -> engineering unit string.
    column_pid_names : dict[str, str]
        Semantic column name -> original PID name.
    resample_interval_seconds : float
        The uniform grid spacing in seconds.
    fill_method : str
        Name of the fill strategy used during resampling.
    original_sample_count : int
        Number of rows in the input *before* resampling.
    """

    df: pd.DataFrame
    vehicle_id: str
    time_range: Tuple[datetime, datetime]
    dtc_codes: List[str]
    column_units: Dict[str, str]
    column_pid_names: Dict[str, str]
    resample_interval_seconds: float
    fill_method: str
    original_sample_count: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rows_to_raw_dataframe(rows: List[Dict[str, str]]) -> pd.DataFrame:
    """Convert parsed log rows to a DataFrame with DatetimeIndex and semantic columns.

    * Parses ``Timestamp`` into a UTC ``DatetimeIndex``.
    * Renames PID columns to semantic snake_case names.
    * Converts all PID columns to numeric (``NaN`` for non-numeric values).
    * Duplicate timestamps are averaged.
    """
    timestamps = []
    data_rows: List[Dict[str, object]] = []

    for row in rows:
        ts_raw = row.get("Timestamp", "")
        try:
            ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue  # skip rows with unparseable timestamps

        record: Dict[str, object] = {}
        for pid, semantic in _PID_SEMANTIC_NAMES.items():
            raw_val = row.get(pid)
            if raw_val is not None:
                record[semantic] = raw_val
        timestamps.append(ts)
        data_rows.append(record)

    df = pd.DataFrame(data_rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))

    # Ensure all semantic columns exist (fill missing with NaN).
    for semantic in _PID_SEMANTIC_NAMES.values():
        if semantic not in df.columns:
            df[semantic] = np.nan

    # Coerce to numeric.
    df = df.apply(pd.to_numeric, errors="coerce")

    # Handle duplicate timestamps by averaging.
    if df.index.duplicated().any():
        df = df.groupby(df.index).mean()

    # Sort by time.
    df = df.sort_index()

    return df


def _extract_metadata(
    rows: List[Dict[str, str]],
    vehicle_id_override: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Extract pseudonymised vehicle ID and deduplicated DTC codes.

    Returns ``(vehicle_id, dtc_codes)``.
    """
    # Vehicle ID: use override, or pseudonymise from first row's VIN.
    if vehicle_id_override is not None:
        vehicle_id = vehicle_id_override
    else:
        raw_vin = _extract_vin(rows[0].get("VIN", "")) if rows else None
        vehicle_id = pseudonymise_vin(raw_vin) if raw_vin else "V-UNKNOWN"

    # DTC codes: collect from all rows, deduplicate.
    seen: set[str] = set()
    dtc_codes: List[str] = []
    for row in rows:
        for col in ("GET_DTC", "GET_CURRENT_DTC"):
            raw = row.get(col, "[]")
            for code, _desc in _parse_dtc_list(raw):
                code = code.upper()
                if code not in seen:
                    seen.add(code)
                    dtc_codes.append(code)

    return vehicle_id, dtc_codes


def _resample_dataframe(
    df: pd.DataFrame,
    interval_seconds: float,
    fill_method: FillMethod,
) -> pd.DataFrame:
    """Resample *df* onto a uniform time grid.

    Parameters
    ----------
    df : pd.DataFrame
        Input with ``DatetimeIndex`` (UTC).
    interval_seconds : float
        Desired uniform spacing in seconds.
    fill_method : FillMethod
        ``"interpolate"`` — time-weighted linear interpolation,
        ``"ffill"`` / ``"bfill"`` — forward/backward fill,
        ``"none"`` — leave gaps as ``NaN``.
    """
    if df.empty:
        return df

    start = df.index.min()
    end = df.index.max()
    freq = pd.Timedelta(seconds=interval_seconds)
    new_index = pd.date_range(start=start, end=end, freq=freq, name="timestamp")

    if fill_method == "interpolate":
        # Union original + new index, interpolate, then select grid points.
        union_index = df.index.union(new_index).sort_values()
        df_union = df.reindex(union_index)
        df_union = df_union.interpolate(method="time")
        result = df_union.reindex(new_index)
    elif fill_method in ("ffill", "bfill"):
        result = df.reindex(new_index, method=fill_method)
    else:  # "none"
        result = df.reindex(new_index)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_rows(
    rows: List[Dict[str, str]],
    *,
    interval_seconds: float = 1.0,
    fill_method: FillMethod = "interpolate",
    vehicle_id: Optional[str] = None,
) -> NormalizedTimeSeries:
    """Convert parsed OBD log rows into a uniformly-sampled time series.

    Parameters
    ----------
    rows :
        Output of :func:`log_parser.parse_log_file`.
    interval_seconds :
        Desired uniform grid spacing (default ``1.0``s).
    fill_method :
        Strategy for filling gaps: ``"interpolate"``, ``"ffill"``,
        ``"bfill"``, or ``"none"``.
    vehicle_id :
        Override vehicle ID.  If ``None``, derived from the VIN column.

    Returns
    -------
    NormalizedTimeSeries
        Frozen dataclass with the resampled DataFrame and metadata.

    Raises
    ------
    ValueError
        If *rows* is empty or *interval_seconds* is not positive.
    """
    if not rows:
        raise ValueError("Cannot normalise an empty row list.")
    if interval_seconds <= 0:
        raise ValueError(
            f"interval_seconds must be positive, got {interval_seconds}"
        )

    raw_df = _rows_to_raw_dataframe(rows)
    original_count = len(raw_df)
    resampled = _resample_dataframe(raw_df, interval_seconds, fill_method)
    vid, dtc_codes = _extract_metadata(rows, vehicle_id)

    start_dt = resampled.index.min().to_pydatetime()
    end_dt = resampled.index.max().to_pydatetime()

    return NormalizedTimeSeries(
        df=resampled,
        vehicle_id=vid,
        time_range=(start_dt, end_dt),
        dtc_codes=dtc_codes,
        column_units=dict(_SEMANTIC_UNITS),
        column_pid_names=dict(_SEMANTIC_TO_PID),
        resample_interval_seconds=interval_seconds,
        fill_method=fill_method,
        original_sample_count=original_count,
    )


def normalize_log_file(
    path: str | Path,
    *,
    interval_seconds: float = 1.0,
    fill_method: FillMethod = "interpolate",
    vehicle_id: Optional[str] = None,
) -> NormalizedTimeSeries:
    """Parse *path* and normalise into a uniform time series.

    Convenience wrapper: calls :func:`log_parser.parse_log_file` then
    :func:`normalize_rows`.
    """
    rows = parse_log_file(path)
    return normalize_rows(
        rows,
        interval_seconds=interval_seconds,
        fill_method=fill_method,
        vehicle_id=vehicle_id,
    )

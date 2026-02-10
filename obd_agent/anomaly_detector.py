"""Anomaly detection with temporal context for OBD-II time series.

Stage 2 of the OBD-II Diagnostic Summarisation Pipeline (APP-15).  Consumes
:class:`~obd_agent.time_series_normalizer.NormalizedTimeSeries` produced by
APP-13 and yields structured :class:`AnomalyEvent` objects with time windows,
involved signals, driving context, and severity.

Detection methods:

* **Change-point detection** — ``ruptures`` Pelt + rbf kernel per variable
  column; scores each change-point by the magnitude of the level shift
  relative to the signal range.
* **Multivariate outlier detection** — scikit-learn ``IsolationForest``
  on z-score-normalised columns; consecutive outlier rows are grouped into
  windows and the top contributing signals are reported.

Downstream consumers: APP-16 (clue generation), APP-17 (v2 API endpoint).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import ruptures as rpt
from sklearn.ensemble import IsolationForest

from obd_agent.statistics_extractor import SignalStatistics

logger = logging.getLogger(__name__)
from obd_agent.time_series_normalizer import (
    FillMethod,
    NormalizedTimeSeries,
    normalize_log_file,
)

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyEvent:
    """A single detected anomaly with temporal context.

    Attributes
    ----------
    time_window : tuple[datetime, datetime]
        ``(start, end)`` UTC timestamps bounding the anomalous region.
    signals : tuple[str, ...]
        Semantic column names involved in this event.
    pattern : str
        Human-readable description of the anomalous pattern.
    context : str
        Driving context inferred from the window:
        ``"off"`` | ``"idle"`` | ``"cruise"`` | ``"acceleration"`` | ``"unknown"``.
    severity : str
        ``"low"`` | ``"medium"`` | ``"high"``.
    detector : str
        ``"changepoint"`` | ``"isolation_forest"`` | ``"combined"``.
    score : float
        Composite anomaly score in ``[0.0, 1.0]``.
    """

    time_window: Tuple[datetime, datetime]
    signals: Tuple[str, ...]
    pattern: str
    context: str
    severity: str
    detector: str
    score: float


@dataclass(frozen=True)
class AnomalyReport:
    """Collection of anomaly events with session metadata.

    Attributes
    ----------
    events : tuple[AnomalyEvent, ...]
        Detected anomalies, sorted by start time.
    vehicle_id : str
        Pseudonymised vehicle identifier.
    time_range : tuple[datetime, datetime]
        ``(start, end)`` of the analysed time series.
    dtc_codes : list[str]
        DTC codes present in the session.
    detection_params : dict[str, Any]
        Parameters used for detection (for reproducibility).
    """

    events: Tuple[AnomalyEvent, ...]
    vehicle_id: str
    time_range: Tuple[datetime, datetime]
    dtc_codes: List[str]
    detection_params: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for ``json.dumps``.

        * ``datetime`` values are converted to ISO-8601 strings.
        * ``tuple`` values are converted to lists.
        """
        events_out: List[Dict[str, Any]] = []
        for ev in self.events:
            d = asdict(ev)
            d["time_window"] = [
                ev.time_window[0].isoformat(),
                ev.time_window[1].isoformat(),
            ]
            d["signals"] = list(ev.signals)
            events_out.append(d)

        return {
            "events": events_out,
            "vehicle_id": self.vehicle_id,
            "time_range": [
                self.time_range[0].isoformat(),
                self.time_range[1].isoformat(),
            ],
            "dtc_codes": list(self.dtc_codes),
            "detection_params": dict(self.detection_params),
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CRITICAL_SIGNALS: Tuple[str, ...] = (
    "engine_rpm",
    "vehicle_speed",
    "coolant_temperature",
    "short_fuel_trim_1",
    "long_fuel_trim_1",
    "engine_load",
    "throttle_position",
    "mass_airflow",
)

_MIN_ROWS_CHANGEPOINT = 20
_MIN_ROWS_ISOLATION_FOREST = 30

_SEVERITY_THRESHOLDS = (0.33, 0.66)

# Driving-context thresholds
_RPM_OFF = 50
_SPEED_MOVING = 5
_THROTTLE_CRUISE_STD = 3.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_variable_columns(df: pd.DataFrame) -> List[str]:
    """Return column names that are neither constant nor all-NaN."""
    cols: List[str] = []
    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue  # all-NaN
        if series.nunique() <= 1:
            continue  # constant
        cols.append(col)
    return cols


def _infer_driving_context(df_window: pd.DataFrame) -> str:
    """Classify a time window into a driving context label.

    Returns one of ``"off"``, ``"idle"``, ``"cruise"``, ``"acceleration"``,
    ``"unknown"``.
    """
    rpm = df_window.get("engine_rpm")
    speed = df_window.get("vehicle_speed")
    throttle = df_window.get("throttle_position")

    if rpm is None or speed is None:
        return "unknown"

    rpm_vals = rpm.dropna()
    speed_vals = speed.dropna()

    if len(rpm_vals) == 0 or len(speed_vals) == 0:
        return "unknown"

    mean_rpm = float(rpm_vals.mean())
    mean_speed = float(speed_vals.mean())

    # Engine off
    if mean_rpm < _RPM_OFF:
        return "off"

    # Not moving → idle
    if mean_speed < _SPEED_MOVING:
        return "idle"

    # Moving — check throttle stability for cruise vs acceleration
    if throttle is not None:
        throttle_vals = throttle.dropna()
        if len(throttle_vals) >= 2:
            throttle_std = float(throttle_vals.std(ddof=0))
            if throttle_std <= _THROTTLE_CRUISE_STD:
                return "cruise"
            return "acceleration"

    return "unknown"


def _compute_severity(
    n_signals: int,
    score: float,
    duration_seconds: float,
    has_critical: bool,
) -> str:
    """Compute severity tier from weighted composite score.

    Weights: 40% score, 25% signal count, 15% duration, 20% criticality.
    """
    # Normalise components to [0, 1]
    score_norm = max(0.0, min(1.0, score))
    signal_norm = min(1.0, n_signals / 8.0)
    duration_norm = min(1.0, duration_seconds / 300.0)  # cap at 5 min
    critical_norm = 1.0 if has_critical else 0.0

    composite = (
        0.40 * score_norm
        + 0.25 * signal_norm
        + 0.15 * duration_norm
        + 0.20 * critical_norm
    )
    composite = max(0.0, min(1.0, composite))

    if composite >= _SEVERITY_THRESHOLDS[1]:
        return "high"
    if composite >= _SEVERITY_THRESHOLDS[0]:
        return "medium"
    return "low"


def _find_contiguous_runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """Convert a boolean array into a list of ``(start, end)`` index pairs.

    Each pair denotes a contiguous run of ``True`` values.  ``end`` is
    inclusive.
    """
    if len(mask) == 0:
        return []

    runs: List[Tuple[int, int]] = []
    in_run = False
    start = 0

    for i, val in enumerate(mask):
        if val and not in_run:
            start = i
            in_run = True
        elif not val and in_run:
            runs.append((start, i - 1))
            in_run = False

    if in_run:
        runs.append((start, len(mask) - 1))

    return runs


def _detect_changepoints(
    df: pd.DataFrame,
    columns: List[str],
    min_segment_length: int,
    pen: float = 3.0,
) -> List[AnomalyEvent]:
    """Run ruptures Pelt on each variable column and emit events.

    For each detected change-point, a small window around the break is
    created.  The anomaly score is the magnitude of the level shift divided
    by the signal's total range.
    """
    if len(df) < _MIN_ROWS_CHANGEPOINT:
        return []

    events: List[AnomalyEvent] = []
    index = df.index

    for col in columns:
        series = df[col].values.copy()
        # Skip columns with too many NaNs
        valid_mask = ~np.isnan(series)
        if valid_mask.sum() < _MIN_ROWS_CHANGEPOINT:
            continue

        # Fill NaN for ruptures (it needs contiguous data)
        filled = pd.Series(series).ffill().bfill().values
        signal_range = float(np.nanmax(filled) - np.nanmin(filled))
        if signal_range == 0:
            continue

        algo = rpt.Pelt(model="rbf", min_size=min_segment_length).fit(filled)
        try:
            breakpoints = algo.predict(pen=pen)
        except Exception:
            logger.warning("Changepoint detection failed for column %s", col)
            continue

        # ruptures returns breakpoints as 1-indexed positions including n
        breakpoints = [bp for bp in breakpoints if bp < len(df)]
        if not breakpoints:
            continue

        for bp in breakpoints:
            # Window around change-point
            half_window = max(min_segment_length // 2, 2)
            w_start = max(0, bp - half_window)
            w_end = min(len(df) - 1, bp + half_window - 1)

            # Level shift magnitude
            left = filled[max(0, bp - half_window) : bp]
            right = filled[bp : min(len(df), bp + half_window)]
            if len(left) == 0 or len(right) == 0:
                continue

            shift = abs(float(np.mean(right)) - float(np.mean(left)))
            score = min(1.0, shift / signal_range)

            start_time = index[w_start].to_pydatetime()
            end_time = index[w_end].to_pydatetime()
            # Ensure timezone-aware
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)

            duration = (end_time - start_time).total_seconds()
            window_df = df.iloc[w_start : w_end + 1]
            context = _infer_driving_context(window_df)

            has_critical = col in _CRITICAL_SIGNALS
            severity = _compute_severity(1, score, duration, has_critical)

            pattern = (
                f"Change-point in {col}: level shift of "
                f"{shift:.2f} (score {score:.2f})"
            )

            events.append(
                AnomalyEvent(
                    time_window=(start_time, end_time),
                    signals=(col,),
                    pattern=pattern,
                    context=context,
                    severity=severity,
                    detector="changepoint",
                    score=score,
                )
            )

    return events


def _detect_multivariate_outliers(
    df: pd.DataFrame,
    columns: List[str],
    contamination: float,
) -> List[AnomalyEvent]:
    """Z-score normalise, run Isolation Forest, group consecutive outliers.

    For each outlier window the top-5 contributing signals (by absolute
    z-score deviation) are reported.
    """
    if len(df) < _MIN_ROWS_ISOLATION_FOREST:
        return []

    if len(columns) < 2:
        return []

    # Build matrix with NaN filled
    mat = df[columns].copy()
    mat = mat.ffill().bfill()
    # Drop any remaining all-NaN columns
    mat = mat.dropna(axis=1, how="all")
    if mat.shape[1] < 2:
        return []

    used_cols = list(mat.columns)

    # Z-score normalisation
    means = mat.mean()
    stds = mat.std(ddof=0)
    stds = stds.replace(0, 1)  # avoid division by zero
    z_scores = (mat - means) / stds

    # Isolation Forest
    iso = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_estimators=100,
    )
    labels = iso.fit_predict(z_scores.values)
    outlier_mask = labels == -1

    if not outlier_mask.any():
        return []

    # Group consecutive outlier rows into windows
    runs = _find_contiguous_runs(outlier_mask)
    index = df.index
    events: List[AnomalyEvent] = []

    for run_start, run_end in runs:
        window_z = z_scores.iloc[run_start : run_end + 1]
        # Top contributing signals by mean absolute z-score
        mean_abs_z = window_z.abs().mean()
        top_signals = mean_abs_z.nlargest(min(5, len(used_cols))).index.tolist()

        start_time = index[run_start].to_pydatetime()
        end_time = index[run_end].to_pydatetime()
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        duration = (end_time - start_time).total_seconds()
        window_df = df.iloc[run_start : run_end + 1]
        context = _infer_driving_context(window_df)

        # Score: mean of decision_function scores for outlier rows, normalised
        raw_scores = iso.decision_function(z_scores.iloc[run_start : run_end + 1].values)
        # decision_function: lower = more anomalous; normalise to [0, 1]
        score = float(np.clip(-np.mean(raw_scores), 0.0, 1.0))

        has_critical = any(s in _CRITICAL_SIGNALS for s in top_signals)
        severity = _compute_severity(
            len(top_signals), score, duration, has_critical,
        )

        pattern = (
            f"Multivariate outlier ({run_end - run_start + 1} rows): "
            f"top signals {', '.join(top_signals)}"
        )

        events.append(
            AnomalyEvent(
                time_window=(start_time, end_time),
                signals=tuple(top_signals),
                pattern=pattern,
                context=context,
                severity=severity,
                detector="isolation_forest",
                score=score,
            )
        )

    return events


def _merge_overlapping_events(
    events: List[AnomalyEvent],
) -> List[AnomalyEvent]:
    """Merge time-overlapping events.

    * Union of signals.
    * Severity recomputed from merged attributes.
    * ``detector="combined"`` if sources differ.
    * Scores are averaged.
    """
    if len(events) <= 1:
        return events

    # Sort by start time
    sorted_events = sorted(events, key=lambda e: e.time_window[0])
    merged: List[AnomalyEvent] = []

    current = sorted_events[0]
    for nxt in sorted_events[1:]:
        # Check overlap
        if nxt.time_window[0] <= current.time_window[1]:
            # Merge
            new_start = min(current.time_window[0], nxt.time_window[0])
            new_end = max(current.time_window[1], nxt.time_window[1])
            new_signals = tuple(
                dict.fromkeys(current.signals + nxt.signals)
            )
            new_score = (current.score + nxt.score) / 2.0

            # Detector label
            if current.detector != nxt.detector:
                new_detector = "combined"
            else:
                new_detector = current.detector

            new_pattern = f"{current.pattern}; {nxt.pattern}"

            duration = (new_end - new_start).total_seconds()
            has_critical = any(s in _CRITICAL_SIGNALS for s in new_signals)
            new_severity = _compute_severity(
                len(new_signals), new_score, duration, has_critical,
            )

            current = AnomalyEvent(
                time_window=(new_start, new_end),
                signals=new_signals,
                pattern=new_pattern,
                context=current.context,
                severity=new_severity,
                detector=new_detector,
                score=new_score,
            )
        else:
            merged.append(current)
            current = nxt

    merged.append(current)
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_anomalies(
    ts: NormalizedTimeSeries,
    *,
    stats: Optional[SignalStatistics] = None,
    min_segment_length: int = 10,
    contamination: float = 0.05,
    pen: float = 3.0,
) -> AnomalyReport:
    """Detect anomalies in a normalised OBD-II time series.

    Parameters
    ----------
    ts :
        Output of :func:`~obd_agent.time_series_normalizer.normalize_rows`
        or :func:`~obd_agent.time_series_normalizer.normalize_log_file`.
    stats :
        Optional pre-computed :class:`SignalStatistics` (currently unused,
        reserved for future severity refinement).
    min_segment_length :
        Minimum segment length for ``ruptures`` Pelt (must be >= 2).
    contamination :
        Expected proportion of outliers for Isolation Forest.
        Must be in ``(0, 0.5]``.
    pen :
        Penalty parameter for ``ruptures`` Pelt (default ``3.0``).

    Returns
    -------
    AnomalyReport
        Frozen dataclass with detected events and session metadata.

    Raises
    ------
    ValueError
        If *contamination* or *min_segment_length* is out of range.
    """
    if not (0 < contamination <= 0.5):
        raise ValueError(
            f"contamination must be in (0, 0.5], got {contamination}"
        )
    if min_segment_length < 2:
        raise ValueError(
            f"min_segment_length must be >= 2, got {min_segment_length}"
        )

    detection_params: Dict[str, Any] = {
        "min_segment_length": min_segment_length,
        "contamination": contamination,
        "pen": pen,
    }

    df = ts.df
    # Empty / too-few-rows → empty report
    if df.empty or len(df) < _MIN_ROWS_CHANGEPOINT:
        return AnomalyReport(
            events=(),
            vehicle_id=ts.vehicle_id,
            time_range=ts.time_range,
            dtc_codes=list(ts.dtc_codes),
            detection_params=detection_params,
        )

    # Filter to variable columns only
    columns = _filter_variable_columns(df)
    if not columns:
        return AnomalyReport(
            events=(),
            vehicle_id=ts.vehicle_id,
            time_range=ts.time_range,
            dtc_codes=list(ts.dtc_codes),
            detection_params=detection_params,
        )

    # Run detectors
    cp_events = _detect_changepoints(df, columns, min_segment_length, pen=pen)
    if_events = _detect_multivariate_outliers(df, columns, contamination)

    # Merge overlapping events
    all_events = _merge_overlapping_events(cp_events + if_events)

    # Sort by start time
    all_events.sort(key=lambda e: e.time_window[0])

    return AnomalyReport(
        events=tuple(all_events),
        vehicle_id=ts.vehicle_id,
        time_range=ts.time_range,
        dtc_codes=list(ts.dtc_codes),
        detection_params=detection_params,
    )


def detect_anomalies_from_log_file(
    path: str | Path,
    *,
    interval_seconds: float = 1.0,
    fill_method: FillMethod = "interpolate",
    vehicle_id: Optional[str] = None,
    min_segment_length: int = 10,
    contamination: float = 0.05,
    pen: float = 3.0,
) -> AnomalyReport:
    """Parse an OBD log file and detect anomalies.

    Convenience wrapper that chains
    :func:`~obd_agent.time_series_normalizer.normalize_log_file` with
    :func:`detect_anomalies`.
    """
    ts = normalize_log_file(
        path,
        interval_seconds=interval_seconds,
        fill_method=fill_method,
        vehicle_id=vehicle_id,
    )
    return detect_anomalies(
        ts,
        min_segment_length=min_segment_length,
        contamination=contamination,
        pen=pen,
    )

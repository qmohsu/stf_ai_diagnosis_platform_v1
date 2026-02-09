"""Extract per-signal statistical profiles from normalised OBD-II time series.

Stage 1 of the OBD-II Diagnostic Summarisation Pipeline (APP-14).  Consumes
:class:`~obd_agent.time_series_normalizer.NormalizedTimeSeries` produced by
APP-13 and yields a :class:`SignalStatistics` object containing descriptive
stats, percentiles, temporal dynamics, and signal characterisation for every
non-all-NaN column.

No new dependencies — everything is computed with **pandas** + **numpy**.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from obd_agent.time_series_normalizer import (
    FillMethod,
    NormalizedTimeSeries,
    normalize_log_file,
)

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalStats:
    """Statistical profile for a single signal (column).

    All float fields may be ``NaN`` when the computation is undefined
    (e.g. autocorrelation on fewer than 3 observations).
    """

    # Descriptive
    mean: float
    std: float  # population std (ddof=0)
    min: float
    max: float
    # Percentiles
    p5: float
    p25: float
    p50: float  # median
    p75: float
    p95: float
    # Temporal dynamics
    autocorrelation_lag1: float  # NaN if n < 3 or zero variance
    mean_abs_change: float  # NaN if n < 2
    max_abs_change: float  # NaN if n < 2
    # Signal characterisation
    energy: float  # sum(x^2) / n
    entropy: float  # Shannon entropy, 10-bin histogram; NaN if n < 2
    # Count
    valid_count: int


@dataclass(frozen=True)
class SignalStatistics:
    """Aggregated statistics for all signals in a normalised time series."""

    stats: Dict[str, SignalStats]  # semantic_name -> SignalStats
    vehicle_id: str
    time_range: Tuple[datetime, datetime]
    dtc_codes: List[str]
    column_units: Dict[str, str]
    resample_interval_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding.

        * ``NaN`` values are replaced with ``None``.
        * ``time_range`` datetimes are converted to ISO-8601 strings.
        """
        stats_out: Dict[str, Any] = {}
        for name, ss in self.stats.items():
            stats_out[name] = {
                k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
                for k, v in asdict(ss).items()
            }

        return {
            "stats": stats_out,
            "vehicle_id": self.vehicle_id,
            "time_range": [
                self.time_range[0].isoformat(),
                self.time_range[1].isoformat(),
            ],
            "dtc_codes": list(self.dtc_codes),
            "column_units": dict(self.column_units),
            "resample_interval_seconds": self.resample_interval_seconds,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _autocorrelation_lag1(values: np.ndarray) -> float:
    """Pearson correlation between x[t] and x[t+1].

    Returns ``NaN`` if *n* < 3 or the signal has zero variance.
    """
    n = len(values)
    if n < 3:
        return float("nan")
    x = values[:-1]
    y = values[1:]
    x_mean = x.mean()
    y_mean = y.mean()
    x_std = x.std(ddof=0)
    y_std = y.std(ddof=0)
    if x_std == 0.0 or y_std == 0.0:
        return float("nan")
    cov = ((x - x_mean) * (y - y_mean)).mean()
    return float(cov / (x_std * y_std))


def _shannon_entropy(values: np.ndarray, n_bins: int = 10) -> float:
    """Histogram-based Shannon entropy in bits.

    Returns ``NaN`` if *n* < 2.  Returns ``0.0`` for a constant signal.
    """
    n = len(values)
    if n < 2:
        return float("nan")
    if values.min() == values.max():
        return 0.0
    counts, _ = np.histogram(values, bins=n_bins)
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


def _compute_signal_stats(
    values: np.ndarray,
    *,
    n_bins: int = 10,
    decimal_places: int = 4,
) -> SignalStats:
    """Compute all 15 statistical fields from a 1-D numpy array.

    *values* must contain no NaN entries (caller must drop them beforehand).

    Raises :class:`ValueError` if *values* is empty.
    """
    n = len(values)
    if n == 0:
        raise ValueError("Cannot compute statistics on an empty array.")
    if __debug__:
        assert not np.any(np.isnan(values)), "values must not contain NaN"

    def _r(v: float) -> float:
        return round(v, decimal_places)

    mean = _r(float(values.mean()))
    std = _r(float(values.std(ddof=0)))
    mn = _r(float(values.min()))
    mx = _r(float(values.max()))

    p5, p25, p50, p75, p95 = (
        _r(float(v))
        for v in np.percentile(values, [5, 25, 50, 75, 95])
    )

    ac = _autocorrelation_lag1(values)
    ac = _r(ac) if not math.isnan(ac) else float("nan")

    if n >= 2:
        diffs = np.abs(np.diff(values))
        mac = _r(float(diffs.mean()))
        mxc = _r(float(diffs.max()))
    else:
        mac = float("nan")
        mxc = float("nan")

    energy = _r(float(np.sum(values**2) / n))

    ent = _shannon_entropy(values, n_bins=n_bins)
    ent = _r(ent) if not math.isnan(ent) else float("nan")

    return SignalStats(
        mean=mean,
        std=std,
        min=mn,
        max=mx,
        p5=p5,
        p25=p25,
        p50=p50,
        p75=p75,
        p95=p95,
        autocorrelation_lag1=ac,
        mean_abs_change=mac,
        max_abs_change=mxc,
        energy=energy,
        entropy=ent,
        valid_count=n,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_statistics(ts: NormalizedTimeSeries) -> SignalStatistics:
    """Compute per-signal statistics from a normalised time series.

    Parameters
    ----------
    ts :
        Output of :func:`~obd_agent.time_series_normalizer.normalize_rows`
        or :func:`~obd_agent.time_series_normalizer.normalize_log_file`.

    Returns
    -------
    SignalStatistics
        Frozen dataclass with a ``stats`` dict mapping each non-all-NaN
        semantic column name to its :class:`SignalStats`.

    Raises
    ------
    ValueError
        If *ts.df* is empty (zero rows).
    """
    df = ts.df
    if df.empty:
        raise ValueError("Cannot extract statistics from an empty DataFrame.")

    stats: Dict[str, SignalStats] = {}
    for col in df.columns:
        series = df[col].dropna().to_numpy()
        if len(series) == 0:
            continue  # skip all-NaN columns
        stats[col] = _compute_signal_stats(series)

    return SignalStatistics(
        stats=stats,
        vehicle_id=ts.vehicle_id,
        time_range=ts.time_range,
        dtc_codes=list(ts.dtc_codes),
        column_units={k: v for k, v in ts.column_units.items() if k in stats},
        resample_interval_seconds=ts.resample_interval_seconds,
    )


def extract_statistics_from_log_file(
    path: str | Path,
    *,
    interval_seconds: float = 1.0,
    fill_method: FillMethod = "interpolate",
    vehicle_id: Optional[str] = None,
) -> SignalStatistics:
    """Parse an OBD log file and extract per-signal statistics.

    Convenience wrapper that chains
    :func:`~obd_agent.time_series_normalizer.normalize_log_file` with
    :func:`extract_statistics`.

    Parameters
    ----------
    path :
        Path to a raw OBD TSV log file.
    interval_seconds :
        Desired uniform grid spacing (default ``1.0`` s).
    fill_method :
        Gap-filling strategy (``"interpolate"``, ``"ffill"``, ``"bfill"``,
        ``"none"``).
    vehicle_id :
        Override vehicle ID.  If ``None``, derived from VIN column.
    """
    ts = normalize_log_file(
        path,
        interval_seconds=interval_seconds,
        fill_method=fill_method,
        vehicle_id=vehicle_id,
    )
    return extract_statistics(ts)

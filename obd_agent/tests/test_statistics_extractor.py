"""Tests for the OBD per-signal statistics extractor (APP-14)."""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import pytest

from obd_agent.log_parser import _PID_UNITS, parse_log_file
from obd_agent.statistics_extractor import (
    SignalStatistics,
    SignalStats,
    _autocorrelation_lag1,
    _compute_signal_stats,
    _shannon_entropy,
    extract_statistics,
    extract_statistics_from_log_file,
)
from obd_agent.time_series_normalizer import (
    NormalizedTimeSeries,
    normalize_log_file,
    normalize_rows,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_REAL_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ts(
    data: Dict[str, list],
    *,
    freq: str = "1s",
    vehicle_id: str = "V-TEST",
    dtc_codes: list | None = None,
) -> NormalizedTimeSeries:
    """Build a minimal NormalizedTimeSeries for testing."""
    n = len(next(iter(data.values())))
    idx = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame(data, index=idx)
    df.index.name = "timestamp"
    start = df.index[0].to_pydatetime()
    end = df.index[-1].to_pydatetime()
    return NormalizedTimeSeries(
        df=df,
        vehicle_id=vehicle_id,
        time_range=(start, end),
        dtc_codes=dtc_codes or [],
        column_units={c: "unit" for c in df.columns},
        column_pid_names={c: c.upper() for c in df.columns},
        resample_interval_seconds=1.0,
        fill_method="interpolate",
        original_sample_count=n,
    )


# ---------------------------------------------------------------------------
# SignalStats dataclass
# ---------------------------------------------------------------------------


class TestSignalStatsDataclass:
    """Verify SignalStats is frozen, has 15 fields, and accepts NaN."""

    def test_frozen(self) -> None:
        ss = SignalStats(
            mean=0, std=0, min=0, max=0,
            p5=0, p25=0, p50=0, p75=0, p95=0,
            autocorrelation_lag1=0, mean_abs_change=0, max_abs_change=0,
            energy=0, entropy=0, valid_count=1,
        )
        with pytest.raises(FrozenInstanceError):
            ss.mean = 99  # type: ignore[misc]

    def test_field_count(self) -> None:
        assert len(fields(SignalStats)) == 15

    def test_nan_allowed(self) -> None:
        ss = SignalStats(
            mean=1.0, std=0.0, min=1.0, max=1.0,
            p5=1.0, p25=1.0, p50=1.0, p75=1.0, p95=1.0,
            autocorrelation_lag1=float("nan"),
            mean_abs_change=float("nan"),
            max_abs_change=float("nan"),
            energy=1.0,
            entropy=float("nan"),
            valid_count=1,
        )
        assert math.isnan(ss.autocorrelation_lag1)
        assert math.isnan(ss.mean_abs_change)
        assert math.isnan(ss.entropy)


# ---------------------------------------------------------------------------
# SignalStatistics dataclass
# ---------------------------------------------------------------------------


class TestSignalStatisticsDataclass:
    """Verify SignalStatistics is frozen, to_dict works, NaN -> None."""

    @pytest.fixture()
    def sample(self) -> SignalStatistics:
        ss = SignalStats(
            mean=1.0, std=0.5, min=0.0, max=2.0,
            p5=0.1, p25=0.5, p50=1.0, p75=1.5, p95=1.9,
            autocorrelation_lag1=0.8,
            mean_abs_change=0.3,
            max_abs_change=1.0,
            energy=1.5,
            entropy=2.5,
            valid_count=100,
        )
        return SignalStatistics(
            stats={"engine_rpm": ss},
            vehicle_id="V-TEST",
            time_range=(
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 2, tzinfo=timezone.utc),
            ),
            dtc_codes=["P0301"],
            column_units={"engine_rpm": "rpm"},
            resample_interval_seconds=1.0,
        )

    def test_frozen(self, sample: SignalStatistics) -> None:
        with pytest.raises(FrozenInstanceError):
            sample.vehicle_id = "V-OTHER"  # type: ignore[misc]

    def test_to_dict_returns_dict(self, sample: SignalStatistics) -> None:
        d = sample.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_has_stats(self, sample: SignalStatistics) -> None:
        d = sample.to_dict()
        assert "engine_rpm" in d["stats"]

    def test_to_dict_metadata_keys(self, sample: SignalStatistics) -> None:
        d = sample.to_dict()
        assert d["vehicle_id"] == "V-TEST"
        assert d["dtc_codes"] == ["P0301"]
        assert d["resample_interval_seconds"] == 1.0
        assert d["column_units"] == {"engine_rpm": "rpm"}

    def test_to_dict_time_range_iso(self, sample: SignalStatistics) -> None:
        d = sample.to_dict()
        assert isinstance(d["time_range"], list)
        assert len(d["time_range"]) == 2
        # Should be parseable ISO strings.
        for ts_str in d["time_range"]:
            datetime.fromisoformat(ts_str)

    def test_to_dict_nan_becomes_none(self) -> None:
        ss = SignalStats(
            mean=1.0, std=0.0, min=1.0, max=1.0,
            p5=1.0, p25=1.0, p50=1.0, p75=1.0, p95=1.0,
            autocorrelation_lag1=float("nan"),
            mean_abs_change=float("nan"),
            max_abs_change=float("nan"),
            energy=1.0,
            entropy=float("nan"),
            valid_count=1,
        )
        sig = SignalStatistics(
            stats={"sig": ss},
            vehicle_id="V-X",
            time_range=(
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            dtc_codes=[],
            column_units={"sig": "u"},
            resample_interval_seconds=1.0,
        )
        d = sig.to_dict()
        assert d["stats"]["sig"]["autocorrelation_lag1"] is None
        assert d["stats"]["sig"]["mean_abs_change"] is None
        assert d["stats"]["sig"]["entropy"] is None
        # Non-NaN fields should remain numeric.
        assert d["stats"]["sig"]["mean"] == 1.0


# ---------------------------------------------------------------------------
# _autocorrelation_lag1
# ---------------------------------------------------------------------------


class TestAutocorrelationLag1:
    """Unit tests for the lag-1 autocorrelation helper."""

    def test_perfect_positive(self) -> None:
        """Linearly increasing signal -> autocorrelation near +1."""
        vals = np.arange(100, dtype=float)
        ac = _autocorrelation_lag1(vals)
        assert ac == pytest.approx(1.0, abs=0.01)

    def test_alternating_negative(self) -> None:
        """Alternating +1 / -1 -> autocorrelation near -1."""
        vals = np.array([1.0, -1.0] * 50)
        ac = _autocorrelation_lag1(vals)
        assert ac == pytest.approx(-1.0, abs=0.02)

    def test_constant_returns_nan(self) -> None:
        """Constant signal has zero variance -> NaN."""
        vals = np.ones(10)
        assert math.isnan(_autocorrelation_lag1(vals))

    def test_n_less_than_3_returns_nan(self) -> None:
        """Fewer than 3 observations -> NaN."""
        assert math.isnan(_autocorrelation_lag1(np.array([1.0, 2.0])))
        assert math.isnan(_autocorrelation_lag1(np.array([1.0])))
        assert math.isnan(_autocorrelation_lag1(np.array([])))

    def test_random_bounded(self) -> None:
        """Autocorrelation is always in [-1, 1]."""
        rng = np.random.default_rng(42)
        vals = rng.normal(size=200)
        ac = _autocorrelation_lag1(vals)
        assert -1.0 <= ac <= 1.0


# ---------------------------------------------------------------------------
# _shannon_entropy
# ---------------------------------------------------------------------------


class TestShannonEntropy:
    """Unit tests for the histogram-based Shannon entropy helper."""

    def test_uniform_distribution(self) -> None:
        """Uniform over 10 bins -> entropy = log2(10) ~ 3.3219."""
        # Create values that spread uniformly across 10 bins.
        vals = np.linspace(0, 1, 10_000)
        ent = _shannon_entropy(vals, n_bins=10)
        assert ent == pytest.approx(math.log2(10), abs=0.05)

    def test_constant_signal(self) -> None:
        """Constant signal -> entropy = 0."""
        vals = np.ones(100)
        assert _shannon_entropy(vals) == 0.0

    def test_binary_signal(self) -> None:
        """Balanced binary signal with 2 bins -> entropy ~ 1 bit."""
        vals = np.array([0.0] * 500 + [1.0] * 500)
        ent = _shannon_entropy(vals, n_bins=2)
        assert ent == pytest.approx(1.0, abs=0.01)

    def test_n_less_than_2_returns_nan(self) -> None:
        assert math.isnan(_shannon_entropy(np.array([1.0])))
        assert math.isnan(_shannon_entropy(np.array([])))

    def test_non_negative(self) -> None:
        """Entropy is never negative."""
        rng = np.random.default_rng(42)
        vals = rng.normal(size=200)
        ent = _shannon_entropy(vals)
        assert ent >= 0.0


# ---------------------------------------------------------------------------
# _compute_signal_stats
# ---------------------------------------------------------------------------


class TestComputeSignalStats:
    """Unit tests for the per-signal stats computation."""

    def test_constant_signal(self) -> None:
        vals = np.full(100, 5.0)
        ss = _compute_signal_stats(vals)
        assert ss.mean == pytest.approx(5.0)
        assert ss.std == pytest.approx(0.0)
        assert ss.min == pytest.approx(5.0)
        assert ss.max == pytest.approx(5.0)
        assert ss.p50 == pytest.approx(5.0)
        assert ss.energy == pytest.approx(25.0)
        assert ss.entropy == 0.0
        assert math.isnan(ss.autocorrelation_lag1)  # zero variance
        assert ss.mean_abs_change == pytest.approx(0.0)
        assert ss.max_abs_change == pytest.approx(0.0)
        assert ss.valid_count == 100

    def test_linear_ramp(self) -> None:
        vals = np.arange(1, 11, dtype=float)  # 1..10
        ss = _compute_signal_stats(vals)
        assert ss.mean == pytest.approx(5.5)
        assert ss.min == pytest.approx(1.0)
        assert ss.max == pytest.approx(10.0)
        assert ss.p50 == pytest.approx(5.5)
        assert ss.autocorrelation_lag1 == pytest.approx(1.0, abs=0.05)
        assert ss.mean_abs_change == pytest.approx(1.0)
        assert ss.max_abs_change == pytest.approx(1.0)
        assert ss.valid_count == 10

    def test_single_value(self) -> None:
        vals = np.array([42.0])
        ss = _compute_signal_stats(vals)
        assert ss.mean == pytest.approx(42.0)
        assert ss.std == pytest.approx(0.0)
        assert ss.valid_count == 1
        assert math.isnan(ss.autocorrelation_lag1)
        assert math.isnan(ss.mean_abs_change)
        assert math.isnan(ss.max_abs_change)
        assert math.isnan(ss.entropy)

    def test_two_values(self) -> None:
        vals = np.array([10.0, 20.0])
        ss = _compute_signal_stats(vals)
        assert ss.mean == pytest.approx(15.0)
        assert ss.min == pytest.approx(10.0)
        assert ss.max == pytest.approx(20.0)
        assert ss.mean_abs_change == pytest.approx(10.0)
        assert ss.max_abs_change == pytest.approx(10.0)
        assert ss.valid_count == 2
        # n < 3 for autocorrelation
        assert math.isnan(ss.autocorrelation_lag1)


# ---------------------------------------------------------------------------
# extract_statistics — real fixture
# ---------------------------------------------------------------------------


class TestExtractStatisticsRealFixture:
    """Integration tests against the real OBD log fixture."""

    @pytest.fixture()
    def result(self) -> SignalStatistics:
        ts = normalize_log_file(_REAL_LOG)
        return extract_statistics(ts)

    def test_returns_signal_statistics(self, result: SignalStatistics) -> None:
        assert isinstance(result, SignalStatistics)

    def test_all_32_columns_present(self, result: SignalStatistics) -> None:
        assert len(result.stats) == len(_PID_UNITS)

    def test_vehicle_id_populated(self, result: SignalStatistics) -> None:
        assert result.vehicle_id.startswith("V-")

    def test_time_range_tuple(self, result: SignalStatistics) -> None:
        start, end = result.time_range
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        assert end > start

    def test_dtc_codes_list(self, result: SignalStatistics) -> None:
        assert isinstance(result.dtc_codes, list)

    def test_column_units_match_stats(self, result: SignalStatistics) -> None:
        assert set(result.column_units.keys()) == set(result.stats.keys())

    def test_resample_interval(self, result: SignalStatistics) -> None:
        assert result.resample_interval_seconds == 1.0

    def test_engine_rpm_all_zero(self, result: SignalStatistics) -> None:
        """Engine RPM is constant 0 in the fixture."""
        rpm = result.stats["engine_rpm"]
        assert rpm.mean == pytest.approx(0.0)
        assert rpm.std == pytest.approx(0.0)
        assert rpm.min == pytest.approx(0.0)
        assert rpm.max == pytest.approx(0.0)
        assert rpm.energy == pytest.approx(0.0)

    def test_coolant_temperature_constant(self, result: SignalStatistics) -> None:
        """Coolant temperature is constant 32 degC in the fixture."""
        ct = result.stats["coolant_temperature"]
        assert ct.mean == pytest.approx(32.0)
        assert ct.std == pytest.approx(0.0)
        assert ct.entropy == 0.0

    def test_valid_count_positive(self, result: SignalStatistics) -> None:
        for name, ss in result.stats.items():
            assert ss.valid_count > 0, f"{name} has zero valid_count"

    def test_percentiles_ordered(self, result: SignalStatistics) -> None:
        """p5 <= p25 <= p50 <= p75 <= p95 for every signal."""
        for name, ss in result.stats.items():
            assert ss.p5 <= ss.p25, f"{name}: p5 > p25"
            assert ss.p25 <= ss.p50, f"{name}: p25 > p50"
            assert ss.p50 <= ss.p75, f"{name}: p50 > p75"
            assert ss.p75 <= ss.p95, f"{name}: p75 > p95"

    def test_long_fuel_trim_has_variation(self, result: SignalStatistics) -> None:
        """Long fuel trim should have non-zero std (it has a spike)."""
        lft = result.stats["long_fuel_trim_1"]
        assert lft.std > 0.0


# ---------------------------------------------------------------------------
# extract_statistics_from_log_file
# ---------------------------------------------------------------------------


class TestExtractStatisticsFromLogFile:
    """Test the convenience wrapper."""

    def test_equivalence_with_manual(self) -> None:
        ts = normalize_log_file(_REAL_LOG)
        manual = extract_statistics(ts)
        wrapped = extract_statistics_from_log_file(_REAL_LOG)
        assert set(manual.stats.keys()) == set(wrapped.stats.keys())
        assert manual.vehicle_id == wrapped.vehicle_id
        for col in manual.stats:
            assert manual.stats[col].mean == pytest.approx(
                wrapped.stats[col].mean
            )

    def test_string_path_input(self) -> None:
        result = extract_statistics_from_log_file(str(_REAL_LOG))
        assert isinstance(result, SignalStatistics)

    def test_path_object_input(self) -> None:
        result = extract_statistics_from_log_file(_REAL_LOG)
        assert isinstance(result, SignalStatistics)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case handling for extract_statistics."""

    def test_empty_dataframe_raises(self) -> None:
        df = pd.DataFrame(
            index=pd.DatetimeIndex([], name="timestamp"),
            columns=["engine_rpm"],
        )
        ts = NormalizedTimeSeries(
            df=df,
            vehicle_id="V-TEST",
            time_range=(
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            dtc_codes=[],
            column_units={"engine_rpm": "rpm"},
            column_pid_names={"engine_rpm": "RPM"},
            resample_interval_seconds=1.0,
            fill_method="interpolate",
            original_sample_count=0,
        )
        with pytest.raises(ValueError, match="empty"):
            extract_statistics(ts)

    def test_single_row(self) -> None:
        ts = _make_ts({"sig_a": [42.0]})
        result = extract_statistics(ts)
        assert result.stats["sig_a"].valid_count == 1
        assert result.stats["sig_a"].mean == pytest.approx(42.0)
        assert math.isnan(result.stats["sig_a"].entropy)

    def test_all_nan_column_omitted(self) -> None:
        ts = _make_ts({"sig_a": [1.0, 2.0, 3.0], "sig_b": [float("nan")] * 3})
        result = extract_statistics(ts)
        assert "sig_a" in result.stats
        assert "sig_b" not in result.stats

    def test_partial_nan_uses_valid_only(self) -> None:
        ts = _make_ts({"sig_a": [1.0, float("nan"), 3.0, 5.0]})
        result = extract_statistics(ts)
        # valid_count should be 3 (the non-NaN values)
        assert result.stats["sig_a"].valid_count == 3
        # mean of [1, 3, 5] = 3.0
        assert result.stats["sig_a"].mean == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# to_dict / JSON serialisation
# ---------------------------------------------------------------------------


class TestToDict:
    """Verify to_dict produces JSON-safe output."""

    @pytest.fixture()
    def result(self) -> SignalStatistics:
        ts = normalize_log_file(_REAL_LOG)
        return extract_statistics(ts)

    def test_json_round_trip(self, result: SignalStatistics) -> None:
        d = result.to_dict()
        serialised = json.dumps(d)
        restored = json.loads(serialised)
        assert isinstance(restored, dict)
        assert "stats" in restored

    def test_no_nan_literal_in_json(self, result: SignalStatistics) -> None:
        d = result.to_dict()
        serialised = json.dumps(d)
        assert "NaN" not in serialised
        assert "nan" not in serialised
        assert "Infinity" not in serialised

    def test_metadata_keys_present(self, result: SignalStatistics) -> None:
        d = result.to_dict()
        assert "vehicle_id" in d
        assert "time_range" in d
        assert "dtc_codes" in d
        assert "column_units" in d
        assert "resample_interval_seconds" in d

    def test_time_range_iso_format(self, result: SignalStatistics) -> None:
        d = result.to_dict()
        for ts_str in d["time_range"]:
            parsed = datetime.fromisoformat(ts_str)
            assert isinstance(parsed, datetime)

    def test_stats_dict_structure(self, result: SignalStatistics) -> None:
        d = result.to_dict()
        for name, stat_dict in d["stats"].items():
            assert isinstance(stat_dict, dict)
            assert "mean" in stat_dict
            assert "valid_count" in stat_dict

    def test_nan_fields_become_none(self) -> None:
        """Signals with NaN fields should have None in to_dict output."""
        ts = _make_ts({"sig_a": [5.0]})  # single value -> many NaN fields
        result = extract_statistics(ts)
        d = result.to_dict()
        sig = d["stats"]["sig_a"]
        assert sig["autocorrelation_lag1"] is None
        assert sig["mean_abs_change"] is None
        assert sig["entropy"] is None
        # Serialises without error.
        serialised = json.dumps(d)
        assert "NaN" not in serialised

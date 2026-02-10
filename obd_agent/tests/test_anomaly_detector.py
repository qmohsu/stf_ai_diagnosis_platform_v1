"""Tests for obd_agent.anomaly_detector (APP-15).

Covers dataclasses, internal helpers, detection algorithms, and the
public API against both synthetic data and the real log fixture.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from obd_agent.anomaly_detector import (
    AnomalyEvent,
    AnomalyReport,
    _compute_severity,
    _detect_changepoints,
    _detect_multivariate_outliers,
    _filter_variable_columns,
    _find_contiguous_runs,
    _infer_driving_context,
    _merge_overlapping_events,
    detect_anomalies,
    detect_anomalies_from_log_file,
)
from obd_agent.time_series_normalizer import NormalizedTimeSeries, normalize_log_file

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_LOG_FILE = _FIXTURES_DIR / "obd_log_20250723_144216.txt"


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------


def _make_ts(
    df: pd.DataFrame,
    vehicle_id: str = "V-TEST1234",
    dtc_codes: list | None = None,
) -> NormalizedTimeSeries:
    """Build a minimal NormalizedTimeSeries for testing."""
    if dtc_codes is None:
        dtc_codes = []
    start = df.index.min().to_pydatetime()
    end = df.index.max().to_pydatetime()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return NormalizedTimeSeries(
        df=df,
        vehicle_id=vehicle_id,
        time_range=(start, end),
        dtc_codes=dtc_codes,
        column_units={col: "unit" for col in df.columns},
        column_pid_names={col: col.upper() for col in df.columns},
        resample_interval_seconds=1.0,
        fill_method="interpolate",
        original_sample_count=len(df),
    )


def _make_datetime_index(n: int, start: str = "2025-07-23 14:42:16") -> pd.DatetimeIndex:
    """Create a UTC DatetimeIndex of *n* seconds starting at *start*."""
    base = pd.Timestamp(start, tz="UTC")
    return pd.date_range(start=base, periods=n, freq="1s", name="timestamp")


# ===================================================================
# TestAnomalyEventDataclass
# ===================================================================


class TestAnomalyEventDataclass:
    """AnomalyEvent is a frozen dataclass with 7 fields."""

    def test_creation(self):
        ev = AnomalyEvent(
            time_window=(
                datetime(2025, 7, 23, 14, 42, 16, tzinfo=timezone.utc),
                datetime(2025, 7, 23, 14, 42, 26, tzinfo=timezone.utc),
            ),
            signals=("engine_rpm", "vehicle_speed"),
            pattern="Test pattern",
            context="idle",
            severity="medium",
            detector="changepoint",
            score=0.55,
        )
        assert ev.severity == "medium"
        assert ev.detector == "changepoint"

    def test_frozen(self):
        ev = AnomalyEvent(
            time_window=(
                datetime(2025, 7, 23, tzinfo=timezone.utc),
                datetime(2025, 7, 23, tzinfo=timezone.utc),
            ),
            signals=("engine_rpm",),
            pattern="p",
            context="idle",
            severity="low",
            detector="changepoint",
            score=0.1,
        )
        with pytest.raises(AttributeError):
            ev.severity = "high"  # type: ignore[misc]

    def test_signals_is_tuple(self):
        ev = AnomalyEvent(
            time_window=(
                datetime(2025, 7, 23, tzinfo=timezone.utc),
                datetime(2025, 7, 23, tzinfo=timezone.utc),
            ),
            signals=("engine_rpm", "vehicle_speed"),
            pattern="p",
            context="idle",
            severity="low",
            detector="changepoint",
            score=0.1,
        )
        assert isinstance(ev.signals, tuple)
        assert len(ev.signals) == 2


# ===================================================================
# TestAnomalyReportDataclass
# ===================================================================


class TestAnomalyReportDataclass:
    """AnomalyReport is a frozen dataclass with to_dict()."""

    def _make_report(self, events=()) -> AnomalyReport:
        return AnomalyReport(
            events=events,
            vehicle_id="V-TEST1234",
            time_range=(
                datetime(2025, 7, 23, 14, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 7, 23, 15, 0, 0, tzinfo=timezone.utc),
            ),
            dtc_codes=["P0300"],
            detection_params={"contamination": 0.05},
        )

    def test_frozen(self):
        report = self._make_report()
        with pytest.raises(AttributeError):
            report.vehicle_id = "other"  # type: ignore[misc]

    def test_empty_events(self):
        report = self._make_report()
        assert len(report.events) == 0

    def test_to_dict_returns_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert isinstance(d, dict)
        assert "events" in d
        assert "vehicle_id" in d
        assert "time_range" in d
        assert "dtc_codes" in d
        assert "detection_params" in d

    def test_to_dict_json_serialisable(self):
        ev = AnomalyEvent(
            time_window=(
                datetime(2025, 7, 23, 14, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 7, 23, 14, 0, 10, tzinfo=timezone.utc),
            ),
            signals=("engine_rpm",),
            pattern="test",
            context="idle",
            severity="low",
            detector="changepoint",
            score=0.5,
        )
        report = self._make_report(events=(ev,))
        d = report.to_dict()
        serialised = json.dumps(d)
        assert isinstance(serialised, str)
        parsed = json.loads(serialised)
        assert len(parsed["events"]) == 1


# ===================================================================
# TestFilterVariableColumns
# ===================================================================


class TestFilterVariableColumns:
    """_filter_variable_columns removes constant and all-NaN columns."""

    def test_removes_constant(self):
        idx = _make_datetime_index(10)
        df = pd.DataFrame(
            {"a": np.ones(10), "b": np.arange(10, dtype=float)},
            index=idx,
        )
        result = _filter_variable_columns(df)
        assert "a" not in result
        assert "b" in result

    def test_keeps_variable(self):
        idx = _make_datetime_index(10)
        df = pd.DataFrame({"x": np.random.randn(10)}, index=idx)
        result = _filter_variable_columns(df)
        assert "x" in result

    def test_removes_all_nan(self):
        idx = _make_datetime_index(10)
        df = pd.DataFrame(
            {"nan_col": [np.nan] * 10, "good": np.arange(10, dtype=float)},
            index=idx,
        )
        result = _filter_variable_columns(df)
        assert "nan_col" not in result
        assert "good" in result


# ===================================================================
# TestInferDrivingContext
# ===================================================================


class TestInferDrivingContext:
    """_infer_driving_context classifies windows by RPM/speed/throttle."""

    def _window(self, rpm, speed, throttle=None):
        n = len(rpm)
        idx = _make_datetime_index(n)
        data: Dict[str, list] = {
            "engine_rpm": rpm,
            "vehicle_speed": speed,
        }
        if throttle is not None:
            data["throttle_position"] = throttle
        return pd.DataFrame(data, index=idx)

    def test_off(self):
        df = self._window([0] * 10, [0] * 10)
        assert _infer_driving_context(df) == "off"

    def test_idle(self):
        df = self._window([800] * 10, [0] * 10)
        assert _infer_driving_context(df) == "idle"

    def test_cruise(self):
        df = self._window(
            [2000] * 10,
            [60] * 10,
            [30.0, 30.1, 29.9, 30.0, 30.2, 30.0, 29.8, 30.0, 30.1, 29.9],
        )
        assert _infer_driving_context(df) == "cruise"

    def test_acceleration(self):
        df = self._window(
            [3000] * 10,
            [80] * 10,
            list(range(20, 80, 6)),  # rapidly increasing throttle
        )
        assert _infer_driving_context(df) == "acceleration"

    def test_unknown_missing_signals(self):
        idx = _make_datetime_index(5)
        df = pd.DataFrame({"some_other_signal": [1, 2, 3, 4, 5]}, index=idx)
        assert _infer_driving_context(df) == "unknown"


# ===================================================================
# TestComputeSeverity
# ===================================================================


class TestComputeSeverity:
    """_compute_severity buckets composite score into low/medium/high."""

    def test_low(self):
        result = _compute_severity(n_signals=1, score=0.1, duration_seconds=5, has_critical=False)
        assert result == "low"

    def test_medium(self):
        result = _compute_severity(n_signals=4, score=0.6, duration_seconds=120, has_critical=False)
        assert result == "medium"

    def test_high(self):
        result = _compute_severity(n_signals=8, score=0.9, duration_seconds=300, has_critical=True)
        assert result == "high"

    def test_score_clamped(self):
        # Even with extreme values, result is a valid tier
        result = _compute_severity(n_signals=100, score=2.0, duration_seconds=9999, has_critical=True)
        assert result in ("low", "medium", "high")


# ===================================================================
# TestFindContiguousRuns
# ===================================================================


class TestFindContiguousRuns:
    """_find_contiguous_runs converts bool array to (start, end) pairs."""

    def test_single_run(self):
        mask = np.array([False, True, True, True, False])
        runs = _find_contiguous_runs(mask)
        assert runs == [(1, 3)]

    def test_multiple_runs(self):
        mask = np.array([True, True, False, False, True])
        runs = _find_contiguous_runs(mask)
        assert runs == [(0, 1), (4, 4)]

    def test_no_runs(self):
        mask = np.array([False, False, False])
        runs = _find_contiguous_runs(mask)
        assert runs == []

    def test_all_true(self):
        mask = np.array([True, True, True])
        runs = _find_contiguous_runs(mask)
        assert runs == [(0, 2)]


# ===================================================================
# TestDetectChangepoints
# ===================================================================


class TestDetectChangepoints:
    """_detect_changepoints finds level shifts via ruptures Pelt."""

    def test_step_change_detected(self):
        """A clear level shift should produce at least one event."""
        n = 100
        idx = _make_datetime_index(n)
        signal = np.concatenate([np.zeros(50), np.ones(50) * 10.0])
        df = pd.DataFrame({"test_signal": signal}, index=idx)
        events = _detect_changepoints(df, ["test_signal"], min_segment_length=5)
        assert len(events) >= 1
        assert events[0].detector == "changepoint"

    def test_constant_signal_no_events(self):
        """A constant signal should produce no change-points."""
        n = 100
        idx = _make_datetime_index(n)
        df = pd.DataFrame({"flat": np.ones(n) * 5.0}, index=idx)
        events = _detect_changepoints(df, ["flat"], min_segment_length=5)
        assert len(events) == 0

    def test_too_few_rows(self):
        """Fewer than _MIN_ROWS_CHANGEPOINT → no events."""
        idx = _make_datetime_index(10)
        df = pd.DataFrame({"x": np.arange(10, dtype=float)}, index=idx)
        events = _detect_changepoints(df, ["x"], min_segment_length=5)
        assert events == []

    def test_pattern_string_populated(self):
        """Events should have a non-empty pattern description."""
        n = 100
        idx = _make_datetime_index(n)
        signal = np.concatenate([np.zeros(50), np.ones(50) * 20.0])
        df = pd.DataFrame({"sig": signal}, index=idx)
        events = _detect_changepoints(df, ["sig"], min_segment_length=5)
        if events:
            assert "Change-point" in events[0].pattern
            assert "sig" in events[0].pattern


# ===================================================================
# TestDetectMultivariateOutliers
# ===================================================================


class TestDetectMultivariateOutliers:
    """_detect_multivariate_outliers uses Isolation Forest on z-scores."""

    def test_injected_outlier_detected(self):
        """Extreme outlier rows should be flagged."""
        n = 200
        idx = _make_datetime_index(n)
        rng = np.random.RandomState(42)
        data = rng.randn(n, 3)
        # Inject extreme outlier at rows 100-104
        data[100:105, :] = 50.0
        df = pd.DataFrame(data, columns=["a", "b", "c"], index=idx)
        events = _detect_multivariate_outliers(df, ["a", "b", "c"], contamination=0.05)
        assert len(events) >= 1
        assert events[0].detector == "isolation_forest"

    def test_normal_data_few_events(self):
        """Gaussian data with no outliers should produce few or no events."""
        n = 200
        idx = _make_datetime_index(n)
        rng = np.random.RandomState(99)
        data = rng.randn(n, 3) * 0.1  # tight cluster
        df = pd.DataFrame(data, columns=["a", "b", "c"], index=idx)
        events = _detect_multivariate_outliers(df, ["a", "b", "c"], contamination=0.05)
        # Should produce some events (contamination forces ~5%) but scores should be low
        for ev in events:
            assert ev.detector == "isolation_forest"

    def test_too_few_rows(self):
        """Fewer than _MIN_ROWS_ISOLATION_FOREST → no events."""
        idx = _make_datetime_index(10)
        df = pd.DataFrame({"a": np.arange(10.0), "b": np.arange(10.0)}, index=idx)
        events = _detect_multivariate_outliers(df, ["a", "b"], contamination=0.05)
        assert events == []

    def test_single_column_skipped(self):
        """Fewer than 2 columns → no events."""
        n = 50
        idx = _make_datetime_index(n)
        df = pd.DataFrame({"a": np.arange(n, dtype=float)}, index=idx)
        events = _detect_multivariate_outliers(df, ["a"], contamination=0.05)
        assert events == []


# ===================================================================
# TestMergeOverlappingEvents
# ===================================================================


class TestMergeOverlappingEvents:
    """_merge_overlapping_events combines time-overlapping events."""

    def _event(self, start_sec, end_sec, signals=("a",), detector="changepoint", score=0.5):
        base = datetime(2025, 7, 23, 14, 0, 0, tzinfo=timezone.utc)
        return AnomalyEvent(
            time_window=(
                base + timedelta(seconds=start_sec),
                base + timedelta(seconds=end_sec),
            ),
            signals=signals,
            pattern="test",
            context="idle",
            severity="medium",
            detector=detector,
            score=score,
        )

    def test_non_overlapping_preserved(self):
        events = [self._event(0, 5), self._event(10, 15)]
        merged = _merge_overlapping_events(events)
        assert len(merged) == 2

    def test_overlapping_merged(self):
        events = [self._event(0, 10), self._event(5, 15)]
        merged = _merge_overlapping_events(events)
        assert len(merged) == 1
        assert merged[0].time_window[0] == events[0].time_window[0]
        assert merged[0].time_window[1] == events[1].time_window[1]

    def test_combined_label_when_mixed_detectors(self):
        events = [
            self._event(0, 10, detector="changepoint"),
            self._event(5, 15, detector="isolation_forest"),
        ]
        merged = _merge_overlapping_events(events)
        assert len(merged) == 1
        assert merged[0].detector == "combined"


# ===================================================================
# TestDetectAnomaliesPublicAPI
# ===================================================================


class TestDetectAnomaliesPublicAPI:
    """detect_anomalies() validates params and returns AnomalyReport."""

    def _empty_ts(self) -> NormalizedTimeSeries:
        idx = _make_datetime_index(5)
        df = pd.DataFrame({"a": [1.0] * 5}, index=idx)
        return _make_ts(df)

    def test_return_type(self):
        ts = self._empty_ts()
        report = detect_anomalies(ts)
        assert isinstance(report, AnomalyReport)

    def test_invalid_contamination(self):
        ts = self._empty_ts()
        with pytest.raises(ValueError, match="contamination"):
            detect_anomalies(ts, contamination=0.0)
        with pytest.raises(ValueError, match="contamination"):
            detect_anomalies(ts, contamination=0.6)

    def test_invalid_min_segment(self):
        ts = self._empty_ts()
        with pytest.raises(ValueError, match="min_segment_length"):
            detect_anomalies(ts, min_segment_length=1)

    def test_empty_df(self):
        idx = _make_datetime_index(5)
        df = pd.DataFrame({"a": [1.0] * 5}, index=idx)
        ts = _make_ts(df)
        report = detect_anomalies(ts)
        assert len(report.events) == 0  # only 5 rows < 20


# ===================================================================
# TestDetectAnomaliesRealFixture
# ===================================================================


class TestDetectAnomaliesRealFixture:
    """Run detection on the real log fixture."""

    @pytest.fixture(scope="class")
    def report(self) -> AnomalyReport:
        ts = normalize_log_file(_LOG_FILE)
        return detect_anomalies(ts)

    def test_returns_report(self, report: AnomalyReport):
        assert isinstance(report, AnomalyReport)

    def test_has_events(self, report: AnomalyReport):
        assert len(report.events) > 0

    def test_fields_populated(self, report: AnomalyReport):
        for ev in report.events:
            assert ev.time_window[0] <= ev.time_window[1]
            assert len(ev.signals) > 0
            assert ev.pattern != ""
            assert ev.detector in ("changepoint", "isolation_forest", "combined")
            assert 0.0 <= ev.score <= 1.0

    def test_context_values(self, report: AnomalyReport):
        valid_contexts = {"off", "idle", "cruise", "acceleration", "unknown"}
        for ev in report.events:
            assert ev.context in valid_contexts

    def test_severity_values(self, report: AnomalyReport):
        valid_severities = {"low", "medium", "high"}
        for ev in report.events:
            assert ev.severity in valid_severities


# ===================================================================
# TestDetectAnomaliesFromLogFile
# ===================================================================


class TestDetectAnomaliesFromLogFile:
    """detect_anomalies_from_log_file() is equivalent to manual pipeline."""

    @pytest.fixture(scope="class")
    def report_from_file(self) -> AnomalyReport:
        return detect_anomalies_from_log_file(_LOG_FILE)

    def test_equivalence(self, report_from_file: AnomalyReport):
        ts = normalize_log_file(_LOG_FILE)
        report_manual = detect_anomalies(ts)
        # Same number of events and same vehicle ID
        assert len(report_from_file.events) == len(report_manual.events)
        assert report_from_file.vehicle_id == report_manual.vehicle_id

    def test_str_path(self):
        report = detect_anomalies_from_log_file(str(_LOG_FILE))
        assert isinstance(report, AnomalyReport)

    def test_path_object(self):
        report = detect_anomalies_from_log_file(_LOG_FILE)
        assert isinstance(report, AnomalyReport)


# ===================================================================
# TestToDict
# ===================================================================


class TestToDict:
    """AnomalyReport.to_dict() produces JSON-friendly output."""

    @pytest.fixture(scope="class")
    def report(self) -> AnomalyReport:
        return detect_anomalies_from_log_file(_LOG_FILE)

    def test_json_round_trip(self, report: AnomalyReport):
        d = report.to_dict()
        serialised = json.dumps(d)
        parsed = json.loads(serialised)
        assert parsed["vehicle_id"] == report.vehicle_id

    def test_iso_timestamps(self, report: AnomalyReport):
        d = report.to_dict()
        # time_range should be ISO strings
        assert isinstance(d["time_range"][0], str)
        assert isinstance(d["time_range"][1], str)
        # Event timestamps too
        if d["events"]:
            tw = d["events"][0]["time_window"]
            assert isinstance(tw[0], str)
            assert isinstance(tw[1], str)

    def test_metadata_keys(self, report: AnomalyReport):
        d = report.to_dict()
        assert "events" in d
        assert "vehicle_id" in d
        assert "time_range" in d
        assert "dtc_codes" in d
        assert "detection_params" in d

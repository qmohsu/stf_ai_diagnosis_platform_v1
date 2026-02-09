"""Tests for the OBD time-series normaliser (APP-13)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from obd_agent.log_parser import _PID_UNITS, parse_log_file, pseudonymise_vin
from obd_agent.time_series_normalizer import (
    NormalizedTimeSeries,
    _PID_SEMANTIC_NAMES,
    _SEMANTIC_TO_PID,
    _SEMANTIC_UNITS,
    _extract_metadata,
    _resample_dataframe,
    _rows_to_raw_dataframe,
    normalize_log_file,
    normalize_rows,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_REAL_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"
_REAL_VIN = "JHMGK5830HX202404"


# ---------------------------------------------------------------------------
# PID semantic-name mapping
# ---------------------------------------------------------------------------


class TestPIDSemanticNames:
    """Verify the PID-to-semantic-name mapping is complete and consistent."""

    def test_all_pids_covered(self) -> None:
        """Every PID in _PID_UNITS has a semantic name."""
        assert set(_PID_SEMANTIC_NAMES.keys()) == set(_PID_UNITS.keys())

    def test_snake_case_format(self) -> None:
        """All semantic names are valid snake_case identifiers."""
        pattern = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
        for pid, name in _PID_SEMANTIC_NAMES.items():
            assert pattern.match(name), f"{pid} -> {name!r} is not snake_case"

    def test_no_duplicate_semantic_names(self) -> None:
        """Semantic names are unique (no two PIDs share the same name)."""
        values = list(_PID_SEMANTIC_NAMES.values())
        assert len(values) == len(set(values))

    def test_round_trip_inversion(self) -> None:
        """_SEMANTIC_TO_PID inverts _PID_SEMANTIC_NAMES exactly."""
        for pid, semantic in _PID_SEMANTIC_NAMES.items():
            assert _SEMANTIC_TO_PID[semantic] == pid

    def test_semantic_units_derived(self) -> None:
        """_SEMANTIC_UNITS has the same keys as _PID_SEMANTIC_NAMES values."""
        assert set(_SEMANTIC_UNITS.keys()) == set(_PID_SEMANTIC_NAMES.values())

    def test_semantic_units_match_pid_units(self) -> None:
        """Each semantic unit matches its source PID unit."""
        for pid, unit in _PID_UNITS.items():
            semantic = _PID_SEMANTIC_NAMES[pid]
            assert _SEMANTIC_UNITS[semantic] == unit

    def test_count_matches_pid_units(self) -> None:
        """PID mapping count matches _PID_UNITS."""
        assert len(_PID_SEMANTIC_NAMES) == len(_PID_UNITS)


# ---------------------------------------------------------------------------
# _rows_to_raw_dataframe
# ---------------------------------------------------------------------------


class TestRowsToRawDataframe:
    """Verify conversion of parsed rows into a raw DataFrame."""

    @pytest.fixture()
    def rows(self) -> list:
        return parse_log_file(_REAL_LOG)

    @pytest.fixture()
    def raw_df(self, rows: list) -> pd.DataFrame:
        return _rows_to_raw_dataframe(rows)

    def test_has_datetime_index(self, raw_df: pd.DataFrame) -> None:
        assert isinstance(raw_df.index, pd.DatetimeIndex)
        assert raw_df.index.name == "timestamp"

    def test_has_expected_columns(self, raw_df: pd.DataFrame) -> None:
        assert len(raw_df.columns) == len(_PID_UNITS)

    def test_columns_are_semantic_names(self, raw_df: pd.DataFrame) -> None:
        expected = set(_PID_SEMANTIC_NAMES.values())
        assert set(raw_df.columns) == expected

    def test_known_values_first_row(self, raw_df: pd.DataFrame) -> None:
        """First row values match the real fixture."""
        first = raw_df.iloc[0]
        assert first["engine_rpm"] == pytest.approx(0.0)
        assert first["vehicle_speed"] == pytest.approx(0.0)
        assert first["throttle_position"] == pytest.approx(17.25)
        assert first["coolant_temperature"] == pytest.approx(32.0)
        assert first["long_fuel_trim_1"] == pytest.approx(-10.94)

    def test_non_numeric_becomes_nan(self) -> None:
        """Non-numeric PID values are coerced to NaN."""
        row = {
            "Timestamp": "2025-01-01 00:00:00",
            "RPM": "not_a_number",
            "SPEED": "50",
        }
        df = _rows_to_raw_dataframe([row])
        assert pd.isna(df.iloc[0]["engine_rpm"])
        assert df.iloc[0]["vehicle_speed"] == pytest.approx(50.0)

    def test_duplicate_timestamps_averaged(self) -> None:
        """Rows with identical timestamps are merged by averaging."""
        rows = [
            {"Timestamp": "2025-01-01 00:00:00", "RPM": "100", "SPEED": "50"},
            {"Timestamp": "2025-01-01 00:00:00", "RPM": "200", "SPEED": "60"},
        ]
        df = _rows_to_raw_dataframe(rows)
        assert len(df) == 1
        assert df.iloc[0]["engine_rpm"] == pytest.approx(150.0)
        assert df.iloc[0]["vehicle_speed"] == pytest.approx(55.0)

    def test_sorted_by_time(self, raw_df: pd.DataFrame) -> None:
        assert raw_df.index.is_monotonic_increasing

    def test_row_count_matches_input(self, rows: list, raw_df: pd.DataFrame) -> None:
        """Row count equals input rows (no duplicates in fixture)."""
        assert len(raw_df) == len(rows)


# ---------------------------------------------------------------------------
# _extract_metadata
# ---------------------------------------------------------------------------


class TestExtractMetadata:
    """Verify vehicle ID pseudonymisation and DTC extraction."""

    @pytest.fixture()
    def rows(self) -> list:
        return parse_log_file(_REAL_LOG)

    def test_vehicle_id_pseudonymised(self, rows: list) -> None:
        vid, _ = _extract_metadata(rows)
        assert vid == pseudonymise_vin(_REAL_VIN)
        assert vid.startswith("V-")

    def test_vehicle_id_override(self, rows: list) -> None:
        vid, _ = _extract_metadata(rows, vehicle_id_override="V-CUSTOM")
        assert vid == "V-CUSTOM"

    def test_dtc_extraction_empty(self, rows: list) -> None:
        """Real log has no DTCs."""
        _, dtc_codes = _extract_metadata(rows)
        assert dtc_codes == []

    def test_dtc_extraction_with_codes(self) -> None:
        """Rows with DTC values get extracted and deduplicated."""
        rows = [
            {
                "Timestamp": "2025-01-01 00:00:00",
                "VIN": f"bytearray(b'{_REAL_VIN}')",
                "GET_DTC": "[('P0301', 'Cylinder 1 Misfire')]",
                "GET_CURRENT_DTC": "[('P0301', 'Cylinder 1 Misfire'), ('P0171', 'Lean')]",
            },
        ]
        _, dtc_codes = _extract_metadata(rows)
        assert "P0301" in dtc_codes
        assert "P0171" in dtc_codes
        # Deduplicated: P0301 appears only once.
        assert dtc_codes.count("P0301") == 1

    def test_missing_vin_defaults_to_unknown(self) -> None:
        rows = [{"Timestamp": "2025-01-01 00:00:00", "VIN": ""}]
        vid, _ = _extract_metadata(rows)
        assert vid == "V-UNKNOWN"


# ---------------------------------------------------------------------------
# _resample_dataframe
# ---------------------------------------------------------------------------


class TestResampleDataframe:
    """Verify resampling to a uniform time grid."""

    @pytest.fixture()
    def raw_df(self) -> pd.DataFrame:
        rows = parse_log_file(_REAL_LOG)
        return _rows_to_raw_dataframe(rows)

    def test_uniform_1s_grid(self, raw_df: pd.DataFrame) -> None:
        """Resampling at 1s produces 289 rows (288s span + 1)."""
        result = _resample_dataframe(raw_df, 1.0, "interpolate")
        assert len(result) == 289

    def test_configurable_interval(self, raw_df: pd.DataFrame) -> None:
        """2-second interval halves the row count (approx)."""
        result = _resample_dataframe(raw_df, 2.0, "interpolate")
        assert len(result) == 145  # ceil(288/2) + 1

    def test_fill_method_interpolate(self, raw_df: pd.DataFrame) -> None:
        result = _resample_dataframe(raw_df, 1.0, "interpolate")
        # Constant columns should remain constant after interpolation.
        assert (result["coolant_temperature"].dropna() == 32.0).all()

    def test_fill_method_ffill(self, raw_df: pd.DataFrame) -> None:
        result = _resample_dataframe(raw_df, 1.0, "ffill")
        assert len(result) == 289
        # Forward fill: first value should be present.
        assert result.iloc[0]["engine_rpm"] == pytest.approx(0.0)

    def test_fill_method_bfill(self, raw_df: pd.DataFrame) -> None:
        result = _resample_dataframe(raw_df, 1.0, "bfill")
        assert len(result) == 289
        # Backward fill: last value should be present.
        assert result.iloc[-1]["engine_rpm"] == pytest.approx(0.0)

    def test_fill_method_none(self, raw_df: pd.DataFrame) -> None:
        result = _resample_dataframe(raw_df, 1.0, "none")
        assert len(result) == 289
        # "none" leaves NaN where no original sample exists.
        # Most 1s grid points won't align with ~2s original spacing.
        nan_count = result["engine_rpm"].isna().sum()
        assert nan_count > 0

    def test_original_values_preserved_at_exact_timestamps(
        self, raw_df: pd.DataFrame
    ) -> None:
        """Values at original sample timestamps survive resampling."""
        result = _resample_dataframe(raw_df, 1.0, "interpolate")
        # The first timestamp from the fixture should be in the grid.
        first_ts = raw_df.index[0]
        if first_ts in result.index:
            for col in raw_df.columns:
                orig = raw_df.loc[first_ts, col]
                resampled = result.loc[first_ts, col]
                if not pd.isna(orig):
                    assert resampled == pytest.approx(orig, abs=1e-9)

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame(
            index=pd.DatetimeIndex([], name="timestamp"),
            columns=["engine_rpm"],
        )
        result = _resample_dataframe(df, 1.0, "interpolate")
        assert result.empty


# ---------------------------------------------------------------------------
# normalize_rows (end-to-end)
# ---------------------------------------------------------------------------


class TestNormalizeRows:
    """End-to-end tests on the real fixture."""

    @pytest.fixture()
    def rows(self) -> list:
        return parse_log_file(_REAL_LOG)

    @pytest.fixture()
    def result(self, rows: list) -> NormalizedTimeSeries:
        return normalize_rows(rows)

    def test_returns_normalized_timeseries(self, result: NormalizedTimeSeries) -> None:
        assert isinstance(result, NormalizedTimeSeries)

    def test_correct_shape(self, result: NormalizedTimeSeries) -> None:
        assert result.df.shape == (289, len(_PID_UNITS))

    def test_vehicle_id(self, result: NormalizedTimeSeries) -> None:
        assert result.vehicle_id == pseudonymise_vin(_REAL_VIN)

    def test_time_range(self, result: NormalizedTimeSeries) -> None:
        start, end = result.time_range
        assert start == datetime(2025, 7, 23, 14, 42, 16, tzinfo=timezone.utc)
        assert end == datetime(2025, 7, 23, 14, 47, 4, tzinfo=timezone.utc)

    def test_dtc_codes_empty(self, result: NormalizedTimeSeries) -> None:
        assert result.dtc_codes == []

    def test_column_units(self, result: NormalizedTimeSeries) -> None:
        assert result.column_units["engine_rpm"] == "rpm"
        assert result.column_units["coolant_temperature"] == "degC"
        assert len(result.column_units) == len(_PID_UNITS)

    def test_column_pid_names(self, result: NormalizedTimeSeries) -> None:
        assert result.column_pid_names["engine_rpm"] == "RPM"
        assert result.column_pid_names["coolant_temperature"] == "COOLANT_TEMP"

    def test_resample_interval(self, result: NormalizedTimeSeries) -> None:
        assert result.resample_interval_seconds == 1.0

    def test_fill_method(self, result: NormalizedTimeSeries) -> None:
        assert result.fill_method == "interpolate"

    def test_original_sample_count(self, result: NormalizedTimeSeries) -> None:
        assert result.original_sample_count == 158

    def test_no_all_nan_columns(self, result: NormalizedTimeSeries) -> None:
        """No column should be entirely NaN."""
        for col in result.df.columns:
            assert not result.df[col].isna().all(), f"Column {col!r} is all NaN"

    def test_constant_signal_stays_constant(
        self, result: NormalizedTimeSeries
    ) -> None:
        """Coolant temp is constant (32 degC); interpolation shouldn't alter it."""
        coolant = result.df["coolant_temperature"].dropna()
        assert (coolant == 32.0).all()

    def test_datetime_index_is_utc(self, result: NormalizedTimeSeries) -> None:
        assert result.df.index.tz is not None

    def test_vehicle_id_override(self, rows: list) -> None:
        result = normalize_rows(rows, vehicle_id="V-FLEET-99")
        assert result.vehicle_id == "V-FLEET-99"

    def test_custom_interval(self, rows: list) -> None:
        result = normalize_rows(rows, interval_seconds=2.0)
        assert result.resample_interval_seconds == 2.0
        assert result.df.shape[0] == 145


# ---------------------------------------------------------------------------
# normalize_log_file
# ---------------------------------------------------------------------------


class TestNormalizeLogFile:
    """Test the convenience wrapper."""

    def test_path_string_input(self) -> None:
        result = normalize_log_file(str(_REAL_LOG))
        assert isinstance(result, NormalizedTimeSeries)
        assert result.df.shape == (289, len(_PID_UNITS))

    def test_path_object_input(self) -> None:
        result = normalize_log_file(_REAL_LOG)
        assert isinstance(result, NormalizedTimeSeries)
        assert result.df.shape == (289, len(_PID_UNITS))

    def test_equivalence_with_manual_parse(self) -> None:
        """normalize_log_file == parse_log_file + normalize_rows."""
        rows = parse_log_file(_REAL_LOG)
        manual = normalize_rows(rows)
        wrapped = normalize_log_file(_REAL_LOG)
        pd.testing.assert_frame_equal(manual.df, wrapped.df)
        assert manual.vehicle_id == wrapped.vehicle_id
        assert manual.time_range == wrapped.time_range
        assert manual.dtc_codes == wrapped.dtc_codes


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case handling."""

    def test_empty_rows_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_rows([])

    def test_zero_interval_raises(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        with pytest.raises(ValueError, match="positive"):
            normalize_rows(rows, interval_seconds=0)

    def test_negative_interval_raises(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        with pytest.raises(ValueError, match="positive"):
            normalize_rows(rows, interval_seconds=-1.0)

    def test_single_row(self) -> None:
        """A single row produces a 1-row DataFrame (no resampling needed)."""
        rows = parse_log_file(_REAL_LOG)
        result = normalize_rows([rows[0]])
        assert result.df.shape[0] == 1
        assert result.original_sample_count == 1

    def test_same_timestamp_rows(self) -> None:
        """Multiple rows with the same timestamp are averaged into one."""
        rows = [
            {
                "Timestamp": "2025-01-01 00:00:00",
                "RPM": "100",
                "SPEED": "40",
                "VIN": "",
            },
            {
                "Timestamp": "2025-01-01 00:00:00",
                "RPM": "200",
                "SPEED": "60",
                "VIN": "",
            },
        ]
        result = normalize_rows(rows, vehicle_id="V-TEST")
        assert result.df.shape[0] == 1
        assert result.df.iloc[0]["engine_rpm"] == pytest.approx(150.0)
        assert result.df.iloc[0]["vehicle_speed"] == pytest.approx(50.0)

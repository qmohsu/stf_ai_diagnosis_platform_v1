"""Tests for the OBD log format auto-detection and normalisation layer."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from obd_agent.format_normalizer import (
    _detect_format,
    _normalise_csvlog_timestamp,
    _normalise_timestamp_generic,
    _try_convert,
    _fahrenheit_to_celsius,
    _mph_to_kmh,
    _inhg_to_kpa,
    _psi_to_kpa,
    _lbmin_to_gs,
    _miles_to_km,
    normalize_obd_file,
)
from obd_agent.log_parser import parse_log_file

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_CSVLOG_SAMPLE = _FIXTURES_DIR / "csvlog_sample.csv"
_MAXLOG_SAMPLE = _FIXTURES_DIR / "maxlog_sample.csv"
_NATIVE_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"


def _copy_fixture(src: Path, tmp_path: Path) -> Path:
    """Copy a fixture file into tmp_path for safe normalisation."""
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    return dst


# ── Unit converters ──────────────────────────────────────────────────

class TestUnitConverters:
    """Unit conversion helpers produce correct metric values."""

    def test_fahrenheit_to_celsius_freezing(self) -> None:
        """32 °F → 0 °C."""
        assert _fahrenheit_to_celsius(32.0) == 0.0

    def test_fahrenheit_to_celsius_boiling(self) -> None:
        """212 °F → 100 °C."""
        assert _fahrenheit_to_celsius(212.0) == 100.0

    def test_fahrenheit_to_celsius_body_temp(self) -> None:
        """98.6 °F → 37.0 °C."""
        assert _fahrenheit_to_celsius(98.6) == 37.0

    def test_mph_to_kmh(self) -> None:
        """60 MPH → ~96.56 km/h."""
        assert _mph_to_kmh(60.0) == 96.56

    def test_inhg_to_kpa(self) -> None:
        """29.92 inHg → ~101.33 kPa (standard atmosphere)."""
        result = _inhg_to_kpa(29.92)
        assert 101.3 < result < 101.4

    def test_psi_to_kpa(self) -> None:
        """14.696 psi → ~101.33 kPa."""
        result = _psi_to_kpa(14.696)
        assert 101.3 < result < 101.4

    def test_lbmin_to_gs(self) -> None:
        """1.0 lb/min → ~7.56 g/s."""
        assert _lbmin_to_gs(1.0) == 7.56

    def test_miles_to_km(self) -> None:
        """1.0 miles → ~1.61 km."""
        assert _miles_to_km(1.0) == 1.61


# ── Timestamp normalisation ──────────────────────────────────────────

class TestTimestampNormalisation:
    """Timestamp parsing for OBDWIZ and generic formats."""

    def test_csvlog_am_chinese(self) -> None:
        """OBDWIZ AM timestamp with sub-second precision."""
        raw = "03/18/2026 11:46:45.2597 上午"
        assert _normalise_csvlog_timestamp(raw) == "2026-03-18 11:46:45"

    def test_csvlog_pm_chinese(self) -> None:
        """OBDWIZ PM timestamp converts to 24-hour format."""
        raw = "03/18/2026 02:30:15.1234 下午"
        assert _normalise_csvlog_timestamp(raw) == "2026-03-18 14:30:15"

    def test_csvlog_noon(self) -> None:
        """12 PM (下午) stays as 12."""
        raw = "01/01/2026 12:00:00.0000 下午"
        assert _normalise_csvlog_timestamp(raw) == "2026-01-01 12:00:00"

    def test_csvlog_midnight(self) -> None:
        """12 AM (上午) converts to 00."""
        raw = "01/01/2026 12:00:00.0000 上午"
        assert _normalise_csvlog_timestamp(raw) == "2026-01-01 00:00:00"

    def test_generic_strips_milliseconds(self) -> None:
        """Sub-second precision is removed."""
        raw = "2026-03-18 13:22:10.123"
        assert _normalise_timestamp_generic(raw) == "2026-03-18 13:22:10"

    def test_generic_strips_iso_t(self) -> None:
        """ISO 'T' separator is replaced with space."""
        raw = "2026-03-18T13:22:10"
        assert _normalise_timestamp_generic(raw) == "2026-03-18 13:22:10"

    def test_generic_already_normalised(self) -> None:
        """Already-correct timestamp passes through."""
        raw = "2026-03-18 13:22:10"
        assert _normalise_timestamp_generic(raw) == "2026-03-18 13:22:10"


# ── _try_convert ─────────────────────────────────────────────────────

class TestTryConvert:
    """Unit conversion on raw string values."""

    def test_no_converter(self) -> None:
        """None converter returns value unchanged."""
        assert _try_convert("42.5", None) == "42.5"

    def test_valid_conversion(self) -> None:
        """Fahrenheit string is converted."""
        result = _try_convert("212", _fahrenheit_to_celsius)
        assert result == "100.0"

    def test_na_passthrough(self) -> None:
        """N/A values are returned unchanged."""
        assert _try_convert("N/A", _mph_to_kmh) == "N/A"

    def test_empty_passthrough(self) -> None:
        """Empty strings are returned unchanged."""
        assert _try_convert("", _mph_to_kmh) == ""

    def test_non_numeric_passthrough(self) -> None:
        """Non-numeric strings are returned unchanged."""
        assert _try_convert("Gasoline", _mph_to_kmh) == "Gasoline"


# ── Format detection ─────────────────────────────────────────────────

class TestDetectFormat:
    """Auto-detection of OBD log formats."""

    def test_native_tsv(self) -> None:
        """Lines starting with 'Timestamp\\t' are native TSV."""
        lines = [
            "OBD Data Log\n",
            "Start Time: 2025-07-23\n",
            "Log Interval: 1.0 seconds\n",
            "-" * 80 + "\n",
            "Timestamp\tRPM\tSPEED\n",
            "-" * 80 + "\n",
        ]
        assert _detect_format(lines) == "native_tsv"

    def test_csvlog_obdwiz(self) -> None:
        """Chinese characters in headers identify OBDWIZ format."""
        lines = [
            "Time,车速 (MPH),发动机转速 (RPM)\n",
            "03/18/2026 11:46:45.2597 上午,0,745\n",
        ]
        assert _detect_format(lines) == "csvlog_obdwiz"

    def test_obd_maxlog(self) -> None:
        """Metadata comments + unit-suffixed headers identify maxlog."""
        lines = [
            "# OBD Maximum Data Log\n",
            "# Start Time: 2026-03-18\n",
            "Timestamp,RPM (rpm),SPEED (km/h)\n",
            "2026-03-18 13:22:10.123,745,0\n",
        ]
        assert _detect_format(lines) == "obd_maxlog"

    def test_generic_csv(self) -> None:
        """Comma-separated with bare Timestamp header is generic CSV."""
        lines = [
            "Timestamp,RPM,SPEED,COOLANT_TEMP\n",
            "2026-03-18 13:22:10,745,0,32\n",
        ]
        assert _detect_format(lines) == "generic_csv"

    def test_maxlog_without_metadata(self) -> None:
        """Unit-suffixed CSV headers without # metadata is still maxlog."""
        lines = [
            "Timestamp,RPM (rpm),SPEED (km/h)\n",
            "2026-03-18 13:22:10.123,745,0\n",
        ]
        assert _detect_format(lines) == "obd_maxlog"


# ── CSVLog normalisation ─────────────────────────────────────────────

class TestNormalizeCsvlog:
    """OBDWIZ CSVLog → internal TSV conversion."""

    def test_end_to_end_csvlog(self, tmp_path: Path) -> None:
        """Full OBDWIZ sample converts to parseable TSV."""
        sample = _copy_fixture(_CSVLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        assert len(rows) > 0
        # Verify column names are standard PIDs.
        assert "RPM" in rows[0]
        assert "SPEED" in rows[0]
        assert "COOLANT_TEMP" in rows[0]
        assert "INTAKE_PRESSURE" in rows[0]
        # No Chinese headers should remain.
        for key in rows[0]:
            assert "车速" not in key
            assert "发动机" not in key

    def test_csvlog_unit_conversion(self, tmp_path: Path) -> None:
        """Imperial units are converted to metric."""
        sample = _copy_fixture(_CSVLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        # COOLANT_TEMP: 0 °F → -17.78 °C (first row)
        ct = float(rows[0]["COOLANT_TEMP"])
        assert ct == pytest.approx(-17.78, abs=0.01)
        # SPEED: 0 MPH → 0 km/h (first row)
        assert float(rows[0]["SPEED"]) == 0.0
        # Later row: 12.5 MPH → ~20.12 km/h
        row_with_speed = next(
            r for r in rows if float(r["SPEED"]) > 0
        )
        assert float(row_with_speed["SPEED"]) > 20.0

    def test_csvlog_timestamp_normalised(self, tmp_path: Path) -> None:
        """Timestamps are in YYYY-MM-DD HH:MM:SS format."""
        sample = _copy_fixture(_CSVLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        for row in rows:
            ts = row["Timestamp"]
            assert len(ts) == 19  # "YYYY-MM-DD HH:MM:SS"
            assert "上午" not in ts
            assert "下午" not in ts
            assert "." not in ts  # no sub-second

    def test_csvlog_dedup(self, tmp_path: Path) -> None:
        """Consecutive duplicate data rows are de-duplicated."""
        sample = _copy_fixture(_CSVLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        # The sample has 7 data rows, one pair of duplicates.
        assert len(rows) == 6


# ── Maxlog normalisation ─────────────────────────────────────────────

class TestNormalizeMaxlog:
    """obd_maxlog CSV → internal TSV conversion."""

    def test_end_to_end_maxlog(self, tmp_path: Path) -> None:
        """Full maxlog sample converts to parseable TSV."""
        sample = _copy_fixture(_MAXLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        assert len(rows) > 0
        assert "RPM" in rows[0]
        assert "SPEED" in rows[0]
        assert "COOLANT_TEMP" in rows[0]
        # Unit suffixes should be stripped.
        for key in rows[0]:
            assert "(rpm)" not in key
            assert "(km/h)" not in key

    def test_maxlog_timestamp_truncated(self, tmp_path: Path) -> None:
        """Millisecond precision is stripped from timestamps."""
        sample = _copy_fixture(_MAXLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        for row in rows:
            ts = row["Timestamp"]
            assert "." not in ts
            assert len(ts) == 19

    def test_maxlog_metadata_preserved(self, tmp_path: Path) -> None:
        """# metadata lines are present in the output file."""
        sample = _copy_fixture(_MAXLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        with open(result_path, encoding="utf-8") as fh:
            content = fh.read()
        assert "# VEHICLE INFORMATION" in content
        assert "#   VIN: JHMGK5830HX202404" in content

    def test_maxlog_nonstandard_columns_filtered(
        self, tmp_path: Path,
    ) -> None:
        """DTC_* and MONITOR_* columns are excluded."""
        sample = _copy_fixture(_MAXLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        for row in rows:
            for key in row:
                assert not key.startswith("DTC_")
                assert not key.startswith("MONITOR_")

    def test_maxlog_values_preserved(self, tmp_path: Path) -> None:
        """Numeric values pass through without conversion."""
        sample = _copy_fixture(_MAXLOG_SAMPLE, tmp_path)
        result_path = normalize_obd_file(sample)
        rows = parse_log_file(result_path)
        # First row: RPM=745, COOLANT_TEMP=32
        assert float(rows[0]["RPM"]) == 745.0
        assert float(rows[0]["COOLANT_TEMP"]) == 32.0


# ── Native TSV pass-through ──────────────────────────────────────────

class TestNativeTsvPassthrough:
    """Native TSV files are returned unchanged."""

    def test_native_returns_same_path(self) -> None:
        """normalize_obd_file returns the original path for native TSV."""
        result = normalize_obd_file(_NATIVE_LOG)
        assert result == _NATIVE_LOG

    def test_native_no_side_effects(self) -> None:
        """No .normalized.tsv file is created for native TSV."""
        normalize_obd_file(_NATIVE_LOG)
        normalised = _NATIVE_LOG.with_suffix(".normalized.tsv")
        assert not normalised.exists()

"""Tests for the OBD TSV log file parser."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from obd_agent.log_parser import (
    _extract_vin,
    _parse_dtc_list,
    _try_float,
    log_file_to_snapshots,
    parse_log_file,
    pseudonymise_vin,
    row_to_snapshot,
)
from obd_agent.schemas import OBDSnapshot

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_REAL_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"
_REAL_VIN = "JHMGK5830HX202404"


# ---------------------------------------------------------------------------
# pseudonymise_vin
# ---------------------------------------------------------------------------

class TestPseudonymiseVin:
    def test_deterministic(self) -> None:
        """Same VIN always produces the same pseudonymous ID."""
        a = pseudonymise_vin(_REAL_VIN)
        b = pseudonymise_vin(_REAL_VIN)
        assert a == b

    def test_format(self) -> None:
        """Output follows V-{8 hex chars} pattern."""
        result = pseudonymise_vin(_REAL_VIN)
        assert result.startswith("V-")
        hex_part = result[2:]
        assert len(hex_part) == 8
        # Should be uppercase hex.
        int(hex_part, 16)

    def test_matches_sha256_prefix(self) -> None:
        """Verify the hash matches a manual SHA-256 computation."""
        expected_hex = hashlib.sha256(_REAL_VIN.encode()).hexdigest()[:8].upper()
        assert pseudonymise_vin(_REAL_VIN) == f"V-{expected_hex}"

    def test_different_vins_differ(self) -> None:
        """Two different VINs produce different IDs."""
        a = pseudonymise_vin("VIN_AAA")
        b = pseudonymise_vin("VIN_BBB")
        assert a != b


# ---------------------------------------------------------------------------
# _extract_vin
# ---------------------------------------------------------------------------

class TestExtractVin:
    def test_bytearray_format(self) -> None:
        raw = f"bytearray(b'{_REAL_VIN}')"
        assert _extract_vin(raw) == _REAL_VIN

    def test_plain_string(self) -> None:
        assert _extract_vin(_REAL_VIN) == _REAL_VIN

    def test_na_returns_none(self) -> None:
        assert _extract_vin("N/A") is None

    def test_empty_returns_none(self) -> None:
        assert _extract_vin("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _extract_vin("   ") is None


# ---------------------------------------------------------------------------
# _parse_dtc_list
# ---------------------------------------------------------------------------

class TestParseDtcList:
    def test_empty_list(self) -> None:
        assert _parse_dtc_list("[]") == []

    def test_na(self) -> None:
        assert _parse_dtc_list("N/A") == []

    def test_empty_string(self) -> None:
        assert _parse_dtc_list("") == []

    def test_single_dtc(self) -> None:
        raw = "[('P0301', 'Cylinder 1 Misfire Detected')]"
        result = _parse_dtc_list(raw)
        assert len(result) == 1
        assert result[0] == ("P0301", "Cylinder 1 Misfire Detected")

    def test_multiple_dtcs(self) -> None:
        raw = "[('P0301', 'Cylinder 1 Misfire Detected'), ('P0171', 'System Too Lean Bank 1')]"
        result = _parse_dtc_list(raw)
        assert len(result) == 2
        assert result[0][0] == "P0301"
        assert result[1][0] == "P0171"

    def test_regex_fallback(self) -> None:
        """When ast.literal_eval fails, DTC codes are extracted via regex."""
        raw = "some garbage P0301 and P0420 text"
        result = _parse_dtc_list(raw)
        assert len(result) == 2
        assert result[0] == ("P0301", "")
        assert result[1] == ("P0420", "")


# ---------------------------------------------------------------------------
# _try_float
# ---------------------------------------------------------------------------

class TestTryFloat:
    def test_valid_float(self) -> None:
        assert _try_float("12.34") == 12.34

    def test_valid_int_string(self) -> None:
        assert _try_float("0") == 0.0

    def test_negative(self) -> None:
        assert _try_float("-10.94") == -10.94

    def test_non_numeric(self) -> None:
        assert _try_float("Gasoline") is None

    def test_empty(self) -> None:
        assert _try_float("") is None

    def test_na(self) -> None:
        assert _try_float("N/A") is None


# ---------------------------------------------------------------------------
# parse_log_file
# ---------------------------------------------------------------------------

class TestParseLogFile:
    def test_parses_all_rows(self) -> None:
        """158 data rows in the real log are all parsed."""
        rows = parse_log_file(_REAL_LOG)
        assert len(rows) == 158

    def test_all_rows_have_44_columns(self) -> None:
        """Every row has all 44 expected columns."""
        rows = parse_log_file(_REAL_LOG)
        expected_cols = {
            "Timestamp", "RPM", "SPEED", "THROTTLE_POS", "THROTTLE_POS_B",
            "ENGINE_LOAD", "ABSOLUTE_LOAD", "RELATIVE_THROTTLE_POS",
            "THROTTLE_ACTUATOR", "COOLANT_TEMP", "INTAKE_TEMP",
            "CATALYST_TEMP_B1S1", "MAF", "INTAKE_PRESSURE",
            "BAROMETRIC_PRESSURE", "FUEL_RAIL_PRESSURE_DIRECT", "FUEL_TYPE",
            "FUEL_STATUS", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
            "TIMING_ADVANCE", "O2_B1S2", "O2_S1_WR_CURRENT", "O2_SENSORS",
            "EGR_ERROR", "COMMANDED_EGR", "EVAPORATIVE_PURGE", "RUN_TIME",
            "WARMUPS_SINCE_DTC_CLEAR", "DISTANCE_W_MIL",
            "DISTANCE_SINCE_DTC_CLEAR", "CONTROL_MODULE_VOLTAGE",
            "ELM_VERSION", "ELM_VOLTAGE", "ACCELERATOR_POS_D",
            "ACCELERATOR_POS_E", "VIN", "CALIBRATION_ID", "CVN",
            "OBD_COMPLIANCE", "STATUS", "COMMANDED_EQUIV_RATIO",
            "GET_DTC", "GET_CURRENT_DTC", "CLEAR_DTC",
        }
        for i, row in enumerate(rows):
            missing = expected_cols - set(row.keys())
            assert not missing, f"Row {i} missing columns: {missing}"

    def test_first_row_values(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        row = rows[0]
        assert row["Timestamp"] == "2025-07-23 14:42:16"
        assert row["RPM"] == "0.00"
        assert row["THROTTLE_POS"] == "17.25"
        assert row["COOLANT_TEMP"] == "32.00"
        assert row["LONG_FUEL_TRIM_1"] == "-10.94"

    def test_footer_excluded(self) -> None:
        """Footer lines (blank, separator, 'Log End Time') are not parsed."""
        rows = parse_log_file(_REAL_LOG)
        for row in rows:
            assert "Log End Time" not in row.get("Timestamp", "")

    def test_missing_header_raises(self, tmp_path: Path) -> None:
        """A file without a Timestamp header raises ValueError."""
        bad_file = tmp_path / "bad.txt"
        bad_file.write_text("no header here\njust data\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Could not find column header"):
            parse_log_file(bad_file)

    def test_short_rows_skipped(self, tmp_path: Path) -> None:
        """Rows with fewer columns than the header are skipped."""
        content = (
            "Timestamp\tRPM\tSPEED\n"
            "---\n"
            "2025-01-01 00:00:00\t100\t50\n"
            "short\n"  # too few columns
            "2025-01-01 00:00:01\t200\t60\n"
        )
        f = tmp_path / "short.txt"
        f.write_text(content, encoding="utf-8")
        rows = parse_log_file(f)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# row_to_snapshot
# ---------------------------------------------------------------------------

class TestRowToSnapshot:
    def test_basic_conversion(self) -> None:
        """First row converts to a valid OBDSnapshot."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert isinstance(snap, OBDSnapshot)

    def test_timestamp_parsed(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.ts == datetime(2025, 7, 23, 14, 42, 16, tzinfo=timezone.utc)

    def test_vehicle_id_pseudonymised(self) -> None:
        """VIN is hashed, not stored raw."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.vehicle_id.startswith("V-")
        assert snap.vehicle_id != _REAL_VIN
        assert snap.vehicle_id == pseudonymise_vin(_REAL_VIN)

    def test_raw_vin_not_in_snapshot(self) -> None:
        """The raw VIN never appears in the serialised snapshot."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert _REAL_VIN not in snap.vehicle_id
        assert _REAL_VIN not in snap.model_dump_json()

    def test_vehicle_id_override(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0], vehicle_id="V-OVERRIDE")
        assert snap.vehicle_id == "V-OVERRIDE"

    def test_adapter_info(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.adapter.type == "ELM327 v1.4b"
        assert snap.adapter.port == "log-replay"

    def test_adapter_port_override(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0], adapter_port="/dev/ttyUSB0")
        assert snap.adapter.port == "/dev/ttyUSB0"

    def test_no_dtcs(self) -> None:
        """Real log rows with GET_DTC=[] produce no DTCEntry objects."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.dtc == []

    def test_numeric_pids_extracted(self) -> None:
        """Numeric PID values are extracted with correct units."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert "RPM" in snap.baseline_pids
        assert snap.baseline_pids["RPM"].value == 0.0
        assert snap.baseline_pids["RPM"].unit == "rpm"
        assert "COOLANT_TEMP" in snap.baseline_pids
        assert snap.baseline_pids["COOLANT_TEMP"].value == 32.0
        assert snap.baseline_pids["COOLANT_TEMP"].unit == "degC"

    def test_negative_pid_value(self) -> None:
        """Negative values (e.g. LONG_FUEL_TRIM_1 = -10.94) are preserved."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.baseline_pids["LONG_FUEL_TRIM_1"].value == -10.94
        assert snap.baseline_pids["LONG_FUEL_TRIM_1"].unit == "percent"

    def test_supported_pids_sorted(self) -> None:
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.supported_pids == sorted(snap.supported_pids)
        assert len(snap.supported_pids) > 0

    def test_expected_pid_count(self) -> None:
        """Each snapshot contains a reasonable set of numeric PIDs."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert len(snap.baseline_pids) >= 28

    def test_skip_columns_excluded(self) -> None:
        """Non-numeric metadata columns are not in baseline_pids."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        for skip_col in ("FUEL_TYPE", "VIN", "STATUS", "GET_DTC", "ELM_VERSION"):
            assert skip_col not in snap.baseline_pids

    def test_freeze_frame_empty(self) -> None:
        """TSV logs don't carry freeze-frame data."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        assert snap.freeze_frame == {}

    def test_fallback_timestamp(self) -> None:
        """Bad timestamp falls back to now(UTC)."""
        row = {"Timestamp": "not-a-date", "VIN": ""}
        snap = row_to_snapshot(row, vehicle_id="V-TEST")
        delta = abs((datetime.now(timezone.utc) - snap.ts).total_seconds())
        assert delta < 5

    def test_missing_vin_defaults_to_unknown(self) -> None:
        """Row without VIN column gets V-UNKNOWN."""
        row = {"Timestamp": "2025-01-01 00:00:00"}
        snap = row_to_snapshot(row)
        assert snap.vehicle_id == "V-UNKNOWN"

    def test_json_round_trip(self) -> None:
        """Snapshot serialises to JSON and deserialises back."""
        rows = parse_log_file(_REAL_LOG)
        snap = row_to_snapshot(rows[0])
        json_str = snap.model_dump_json()
        restored = OBDSnapshot.model_validate_json(json_str)
        assert restored.vehicle_id == snap.vehicle_id
        assert restored.ts == snap.ts
        assert len(restored.baseline_pids) == len(snap.baseline_pids)
        assert restored.baseline_pids["RPM"].value == snap.baseline_pids["RPM"].value


# ---------------------------------------------------------------------------
# log_file_to_snapshots  (end-to-end)
# ---------------------------------------------------------------------------

class TestLogFileToSnapshots:
    def test_returns_all_158_snapshots(self) -> None:
        snapshots = log_file_to_snapshots(_REAL_LOG)
        assert isinstance(snapshots, list)
        assert len(snapshots) == 158
        assert all(isinstance(s, OBDSnapshot) for s in snapshots)

    def test_all_same_vehicle_id(self) -> None:
        """All rows from the same log share the same pseudonymised VIN."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        ids = {s.vehicle_id for s in snapshots}
        assert len(ids) == 1
        assert ids.pop() == pseudonymise_vin(_REAL_VIN)

    def test_vehicle_id_override_applies_to_all(self) -> None:
        snapshots = log_file_to_snapshots(_REAL_LOG, vehicle_id="V-FLEET-42")
        assert all(s.vehicle_id == "V-FLEET-42" for s in snapshots)

    def test_timestamps_span_expected_range(self) -> None:
        """Timestamps cover 2025-07-23 14:42:16 through 14:47:04."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        assert snapshots[0].ts == datetime(2025, 7, 23, 14, 42, 16, tzinfo=timezone.utc)
        assert snapshots[-1].ts == datetime(2025, 7, 23, 14, 47, 4, tzinfo=timezone.utc)

    def test_timestamps_monotonically_non_decreasing(self) -> None:
        snapshots = log_file_to_snapshots(_REAL_LOG)
        for i in range(1, len(snapshots)):
            assert snapshots[i].ts >= snapshots[i - 1].ts

    def test_no_dtcs_in_any_row(self) -> None:
        """The real log was captured with no active DTCs."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        for snap in snapshots:
            assert snap.dtc == []

    def test_idle_engine_values(self) -> None:
        """All rows show engine-off idle state (RPM=0, SPEED=0)."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        for snap in snapshots:
            assert snap.baseline_pids["RPM"].value == 0.0
            assert snap.baseline_pids["SPEED"].value == 0.0

    def test_coolant_temp_constant(self) -> None:
        """Coolant temp stays at 32 degC (cold engine, not running)."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        for snap in snapshots:
            assert snap.baseline_pids["COOLANT_TEMP"].value == 32.0

    def test_first_row_unique_long_fuel_trim(self) -> None:
        """First row has LONG_FUEL_TRIM_1 = -10.94, others have 0.0."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        assert snapshots[0].baseline_pids["LONG_FUEL_TRIM_1"].value == -10.94
        for snap in snapshots[1:]:
            assert snap.baseline_pids["LONG_FUEL_TRIM_1"].value == 0.0

    def test_json_round_trip_first_and_last(self) -> None:
        """First and last snapshots survive JSON serialisation."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        for snap in [snapshots[0], snapshots[-1]]:
            json_str = snap.model_dump_json()
            restored = OBDSnapshot.model_validate_json(json_str)
            assert restored.vehicle_id == snap.vehicle_id
            assert restored.ts == snap.ts
            assert len(restored.baseline_pids) == len(snap.baseline_pids)

"""Tests for the OBD log summarizer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obd_agent.log_parser import log_file_to_snapshots
from obd_agent.log_summarizer import (
    CRITICAL_PIDS,
    LogSummary,
    _detect_anomalies,
    summarize_log_file,
    summarize_snapshots,
)
from obd_agent.schemas import OBDSnapshot

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_REAL_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"
_EXPECTED_JSON = _FIXTURES_DIR / "obd_log_20250723_144216.summary.json"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

@pytest.fixture()
def real_snapshots() -> list[OBDSnapshot]:
    return log_file_to_snapshots(_REAL_LOG)


@pytest.fixture()
def real_summary(real_snapshots: list[OBDSnapshot]) -> LogSummary:
    return summarize_snapshots(real_snapshots)


# ---------------------------------------------------------------------------
# summarize_snapshots – structure
# ---------------------------------------------------------------------------

class TestSummaryStructure:
    def test_returns_log_summary(self, real_summary: LogSummary) -> None:
        assert isinstance(real_summary, LogSummary)

    def test_vehicle_id(self, real_summary: LogSummary) -> None:
        assert real_summary.vehicle_id == "V-38615C39"

    def test_adapter(self, real_summary: LogSummary) -> None:
        assert real_summary.adapter == "ELM327 v1.4b"

    def test_pid_summary_keys(self, real_summary: LogSummary) -> None:
        """pid_summary contains exactly the 8 critical PIDs."""
        assert set(real_summary.pid_summary.keys()) == set(CRITICAL_PIDS)

    def test_json_round_trip(self, real_summary: LogSummary) -> None:
        """Summary survives JSON serialisation and deserialisation."""
        json_str = real_summary.model_dump_json()
        restored = LogSummary.model_validate_json(json_str)
        assert restored.vehicle_id == real_summary.vehicle_id
        assert restored.time_range == real_summary.time_range
        assert set(restored.pid_summary.keys()) == set(real_summary.pid_summary.keys())


# ---------------------------------------------------------------------------
# PID stats accuracy
# ---------------------------------------------------------------------------

class TestPIDStats:
    def test_rpm_all_zero(self, real_summary: LogSummary) -> None:
        rpm = real_summary.pid_summary["RPM"]
        assert rpm.min == 0.0
        assert rpm.max == 0.0
        assert rpm.mean == 0.0
        assert rpm.latest == 0.0
        assert rpm.unit == "rpm"

    def test_coolant_temp_constant(self, real_summary: LogSummary) -> None:
        ct = real_summary.pid_summary["COOLANT_TEMP"]
        assert ct.min == 32.0
        assert ct.max == 32.0
        assert ct.mean == 32.0
        assert ct.unit == "degC"

    def test_long_fuel_trim_range(self, real_summary: LogSummary) -> None:
        lft = real_summary.pid_summary["LONG_FUEL_TRIM_1"]
        assert lft.min == -10.94
        assert lft.max == 0.0
        assert lft.latest == 0.0
        assert lft.unit == "percent"

    def test_long_fuel_trim_mean(self, real_summary: LogSummary) -> None:
        """Mean of 157x 0.0 + 1x -10.94 = -10.94/158 ~ -0.07."""
        lft = real_summary.pid_summary["LONG_FUEL_TRIM_1"]
        assert lft.mean == pytest.approx(-0.07, abs=0.01)

    def test_intake_pressure_range(self, real_summary: LogSummary) -> None:
        ip = real_summary.pid_summary["INTAKE_PRESSURE"]
        assert ip.min == 99.0
        assert ip.max == 100.0
        assert ip.unit == "kPa"

    def test_throttle_pos_range(self, real_summary: LogSummary) -> None:
        tp = real_summary.pid_summary["THROTTLE_POS"]
        assert tp.min == 16.08
        assert tp.max == 17.25
        assert tp.latest == 16.08
        assert tp.unit == "percent"

    def test_speed_all_zero(self, real_summary: LogSummary) -> None:
        spd = real_summary.pid_summary["SPEED"]
        assert spd.min == 0.0
        assert spd.max == 0.0

    def test_engine_load_all_zero(self, real_summary: LogSummary) -> None:
        el = real_summary.pid_summary["ENGINE_LOAD"]
        assert el.min == 0.0
        assert el.max == 0.0


# ---------------------------------------------------------------------------
# Time range
# ---------------------------------------------------------------------------

class TestTimeRange:
    def test_start(self, real_summary: LogSummary) -> None:
        assert real_summary.time_range.start == "2025-07-23T14:42:16Z"

    def test_end(self, real_summary: LogSummary) -> None:
        assert real_summary.time_range.end == "2025-07-23T14:47:04Z"

    def test_duration(self, real_summary: LogSummary) -> None:
        assert real_summary.time_range.duration_seconds == 288

    def test_sample_count(self, real_summary: LogSummary) -> None:
        assert real_summary.time_range.sample_count == 158


# ---------------------------------------------------------------------------
# DTC deduplication
# ---------------------------------------------------------------------------

class TestDTCDedup:
    def test_no_dtcs_in_real_log(self, real_summary: LogSummary) -> None:
        assert real_summary.dtc_codes == []

    def test_dtc_dedup(self) -> None:
        """Duplicate DTC codes across snapshots are deduplicated."""
        from obd_agent.schemas import AdapterInfo, DTCEntry, PIDValue

        snaps = [
            OBDSnapshot(
                vehicle_id="V-TEST0001",
                adapter=AdapterInfo(port="sim"),
                dtc=[DTCEntry(code="P0301", desc="Misfire")],
                baseline_pids={"RPM": PIDValue(value=800, unit="rpm")},
                supported_pids=["RPM"],
            ),
            OBDSnapshot(
                vehicle_id="V-TEST0001",
                adapter=AdapterInfo(port="sim"),
                dtc=[
                    DTCEntry(code="P0301", desc="Misfire"),
                    DTCEntry(code="P0171", desc="Lean"),
                ],
                baseline_pids={"RPM": PIDValue(value=850, unit="rpm")},
                supported_pids=["RPM"],
            ),
        ]
        summary = summarize_snapshots(snaps)
        assert summary.dtc_codes == ["P0301", "P0171"]


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

class TestAnomalyDetection:
    def test_anomalies_detected(self, real_summary: LogSummary) -> None:
        """At least one anomaly is detected in the real log."""
        assert len(real_summary.anomalies) > 0

    def test_no_duplicate_anomalies(self, real_summary: LogSummary) -> None:
        """Range-shift and constant-then-change don't both fire for same PID."""
        lft = [a for a in real_summary.anomalies if "LONG_FUEL_TRIM_1" in a]
        assert len(lft) == 1, f"Expected 1 anomaly for LONG_FUEL_TRIM_1, got {lft}"
        tp = [a for a in real_summary.anomalies if "THROTTLE_POS" in a]
        assert len(tp) == 1, f"Expected 1 anomaly for THROTTLE_POS, got {tp}"

    def test_long_fuel_trim_anomaly(self, real_summary: LogSummary) -> None:
        """LONG_FUEL_TRIM_1 initial spike is flagged."""
        lft_anomalies = [a for a in real_summary.anomalies if "LONG_FUEL_TRIM_1" in a]
        assert len(lft_anomalies) == 1
        assert "-10.94" in lft_anomalies[0]

    def test_throttle_pos_anomaly(self, real_summary: LogSummary) -> None:
        """THROTTLE_POS initial value shift is flagged."""
        tp_anomalies = [a for a in real_summary.anomalies if "THROTTLE_POS" in a]
        assert len(tp_anomalies) == 1

    def test_anomalies_are_human_readable(self, real_summary: LogSummary) -> None:
        """Anomaly strings are non-empty and contain the PID name."""
        for anomaly in real_summary.anomalies:
            assert isinstance(anomaly, str)
            assert len(anomaly) > 10
            # Each anomaly should reference a known PID.
            assert any(pid in anomaly for pid in CRITICAL_PIDS)


# ---------------------------------------------------------------------------
# _detect_anomalies unit tests (crafted data)
# ---------------------------------------------------------------------------

class TestDetectAnomaliesUnit:
    def test_empty_values(self) -> None:
        assert _detect_anomalies("RPM", [], "rpm") == []

    def test_all_identical_no_anomaly(self) -> None:
        """All-constant values produce no anomalies (stdev=0, mode=all)."""
        values = [32.0] * 20
        assert _detect_anomalies("COOLANT_TEMP", values, "degC") == []

    def test_fewer_than_3_skips_range_shift(self) -> None:
        """Fewer than 3 values skips the range-shift heuristic."""
        values = [100.0, 0.0]
        anomalies = _detect_anomalies("RPM", values, "rpm")
        assert not any("initial=" in a for a in anomalies)

    def test_fewer_than_5_skips_constant_then_change(self) -> None:
        """Fewer than 5 values skips the constant-then-change heuristic."""
        values = [0.0, 0.0, 0.0, 5.0]
        anomalies = _detect_anomalies("RPM", values, "rpm")
        assert not any("predominantly" in a for a in anomalies)

    def test_out_of_range_flagged(self) -> None:
        """A coolant temp above 110°C is flagged as out-of-range."""
        values = [90.0, 95.0, 130.0]
        anomalies = _detect_anomalies("COOLANT_TEMP", values, "degC")
        assert any("outside typical range" in a for a in anomalies)
        assert any("130.0" in a for a in anomalies)

    def test_out_of_range_negative(self) -> None:
        """A value below the lower bound is flagged."""
        values = [-50.0, 20.0, 25.0]
        anomalies = _detect_anomalies("COOLANT_TEMP", values, "degC")
        assert any("outside typical range" in a for a in anomalies)

    def test_unknown_pid_no_range_check(self) -> None:
        """A PID not in _OPERATING_RANGES skips out-of-range detection."""
        values = [9999.0, 9999.0, 9999.0]
        anomalies = _detect_anomalies("UNKNOWN_PID", values, "volts")
        assert not any("outside typical range" in a for a in anomalies)

    def test_constant_then_change_fires_when_no_range_shift(self) -> None:
        """Constant-then-change fires if range-shift does not fire."""
        # 9 identical + 1 different, but the outlier is *last* so range-shift
        # (which checks first value) won't fire.
        values = [50.0] * 9 + [55.0]
        anomalies = _detect_anomalies("THROTTLE_POS", values, "percent")
        assert any("predominantly" in a for a in anomalies)
        assert not any("initial=" in a for a in anomalies)

    def test_range_shift_suppresses_constant_then_change(self) -> None:
        """When range-shift fires, constant-then-change is suppressed."""
        # First value is a big outlier, rest are constant.
        values = [100.0] + [0.0] * 19
        anomalies = _detect_anomalies("RPM", values, "rpm")
        assert any("initial=" in a for a in anomalies)
        assert not any("predominantly" in a for a in anomalies)


# ---------------------------------------------------------------------------
# Golden file
# ---------------------------------------------------------------------------

class TestGoldenFile:
    def test_output_matches_golden_json(self) -> None:
        """Summarizing the real log produces JSON identical to the golden file."""
        summary = summarize_log_file(_REAL_LOG)
        actual = json.loads(summary.model_dump_json())

        with open(_EXPECTED_JSON, encoding="utf-8") as fh:
            expected = json.load(fh)

        assert actual == expected

    def test_golden_file_structure(self) -> None:
        """Golden file has all required top-level keys."""
        with open(_EXPECTED_JSON, encoding="utf-8") as fh:
            data = json.load(fh)

        required_keys = {"vehicle_id", "adapter", "time_range", "dtc_codes",
                         "pid_summary", "anomalies"}
        assert required_keys <= set(data.keys())

    def test_golden_file_deserialises(self) -> None:
        """Golden file deserialises into a valid LogSummary."""
        with open(_EXPECTED_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        summary = LogSummary.model_validate(data)
        assert summary.vehicle_id == "V-38615C39"
        assert summary.time_range.sample_count == 158


# ---------------------------------------------------------------------------
# summarize_log_file convenience wrapper
# ---------------------------------------------------------------------------

class TestSummarizeLogFile:
    def test_convenience_wrapper(self) -> None:
        """summarize_log_file produces same result as manual pipeline."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        from_snapshots = summarize_snapshots(snapshots)
        from_file = summarize_log_file(_REAL_LOG)

        assert from_file == from_snapshots


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_snapshots_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            summarize_snapshots([])

    def test_single_snapshot(self) -> None:
        """A single-snapshot list produces a valid summary."""
        snapshots = log_file_to_snapshots(_REAL_LOG)
        summary = summarize_snapshots(snapshots[:1])
        assert summary.time_range.sample_count == 1
        assert summary.time_range.duration_seconds == 0
        assert "RPM" in summary.pid_summary

"""Tests for obd_agent.summary_formatter — format_summary_for_dify()."""

from __future__ import annotations

import pytest

from obd_agent.summary_formatter import format_summary_for_dify

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_DATA: dict = {
    "vehicle_id": "V-TEST1234",
    "time_range": {
        "start": "2025-07-23T14:42:16",
        "end": "2025-07-23T14:44:53",
        "duration_seconds": 157,
        "sample_count": 158,
    },
    "dtc_codes": ["P0171", "P0174"],
    "pid_summary": {
        "RPM": {"min": 780.0, "max": 2500.0, "mean": 1200.50, "latest": 900.0, "unit": "rpm"},
        "COOLANT_TEMP": {"min": 85.0, "max": 92.0, "mean": 88.00, "latest": 90.0, "unit": "degC"},
    },
    "value_statistics": {
        "stats": {
            "RPM": {"mean": 1200.5, "std": 350.1, "min": 780.0, "max": 2500.0, "valid_count": 158},
            "COOLANT_TEMP": {"mean": 88.0, "std": 2.1, "min": 85.0, "max": 92.0, "valid_count": 158},
        },
        "column_units": {"RPM": "rpm", "COOLANT_TEMP": "degC"},
        "resample_interval_seconds": 1.0,
    },
    "anomaly_events": [
        {
            "time_window": ["2025-07-23T14:43:00", "2025-07-23T14:43:10"],
            "signals": ["RPM", "THROTTLE_POS"],
            "pattern": "simultaneous_spike",
            "context": "RPM jumped from 800 to 2500",
            "severity": "high",
            "detector": "zscore",
            "score": 3.45,
        },
    ],
    "diagnostic_clues": [
        "Lean fuel mixture detected on bank 1 and bank 2",
        "RPM instability during idle",
    ],
    "clue_details": [
        {
            "rule_id": "FUEL_LEAN_DUAL_BANK",
            "category": "fuel_system",
            "clue": "Lean fuel mixture detected on bank 1 and bank 2",
            "evidence": ["P0171 present", "P0174 present"],
            "severity": "high",
        },
        {
            "rule_id": "RPM_IDLE_UNSTABLE",
            "category": "engine",
            "clue": "RPM instability during idle",
            "evidence": ["RPM std=350.1 during low speed"],
            "severity": "medium",
        },
    ],
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Full data → all fields populated correctly."""

    def test_parse_ok_is_yes(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert result["parse_ok"] == "YES"

    def test_vehicle_id(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert result["vehicle_id"] == "V-TEST1234"

    def test_time_range_formatted(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "2025-07-23T14:42:16" in result["time_range"]
        assert "157s" in result["time_range"]
        assert "158 samples" in result["time_range"]

    def test_dtc_codes_joined(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert result["dtc_codes"] == "P0171, P0174"

    def test_pid_summary_lines(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "RPM: min=780.0 max=2500.0 mean=1200.50" in result["pid_summary"]
        assert "COOLANT_TEMP:" in result["pid_summary"]

    def test_anomaly_events_formatted(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "[HIGH]" in result["anomaly_events"]
        assert "simultaneous_spike" in result["anomaly_events"]
        assert "score: 3.45" in result["anomaly_events"]

    def test_diagnostic_clues_formatted(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "[HIGH] FUEL_LEAN_DUAL_BANK:" in result["diagnostic_clues"]
        assert "P0171 present; P0174 present" in result["diagnostic_clues"]
        assert "[MEDIUM] RPM_IDLE_UNSTABLE:" in result["diagnostic_clues"]

    def test_all_9_keys_present(self):
        result = format_summary_for_dify(_FULL_DATA)
        expected_keys = {
            "parse_ok", "vehicle_id", "time_range", "dtc_codes",
            "pid_summary", "anomaly_events", "diagnostic_clues",
            "rag_query", "debug",
        }
        assert set(result.keys()) == expected_keys

    def test_all_values_are_strings(self):
        result = format_summary_for_dify(_FULL_DATA)
        for key, value in result.items():
            assert isinstance(value, str), f"{key} is {type(value).__name__}, expected str"


# ---------------------------------------------------------------------------
# RAG query cascade
# ---------------------------------------------------------------------------


class TestRagQueryCascade:
    """Verify the priority-based RAG query building."""

    def test_dtcs_present_includes_dtc_codes(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "DTC codes: P0171 P0174" in result["rag_query"]

    def test_clues_present_includes_clues(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "Diagnostic clues:" in result["rag_query"]

    def test_no_dtcs_no_clues_falls_to_anomalies(self):
        data = dict(_FULL_DATA)
        data["dtc_codes"] = []
        data["diagnostic_clues"] = []
        data["clue_details"] = []
        result = format_summary_for_dify(data)
        assert "Anomalies:" in result["rag_query"]
        assert "simultaneous_spike" in result["rag_query"]

    def test_no_dtcs_no_clues_no_anomalies_falls_to_pid_fluctuations(self):
        data = dict(_FULL_DATA)
        data["dtc_codes"] = []
        data["diagnostic_clues"] = []
        data["clue_details"] = []
        data["anomaly_events"] = []
        result = format_summary_for_dify(data)
        assert "PID fluctuations:" in result["rag_query"]
        assert "RPM" in result["rag_query"]

    def test_no_fluctuating_pids_gives_fallback(self):
        data = dict(_FULL_DATA)
        data["dtc_codes"] = []
        data["diagnostic_clues"] = []
        data["clue_details"] = []
        data["anomaly_events"] = []
        # all PIDs have min==max
        data["pid_summary"] = {
            "RPM": {"min": 800.0, "max": 800.0, "mean": 800.0, "latest": 800.0, "unit": "rpm"},
        }
        result = format_summary_for_dify(data)
        assert result["rag_query"] == "general OBD vehicle health check"

    def test_empty_everything_gives_fallback(self):
        data = {
            "vehicle_id": "V-EMPTY",
            "time_range": {},
            "dtc_codes": [],
            "pid_summary": {},
            "anomaly_events": [],
            "diagnostic_clues": [],
            "clue_details": [],
        }
        result = format_summary_for_dify(data)
        assert result["rag_query"] == "general OBD vehicle health check"


# ---------------------------------------------------------------------------
# Empty / malformed data
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Malformed or empty input → parse_ok="NO"."""

    def test_empty_dict(self):
        result = format_summary_for_dify({})
        # _format_impl should still succeed with defaults
        assert result["parse_ok"] == "YES"
        assert result["vehicle_id"] == ""
        assert result["dtc_codes"] == "None"
        assert result["pid_summary"] == "None"

    def test_none_input_returns_no(self):
        # None is not a dict — _format_impl will raise, caught by wrapper
        result = format_summary_for_dify(None)  # type: ignore[arg-type]
        assert result["parse_ok"] == "NO"
        assert "format error" in result["debug"]

    def test_non_dict_input_returns_no(self):
        result = format_summary_for_dify("not a dict")  # type: ignore[arg-type]
        assert result["parse_ok"] == "NO"

    def test_missing_pid_stats_keys_returns_no(self):
        """PID stats missing required keys should trigger error handling."""
        data = {
            "vehicle_id": "V-X",
            "pid_summary": {"RPM": {"min": 800}},  # missing max, mean, latest
        }
        result = format_summary_for_dify(data)
        assert result["parse_ok"] == "NO"
        assert "format error" in result["debug"]


# ---------------------------------------------------------------------------
# Field formatting details
# ---------------------------------------------------------------------------


class TestFieldFormatting:
    """Edge cases in individual field formatting."""

    def test_no_dtcs_shows_none(self):
        data = dict(_FULL_DATA, dtc_codes=[])
        result = format_summary_for_dify(data)
        assert result["dtc_codes"] == "None"

    def test_no_anomaly_events_shows_none(self):
        data = dict(_FULL_DATA, anomaly_events=[])
        result = format_summary_for_dify(data)
        assert result["anomaly_events"] == "None"

    def test_no_clue_details_shows_none(self):
        data = dict(_FULL_DATA, clue_details=[])
        result = format_summary_for_dify(data)
        assert result["diagnostic_clues"] == "None"

    def test_pid_unit_appears_in_summary(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert "rpm" in result["pid_summary"]
        assert "degC" in result["pid_summary"]

    def test_multiple_anomaly_events(self):
        data = dict(_FULL_DATA)
        data["anomaly_events"] = [
            {
                "signals": ["RPM"],
                "pattern": "spike",
                "context": "ctx1",
                "severity": "high",
                "score": 3.0,
            },
            {
                "signals": ["SPEED"],
                "pattern": "drop",
                "context": "ctx2",
                "severity": "low",
                "score": 1.5,
            },
        ]
        result = format_summary_for_dify(data)
        lines = result["anomaly_events"].split("\n")
        assert len(lines) == 2
        assert "[HIGH]" in lines[0]
        assert "[LOW]" in lines[1]

    def test_debug_field_contains_keys(self):
        result = format_summary_for_dify(_FULL_DATA)
        assert result["debug"].startswith("OK, keys=")

    def test_time_range_missing_fields_uses_defaults(self):
        data = dict(_FULL_DATA)
        data["time_range"] = {}
        result = format_summary_for_dify(data)
        assert "? to ?" in result["time_range"]
        assert "0s" in result["time_range"]

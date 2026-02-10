"""Tests for the diagnostic clue generator (APP-16)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from obd_agent.anomaly_detector import AnomalyEvent, AnomalyReport
from obd_agent.clue_generator import (
    DiagnosticClue,
    DiagnosticClueReport,
    _build_template_context,
    _eval_anomaly_check,
    _eval_dtc_check,
    _eval_signal_exists,
    _eval_stat_check,
    _eval_stat_compare,
    _evaluate_rule,
    _load_rules,
    _SignalNamespace,
    generate_clues,
    generate_clues_from_log_file,
)
from obd_agent.statistics_extractor import (
    SignalStatistics,
    SignalStats,
    extract_statistics,
)
from obd_agent.time_series_normalizer import normalize_log_file

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_REAL_LOG = _FIXTURES_DIR / "obd_log_20250723_144216.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIME_RANGE = (
    datetime(2025, 1, 1, tzinfo=timezone.utc),
    datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc),
)


def _make_signal_stats(**overrides: float) -> SignalStats:
    """Build a SignalStats with sensible defaults and optional overrides."""
    defaults = dict(
        mean=0.0, std=0.0, min=0.0, max=0.0,
        p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=0.0,
        autocorrelation_lag1=0.0, mean_abs_change=0.0, max_abs_change=0.0,
        energy=0.0, entropy=0.0, valid_count=100,
    )
    defaults.update(overrides)
    return SignalStats(**defaults)


def _make_statistics(
    signals: Dict[str, SignalStats],
    *,
    vehicle_id: str = "V-TEST",
    dtc_codes: List[str] | None = None,
) -> SignalStatistics:
    """Build a minimal SignalStatistics for testing."""
    return SignalStatistics(
        stats=signals,
        vehicle_id=vehicle_id,
        time_range=_TIME_RANGE,
        dtc_codes=dtc_codes or [],
        column_units={k: "unit" for k in signals},
        resample_interval_seconds=1.0,
    )


def _make_anomaly_report(
    events: List[AnomalyEvent] | None = None,
    *,
    dtc_codes: List[str] | None = None,
) -> AnomalyReport:
    """Build a minimal AnomalyReport for testing."""
    return AnomalyReport(
        events=tuple(events or []),
        vehicle_id="V-TEST",
        time_range=_TIME_RANGE,
        dtc_codes=dtc_codes or [],
        detection_params={"pen": 3.0},
    )


def _make_anomaly_event(
    *,
    signals: tuple = ("short_fuel_trim_1",),
    context: str = "off",
    severity: str = "low",
    score: float = 0.5,
) -> AnomalyEvent:
    """Build a minimal AnomalyEvent for testing."""
    return AnomalyEvent(
        time_window=_TIME_RANGE,
        signals=signals,
        pattern="test pattern",
        context=context,
        severity=severity,
        detector="changepoint",
        score=score,
    )


# ---------------------------------------------------------------------------
# TestDiagnosticClueDataclass
# ---------------------------------------------------------------------------


class TestDiagnosticClueDataclass:
    """Verify DiagnosticClue is frozen and evidence is a tuple."""

    def test_frozen(self) -> None:
        clue = DiagnosticClue(
            rule_id="TEST_001",
            category="statistical",
            clue="Test clue",
            evidence=("a=1",),
            severity="info",
        )
        with pytest.raises(FrozenInstanceError):
            clue.clue = "modified"  # type: ignore[misc]

    def test_evidence_is_tuple(self) -> None:
        clue = DiagnosticClue(
            rule_id="TEST_001",
            category="statistical",
            clue="Test clue",
            evidence=("a=1", "b=2"),
            severity="info",
        )
        assert isinstance(clue.evidence, tuple)
        assert len(clue.evidence) == 2


# ---------------------------------------------------------------------------
# TestDiagnosticClueReportDataclass
# ---------------------------------------------------------------------------


class TestDiagnosticClueReportDataclass:
    """Verify DiagnosticClueReport is frozen and to_dict works."""

    def test_frozen(self) -> None:
        report = DiagnosticClueReport(
            clues=(),
            vehicle_id="V-TEST",
            time_range=_TIME_RANGE,
            dtc_codes=[],
            rules_applied=0,
            rules_matched=0,
        )
        with pytest.raises(FrozenInstanceError):
            report.vehicle_id = "changed"  # type: ignore[misc]

    def test_to_dict_has_diagnostic_clues_key(self) -> None:
        clue = DiagnosticClue(
            rule_id="X", category="dtc", clue="text",
            evidence=("e",), severity="info",
        )
        report = DiagnosticClueReport(
            clues=(clue,),
            vehicle_id="V-TEST",
            time_range=_TIME_RANGE,
            dtc_codes=["P0300"],
            rules_applied=1,
            rules_matched=1,
        )
        d = report.to_dict()
        assert "diagnostic_clues" in d
        assert "clue_details" in d
        assert d["diagnostic_clues"] == ["text"]
        assert d["clue_details"][0]["rule_id"] == "X"


# ---------------------------------------------------------------------------
# TestStatCheck
# ---------------------------------------------------------------------------


class TestStatCheck:
    """Verify stat_check condition evaluation."""

    def test_match(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=30.0, mean=10.0),
        })
        cond = {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50}
        matched, evidence = _eval_stat_check(cond, stats)
        assert matched is True
        assert "engine_rpm.max=30.0" in evidence[0]

    def test_no_match(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=100.0),
        })
        cond = {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50}
        matched, evidence = _eval_stat_check(cond, stats)
        assert matched is False

    def test_nan_field_does_not_match(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=float("nan")),
        })
        cond = {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50}
        matched, evidence = _eval_stat_check(cond, stats)
        assert matched is False


# ---------------------------------------------------------------------------
# TestAnomalyCheck
# ---------------------------------------------------------------------------


class TestAnomalyCheck:
    """Verify anomaly_check condition evaluation."""

    def test_match_by_signal_and_context(self) -> None:
        event = _make_anomaly_event(signals=("short_fuel_trim_1",), context="off")
        report = _make_anomaly_report([event])
        cond = {"type": "anomaly_check", "signal": "short_fuel_trim_1", "context": "off", "min_count": 1}
        matched, evidence, count = _eval_anomaly_check(cond, report)
        assert matched is True
        assert count == 1

    def test_no_match_wrong_context(self) -> None:
        event = _make_anomaly_event(signals=("short_fuel_trim_1",), context="cruise")
        report = _make_anomaly_report([event])
        cond = {"type": "anomaly_check", "signal": "short_fuel_trim_1", "context": "off", "min_count": 1}
        matched, evidence, count = _eval_anomaly_check(cond, report)
        assert matched is False
        assert count == 0


# ---------------------------------------------------------------------------
# TestDtcCheck
# ---------------------------------------------------------------------------


class TestDtcCheck:
    """Verify dtc_check condition evaluation."""

    def test_absent_no_dtcs(self) -> None:
        cond = {"type": "dtc_check", "mode": "absent"}
        matched, evidence, dtcs_str = _eval_dtc_check(cond, [])
        assert matched is True

    def test_prefix_match(self) -> None:
        cond = {"type": "dtc_check", "mode": "prefix", "prefix": "P030"}
        matched, evidence, dtcs_str = _eval_dtc_check(cond, ["P0300", "P0171"])
        assert matched is True
        assert "P0300" in dtcs_str


# ---------------------------------------------------------------------------
# TestStatCompare
# ---------------------------------------------------------------------------


class TestStatCompare:
    """Verify stat_compare condition evaluation."""

    def test_match_ratio(self) -> None:
        stats = _make_statistics({
            "mass_airflow": _make_signal_stats(mean=2.0),
            "engine_load": _make_signal_stats(mean=50.0),
        })
        cond = {
            "type": "stat_compare",
            "signal_a": "mass_airflow", "field_a": "mean",
            "signal_b": "engine_load", "field_b": "mean",
            "op": "lt", "ratio": 0.1,
        }
        matched, evidence = _eval_stat_compare(cond, stats)
        assert matched is True  # 2.0 < 50.0 * 0.1 = 5.0
        assert any("mass_airflow.mean=2.0" in e for e in evidence)

    def test_no_match(self) -> None:
        stats = _make_statistics({
            "mass_airflow": _make_signal_stats(mean=10.0),
            "engine_load": _make_signal_stats(mean=50.0),
        })
        cond = {
            "type": "stat_compare",
            "signal_a": "mass_airflow", "field_a": "mean",
            "signal_b": "engine_load", "field_b": "mean",
            "op": "lt", "ratio": 0.1,
        }
        matched, evidence = _eval_stat_compare(cond, stats)
        assert matched is False  # 10.0 < 50.0 * 0.1 = 5.0 → False

    def test_nan_skips(self) -> None:
        stats = _make_statistics({
            "mass_airflow": _make_signal_stats(mean=float("nan")),
            "engine_load": _make_signal_stats(mean=50.0),
        })
        cond = {
            "type": "stat_compare",
            "signal_a": "mass_airflow", "field_a": "mean",
            "signal_b": "engine_load", "field_b": "mean",
            "op": "lt", "ratio": 0.1,
        }
        matched, evidence = _eval_stat_compare(cond, stats)
        assert matched is False

    def test_missing_signal_skips(self) -> None:
        stats = _make_statistics({
            "engine_load": _make_signal_stats(mean=50.0),
        })
        cond = {
            "type": "stat_compare",
            "signal_a": "mass_airflow", "field_a": "mean",
            "signal_b": "engine_load", "field_b": "mean",
            "op": "lt", "ratio": 0.1,
        }
        matched, evidence = _eval_stat_compare(cond, stats)
        assert matched is False


# ---------------------------------------------------------------------------
# TestSignalExists
# ---------------------------------------------------------------------------


class TestSignalExists:
    """Verify signal_exists condition evaluation."""

    def test_signal_present(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(),
        })
        cond = {"type": "signal_exists", "signal": "engine_rpm", "exists": True}
        matched, evidence = _eval_signal_exists(cond, stats)
        assert matched is True

    def test_signal_absent(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(),
        })
        cond = {"type": "signal_exists", "signal": "mass_airflow", "exists": True}
        matched, evidence = _eval_signal_exists(cond, stats)
        assert matched is False

    def test_signal_expected_absent(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(),
        })
        cond = {"type": "signal_exists", "signal": "mass_airflow", "exists": False}
        matched, evidence = _eval_signal_exists(cond, stats)
        assert matched is True


# ---------------------------------------------------------------------------
# TestEvaluateRule
# ---------------------------------------------------------------------------


class TestEvaluateRule:
    """Verify full rule evaluation with AND logic and template population."""

    def _make_rule(self, **overrides: Any) -> Dict[str, Any]:
        defaults = {
            "id": "TEST_001",
            "category": "statistical",
            "severity": "info",
            "description": "Test rule",
            "conditions": [
                {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50},
            ],
            "template": "RPM max is {engine_rpm.max}.",
        }
        defaults.update(overrides)
        return defaults

    def test_and_logic_all_match(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=30.0, std=5.0),
        })
        rule = self._make_rule(conditions=[
            {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50},
            {"type": "stat_check", "signal": "engine_rpm", "field": "std", "op": "lt", "value": 10},
        ])
        anomalies = _make_anomaly_report()
        ctx = _build_template_context(stats)
        clue = _evaluate_rule(rule, stats, anomalies, [], ctx)
        assert clue is not None
        assert clue.rule_id == "TEST_001"

    def test_and_logic_one_fails(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=30.0, std=15.0),
        })
        rule = self._make_rule(conditions=[
            {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50},
            {"type": "stat_check", "signal": "engine_rpm", "field": "std", "op": "lt", "value": 10},
        ])
        anomalies = _make_anomaly_report()
        ctx = _build_template_context(stats)
        clue = _evaluate_rule(rule, stats, anomalies, [], ctx)
        assert clue is None

    def test_template_populated_and_evidence(self) -> None:
        stats = _make_statistics({
            "engine_rpm": _make_signal_stats(max=25.0, mean=10.0),
        })
        rule = self._make_rule(
            template="RPM max={engine_rpm.max}, mean={engine_rpm.mean}.",
        )
        anomalies = _make_anomaly_report()
        ctx = _build_template_context(stats)
        clue = _evaluate_rule(rule, stats, anomalies, [], ctx)
        assert clue is not None
        assert "25.0" in clue.clue
        assert "10.0" in clue.clue
        assert len(clue.evidence) > 0
        assert "engine_rpm.max=25.0" in clue.evidence[0]


# ---------------------------------------------------------------------------
# TestGenerateCluesRealFixture
# ---------------------------------------------------------------------------


class TestGenerateCluesRealFixture:
    """Integration tests against the real OBD log fixture."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        if not _REAL_LOG.exists():
            pytest.skip("Real log fixture not found")
        ts = normalize_log_file(_REAL_LOG)
        from obd_agent.anomaly_detector import detect_anomalies
        self.stats = extract_statistics(ts)
        self.anomalies = detect_anomalies(ts)
        self.report = generate_clues(self.stats, self.anomalies)

    def test_engine_off_clue(self) -> None:
        rule_ids = [c.rule_id for c in self.report.clues]
        assert "STAT_001" in rule_ids, f"Expected STAT_001 in {rule_ids}"
        clue = next(c for c in self.report.clues if c.rule_id == "STAT_001")
        assert "off" in clue.clue.lower() or "RPM" in clue.clue

    def test_coolant_constant_clue(self) -> None:
        rule_ids = [c.rule_id for c in self.report.clues]
        assert "STAT_003" in rule_ids, f"Expected STAT_003 in {rule_ids}"
        clue = next(c for c in self.report.clues if c.rule_id == "STAT_003")
        assert "coolant" in clue.clue.lower() or "constant" in clue.clue.lower()

    def test_engine_load_low_clue(self) -> None:
        """Engine load is 0 throughout (engine off), so STAT_010 should fire."""
        rule_ids = [c.rule_id for c in self.report.clues]
        assert "STAT_010" in rule_ids, f"Expected STAT_010 in {rule_ids}"

    def test_no_dtc_clue(self) -> None:
        rule_ids = [c.rule_id for c in self.report.clues]
        assert "DTC_004" in rule_ids, f"Expected DTC_004 in {rule_ids}"
        clue = next(c for c in self.report.clues if c.rule_id == "DTC_004")
        assert "no dtc" in clue.clue.lower() or "No DTC" in clue.clue


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case handling."""

    def test_empty_rules_list(self) -> None:
        stats = _make_statistics({"engine_rpm": _make_signal_stats()})
        anomalies = _make_anomaly_report()
        report = generate_clues(stats, anomalies, rules=[])
        assert report.rules_applied == 0
        assert report.rules_matched == 0
        assert len(report.clues) == 0

    def test_missing_signal_graceful_skip(self) -> None:
        """A rule referencing a signal not in stats should not match (no error)."""
        stats = _make_statistics({
            "vehicle_speed": _make_signal_stats(max=0.0),
        })
        anomalies = _make_anomaly_report()
        rule = {
            "id": "SKIP_001",
            "category": "statistical",
            "severity": "info",
            "description": "Needs engine_rpm which is missing",
            "conditions": [
                {"type": "stat_check", "signal": "engine_rpm", "field": "max", "op": "le", "value": 50},
            ],
            "template": "Should not appear.",
        }
        report = generate_clues(stats, anomalies, rules=[rule])
        assert report.rules_matched == 0


# ---------------------------------------------------------------------------
# TestRuleLoading
# ---------------------------------------------------------------------------


class TestRuleLoading:
    """Verify YAML rule loading and validation."""

    def test_default_yaml_loads(self) -> None:
        rules = _load_rules()
        assert len(rules) >= 20
        ids = [r["id"] for r in rules]
        assert "STAT_001" in ids
        assert "DTC_004" in ids

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("not: a: list: [}", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            _load_rules(bad_file)

    def test_duplicate_rule_id_raises(self, tmp_path: Path) -> None:
        dup_file = tmp_path / "dup.yaml"
        dup_file.write_text(
            "- id: DUP\n  category: statistical\n  severity: info\n"
            "  conditions:\n    - type: stat_check\n      signal: x\n      field: max\n      op: le\n      value: 1\n"
            "  template: a\n"
            "- id: DUP\n  category: statistical\n  severity: info\n"
            "  conditions:\n    - type: stat_check\n      signal: x\n      field: max\n      op: le\n      value: 1\n"
            "  template: b\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Duplicate rule id"):
            _load_rules(dup_file)


# ---------------------------------------------------------------------------
# TestGenerateCluesFromLogFile
# ---------------------------------------------------------------------------


class TestGenerateCluesFromLogFile:
    """Verify the convenience wrapper produces equivalent results."""

    def test_equivalence_with_manual_pipeline(self) -> None:
        if not _REAL_LOG.exists():
            pytest.skip("Real log fixture not found")

        from obd_agent.anomaly_detector import detect_anomalies

        # Manual pipeline
        ts = normalize_log_file(_REAL_LOG)
        stats = extract_statistics(ts)
        anomalies = detect_anomalies(ts)
        manual = generate_clues(stats, anomalies)

        # Convenience wrapper
        auto = generate_clues_from_log_file(_REAL_LOG)

        assert manual.rules_applied == auto.rules_applied
        assert manual.rules_matched == auto.rules_matched
        assert [c.rule_id for c in manual.clues] == [c.rule_id for c in auto.clues]


# ---------------------------------------------------------------------------
# TestToDict
# ---------------------------------------------------------------------------


class TestToDict:
    """Verify to_dict produces JSON-serialisable output."""

    def test_json_round_trip(self) -> None:
        clue = DiagnosticClue(
            rule_id="T1",
            category="statistical",
            clue="Test",
            evidence=("a=1", "b=2"),
            severity="info",
        )
        report = DiagnosticClueReport(
            clues=(clue,),
            vehicle_id="V-TEST",
            time_range=_TIME_RANGE,
            dtc_codes=["P0300"],
            rules_applied=5,
            rules_matched=1,
        )
        d = report.to_dict()
        serialised = json.dumps(d)
        recovered = json.loads(serialised)
        assert recovered["diagnostic_clues"] == ["Test"]
        assert recovered["rules_applied"] == 5
        assert recovered["rules_matched"] == 1
        assert recovered["vehicle_id"] == "V-TEST"
        assert recovered["dtc_codes"] == ["P0300"]
        assert len(recovered["clue_details"]) == 1
        assert recovered["clue_details"][0]["evidence"] == ["a=1", "b=2"]

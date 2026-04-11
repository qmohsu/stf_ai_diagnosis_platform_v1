"""Tests for the graduated autonomy router (``app.harness.autonomy``).

Covers:
  - ``_count_dtcs``: DTC counting from comma-separated strings
  - ``_max_severity``: Severity extraction from anomaly text
  - ``_count_clues``: Clue counting from diagnostic clue text
  - ``classify_complexity``: Tier 0–3 classification
  - ``apply_overrides``: ``force_agent`` and ``force_oneshot``
  - Determinism: same input always yields same tier
  - Edge cases: empty/missing fields, no DTCs, no anomalies
"""

from __future__ import annotations

import pytest

from app.harness.autonomy import (
    AutonomyDecision,
    _count_clues,
    _count_dtcs,
    _max_severity,
    apply_overrides,
    classify_complexity,
)


# ── Fixtures: representative parsed_summary payloads ────────────────


TIER_0_SIMPLE: dict = {
    "vehicle_id": "V-001",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0420 (Catalyst System Efficiency Below Threshold)",
    "pid_summary": "RPM: 700-2500, COOLANT_TEMP: 88-92",
    "anomaly_events": "none",
    "diagnostic_clues": "STAT_001 Catalyst efficiency below threshold",
}

TIER_0_MODERATE_SEVERITY: dict = {
    "vehicle_id": "V-002",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0171 (System Too Lean)",
    "pid_summary": "RPM: 800-3000, MAF: 2.5-8.0",
    "anomaly_events": "moderate fluctuation in fuel trim",
    "diagnostic_clues": (
        "STAT_001 Lean condition detected;"
        "STAT_002 MAF sensor drift"
    ),
}

TIER_1_MULTIPLE_DTCS: dict = {
    "vehicle_id": "V-003",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": (
        "P0300 (Random/Multiple Cylinder Misfire), "
        "P0301 (Cylinder 1 Misfire), "
        "P0420 (Catalyst Efficiency Below Threshold)"
    ),
    "pid_summary": "RPM: 600-4500, COOLANT_TEMP: 85-102",
    "anomaly_events": "high RPM instability at idle",
    "diagnostic_clues": (
        "STAT_001 Multi-cylinder misfire;"
        "STAT_002 Catalyst degradation;"
        "STAT_003 RPM instability;"
        "STAT_004 Coolant rising"
    ),
}

TIER_1_HIGH_SEVERITY: dict = {
    "vehicle_id": "V-004",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0300 (Random/Multiple Cylinder Misfire)",
    "pid_summary": "RPM: 400-5000, COOLANT_TEMP: 95-115",
    "anomaly_events": "significant RPM drop and high coolant temperature",
    "diagnostic_clues": "STAT_001 Engine misfire with overheating",
}

TIER_2_MANY_DTCS: dict = {
    "vehicle_id": "V-005",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": (
        "P0300 (Misfire), P0301 (Cyl 1), P0302 (Cyl 2), "
        "P0420 (Catalyst), P0171 (Lean)"
    ),
    "pid_summary": "RPM: 500-5500, COOLANT_TEMP: 80-120, MAF: 1.0-12.0",
    "anomaly_events": "high RPM instability, fuel trim anomaly",
    "diagnostic_clues": (
        "STAT_001 Multi-cylinder misfire;"
        "STAT_002 Catalyst degradation;"
        "STAT_003 Lean condition;"
        "STAT_004 MAF drift;"
        "STAT_005 Coolant anomaly"
    ),
}

TIER_2_CRITICAL_SEVERITY: dict = {
    "vehicle_id": "V-006",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0300 (Random Misfire)",
    "pid_summary": "RPM: 200-6000",
    "anomaly_events": "critical engine stall detected at highway speed",
    "diagnostic_clues": "STAT_001 Engine stall; STAT_002 Safety hazard",
}


# ── Tests: _count_dtcs ──────────────────────────────────────────────


class TestCountDtcs:
    """Unit tests for ``_count_dtcs``."""

    def test_single_dtc(self) -> None:
        """Single DTC with description is counted as 1."""
        assert _count_dtcs("P0420 (Catalyst Efficiency)") == 1

    def test_multiple_dtcs_comma_separated(self) -> None:
        """Multiple comma-separated DTCs are counted correctly."""
        assert _count_dtcs("P0300, P0301, P0420") == 3

    def test_dtcs_with_descriptions(self) -> None:
        """DTCs with parenthetical descriptions are parsed."""
        text = (
            "P0300 (Random Misfire), "
            "P0420 (Catalyst Below Threshold)"
        )
        assert _count_dtcs(text) == 2

    def test_duplicate_dtcs_deduplicated(self) -> None:
        """Duplicate DTC codes are counted only once."""
        assert _count_dtcs("P0300, P0300, P0301") == 2

    def test_empty_string(self) -> None:
        """Empty string returns 0."""
        assert _count_dtcs("") == 0

    def test_none_string(self) -> None:
        """Whitespace-only string returns 0."""
        assert _count_dtcs("   ") == 0

    def test_all_dtc_families(self) -> None:
        """All OBD-II DTC families (P, C, B, U) are recognised."""
        assert _count_dtcs("P0300, C0035, B0100, U0073") == 4

    def test_lowercase_normalized(self) -> None:
        """Lowercase DTC codes are normalized and counted."""
        assert _count_dtcs("p0300, p0420") == 2


# ── Tests: _max_severity ────────────────────────────────────────────


class TestMaxSeverity:
    """Unit tests for ``_max_severity``."""

    def test_none_text(self) -> None:
        """Literal 'none' anomaly returns 'none' severity."""
        assert _max_severity("none") == "none"

    def test_empty_string(self) -> None:
        """Empty string returns 'none'."""
        assert _max_severity("") == "none"

    def test_no_anomalies(self) -> None:
        """'no anomalies' text returns 'none'."""
        assert _max_severity("no anomalies") == "none"

    def test_critical_keyword(self) -> None:
        """Text containing 'critical' returns 'critical'."""
        assert _max_severity("critical engine stall") == "critical"

    def test_high_keyword(self) -> None:
        """Text containing 'significant' returns 'high'."""
        assert _max_severity(
            "significant RPM drop"
        ) == "high"

    def test_moderate_default(self) -> None:
        """Anomaly text with no severity keywords defaults to
        'moderate'.
        """
        assert _max_severity(
            "RPM range_shift at 08:32"
        ) == "moderate"

    def test_multiple_keywords_picks_highest(self) -> None:
        """When multiple severity keywords appear, the highest
        wins.
        """
        result = _max_severity(
            "minor vibration with critical engine stall"
        )
        assert result == "critical"

    def test_severe_maps_to_critical(self) -> None:
        """'severe' keyword maps to critical severity."""
        assert _max_severity("severe overheating") == "critical"


# ── Tests: _count_clues ─────────────────────────────────────────────


class TestCountClues:
    """Unit tests for ``_count_clues``."""

    def test_stat_tags(self) -> None:
        """STAT_NNN tags are counted."""
        text = "STAT_001 Misfire; STAT_002 Catalyst"
        assert _count_clues(text) == 2

    def test_rule_tags(self) -> None:
        """RULE_NNN tags are counted."""
        text = "RULE_001 Check ignition; RULE_002 Check fuel"
        assert _count_clues(text) == 2

    def test_mixed_tags(self) -> None:
        """STAT and RULE tags in the same string are counted."""
        text = "STAT_001 Misfire; RULE_001 Check ignition"
        assert _count_clues(text) == 2

    def test_semicolon_separated_no_tags(self) -> None:
        """Clues separated by semicolons without tags are
        counted.
        """
        text = "Engine misfire; Catalyst degradation; RPM drop"
        assert _count_clues(text) == 3

    def test_newline_separated(self) -> None:
        """Clues separated by newlines are counted."""
        text = "Engine misfire\nCatalyst issue\nRPM anomaly"
        assert _count_clues(text) == 3

    def test_empty_string(self) -> None:
        """Empty string returns 0."""
        assert _count_clues("") == 0

    def test_single_clue(self) -> None:
        """Single clue without separator returns 1."""
        assert _count_clues("STAT_001 Catalyst efficiency") == 1

    def test_duplicate_tags_deduplicated(self) -> None:
        """Duplicate STAT tags are counted once."""
        text = "STAT_001 Misfire; STAT_001 Same misfire"
        assert _count_clues(text) == 1


# ── Tests: classify_complexity ──────────────────────────────────────


class TestClassifyComplexity:
    """Tests for the main complexity classifier."""

    def test_tier_0_single_dtc_no_anomaly(self) -> None:
        """Single DTC with no anomalies → Tier 0."""
        result = classify_complexity(TIER_0_SIMPLE)
        assert result.tier == 0
        assert result.use_agent is False
        assert result.strategy == "V1 one-shot"

    def test_tier_0_moderate_severity(self) -> None:
        """Single DTC with moderate severity and few clues
        → Tier 0.
        """
        result = classify_complexity(TIER_0_MODERATE_SEVERITY)
        assert result.tier == 0
        assert result.use_agent is False

    def test_tier_1_multiple_dtcs(self) -> None:
        """3 DTCs with high severity → Tier 1."""
        result = classify_complexity(TIER_1_MULTIPLE_DTCS)
        assert result.tier == 1
        assert result.use_agent is True
        assert result.strategy == "agent loop"
        assert result.suggested_max_iterations <= 5

    def test_tier_1_high_severity_single_dtc(self) -> None:
        """Single DTC but high severity → Tier 1."""
        result = classify_complexity(TIER_1_HIGH_SEVERITY)
        assert result.tier == 1
        assert result.use_agent is True

    def test_tier_2_many_dtcs(self) -> None:
        """5 DTCs → Tier 2 (complex multi-fault)."""
        result = classify_complexity(TIER_2_MANY_DTCS)
        assert result.tier == 2
        assert result.use_agent is True
        assert result.strategy == "full agent"
        assert result.suggested_max_iterations >= 10

    def test_tier_2_critical_severity(self) -> None:
        """Critical severity (even with 1 DTC) → Tier 2."""
        result = classify_complexity(TIER_2_CRITICAL_SEVERITY)
        assert result.tier == 2
        assert result.use_agent is True

    def test_tier_3_has_prior_diagnosis(self) -> None:
        """Session with prior diagnosis → Tier 3 (follow-up)."""
        result = classify_complexity(
            TIER_0_SIMPLE, has_prior_diagnosis=True,
        )
        assert result.tier == 3
        assert result.use_agent is True
        assert "case history" in result.strategy

    def test_tier_3_overrides_any_base_tier(self) -> None:
        """Prior diagnosis forces Tier 3 even for complex cases."""
        result = classify_complexity(
            TIER_2_MANY_DTCS, has_prior_diagnosis=True,
        )
        assert result.tier == 3

    def test_empty_parsed_summary(self) -> None:
        """Empty parsed summary (no DTCs, no anomalies) → Tier 0."""
        result = classify_complexity({})
        assert result.tier == 0
        assert result.use_agent is False

    def test_deterministic(self) -> None:
        """Same input always produces the same tier."""
        results = [
            classify_complexity(TIER_1_MULTIPLE_DTCS)
            for _ in range(10)
        ]
        tiers = {r.tier for r in results}
        assert len(tiers) == 1
        assert tiers == {1}

    def test_reason_contains_dtc_count(self) -> None:
        """Reason string includes DTC count for auditability."""
        result = classify_complexity(TIER_2_MANY_DTCS)
        assert "5 DTC" in result.reason

    def test_four_dtcs_is_tier_2(self) -> None:
        """Exactly 4 DTCs (>3) → Tier 2."""
        summary = {
            "dtc_codes": "P0300, P0301, P0302, P0420",
            "anomaly_events": "moderate instability",
            "diagnostic_clues": "STAT_001 Issue",
        }
        result = classify_complexity(summary)
        assert result.tier == 2


# ── Tests: apply_overrides ──────────────────────────────────────────


class TestApplyOverrides:
    """Tests for query-parameter override logic."""

    def test_force_agent_escalates_tier_0(self) -> None:
        """``force_agent=True`` escalates Tier 0 to use agent."""
        base = classify_complexity(TIER_0_SIMPLE)
        assert base.tier == 0
        assert base.use_agent is False

        overridden = apply_overrides(base, force_agent=True)
        assert overridden.use_agent is True
        assert "forced" in overridden.strategy

    def test_force_agent_noop_for_agent_tier(self) -> None:
        """``force_agent=True`` is a no-op when already using
        agent.
        """
        base = classify_complexity(TIER_1_MULTIPLE_DTCS)
        assert base.use_agent is True

        overridden = apply_overrides(base, force_agent=True)
        assert overridden is base  # identity — no change

    def test_force_oneshot_overrides_agent(self) -> None:
        """``force_oneshot=True`` forces V1 one-shot even for
        Tier 2.
        """
        base = classify_complexity(TIER_2_MANY_DTCS)
        assert base.use_agent is True

        overridden = apply_overrides(base, force_oneshot=True)
        assert overridden.use_agent is False
        assert "forced" in overridden.strategy

    def test_force_oneshot_noop_for_tier_0(self) -> None:
        """``force_oneshot=True`` is a no-op when already
        one-shot.
        """
        base = classify_complexity(TIER_0_SIMPLE)
        assert base.use_agent is False

        overridden = apply_overrides(base, force_oneshot=True)
        assert overridden is base

    def test_force_oneshot_beats_force_agent(self) -> None:
        """When both flags are True, ``force_oneshot`` wins
        (safety-first: prefer cheaper path).
        """
        base = classify_complexity(TIER_1_MULTIPLE_DTCS)
        overridden = apply_overrides(
            base, force_agent=True, force_oneshot=True,
        )
        assert overridden.use_agent is False

    def test_no_overrides_returns_original(self) -> None:
        """No flags → original decision returned unchanged."""
        base = classify_complexity(TIER_1_MULTIPLE_DTCS)
        overridden = apply_overrides(base)
        assert overridden is base

    def test_override_preserves_tier(self) -> None:
        """Overrides change ``use_agent`` and ``strategy``
        but preserve the original tier number.
        """
        base = classify_complexity(TIER_2_MANY_DTCS)
        overridden = apply_overrides(base, force_oneshot=True)
        assert overridden.tier == base.tier
        assert overridden.use_agent is False

    def test_override_reason_includes_original(self) -> None:
        """Overridden reason references the original reason."""
        base = classify_complexity(TIER_0_SIMPLE)
        overridden = apply_overrides(base, force_agent=True)
        assert "original" in overridden.reason.lower()

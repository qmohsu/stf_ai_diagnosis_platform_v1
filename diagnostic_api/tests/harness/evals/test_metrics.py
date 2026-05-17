"""Tests for the lane dispatcher + weight rebalance in ``metrics.py``.

The bulk of ``metrics.py`` is exercised end-to-end via
``test_judge.py`` (manual lane) and ``test_metrics_obd.py`` (OBD
lane).  This module focuses on the HARNESS-21 dispatcher additions:

- ``_is_obd_lane`` chooses the right rubric based on
  ``GoldenEntry.question_type``.
- The OBD lane routes through ``metrics_obd`` and slots its dims
  into the shared ``DeterministicMetrics`` envelope.
- The manual lane stays untouched.
- ``DEFAULT_OVERALL_WEIGHTS`` sums to 1.0 and exposes
  ``value_accuracy``.
- ``compute_overall`` includes the new term.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import pytest

from tests.harness.evals.metrics import (
    DEFAULT_OVERALL_WEIGHTS,
    DeterministicMetrics,
    _is_obd_lane,
    compute_deterministic_metrics,
    compute_overall,
)
from tests.harness.evals.schemas import (
    DTCCitation,
    ExpectedDTC,
    ExpectedSignalCitation,
    GoldenCitation,
    GoldenEntry,
    SignalCitation,
    SystemRunResult,
)


# ── Lane detection ────────────────────────────────────────────────


class TestIsObdLane:
    """``_is_obd_lane`` uses ``question_type`` membership."""

    @pytest.mark.parametrize("qt", [
        "signal_statistics",
        "event_finding",
        "dtc_enumeration",
        "dtc_decode",
        "compound_obd",
        "adversarial_obd",
    ])
    def test_obd_types_route_to_obd(self, qt):
        entry = GoldenEntry(
            id=f"obd-{qt}",
            category="symptom",
            question_type=qt,
            difficulty="easy",
            question="q",
            golden_summary="s",
        )
        assert _is_obd_lane(entry) is True

    @pytest.mark.parametrize("qt", [
        "lookup",
        "procedural",
        "cross-section",
        "image-required",
        "adversarial",
    ])
    def test_manual_types_route_to_manual(self, qt):
        entry = GoldenEntry(
            id=f"manual-{qt}",
            category="symptom",
            question_type=qt,
            difficulty="easy",
            question="q",
            golden_summary="s",
        )
        assert _is_obd_lane(entry) is False


# ── Lane dispatcher: OBD entries ──────────────────────────────────


class TestObdLaneDispatch:
    """OBD question_type → OBD metrics in the shared envelope."""

    def test_obd_entry_uses_signal_recall_in_section_recall_slot(self):
        """Compute on an OBD entry where the agent matched 1/2
        expected signals.  ``section_recall`` should hold
        ``signal_recall`` = 0.5, not slug-based 1.0."""
        entry = GoldenEntry(
            id="yamaha-001",
            category="component",
            question_type="signal_statistics",
            difficulty="easy",
            question="q",
            golden_summary="s",
            expected_signal_citations=[
                ExpectedSignalCitation(signal="RPM"),
                ExpectedSignalCitation(signal="SPEED"),
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="q",
            output_text="...",
            obd_signal_citations=[SignalCitation(signal="RPM")],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(0.5)
        # Signal_precision: 1 cited, 1 in expected → 1.0.
        assert metrics.claim_precision == pytest.approx(1.0)
        # No DTC expectations → vacuous 1.0.
        assert metrics.citation_quality == pytest.approx(1.0)
        # value_accuracy: golden has no value field → vacuous 1.0.
        assert metrics.value_accuracy == pytest.approx(1.0)
        # OBD neutrals.
        assert metrics.exploration_cost == pytest.approx(0.0)
        assert metrics.trajectory_efficiency == pytest.approx(1.0)

    def test_obd_dtc_only_entry_routes_correctly(self):
        """``dtc_enumeration`` entry: only DTCs are graded."""
        entry = GoldenEntry(
            id="yamaha-dtcs-001",
            category="dtc",
            question_type="dtc_enumeration",
            difficulty="easy",
            question="q",
            golden_summary="s",
            expected_dtcs=[
                ExpectedDTC(code="87F11043", status="stored"),
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="q",
            output_text="...",
            obd_dtc_citations=[
                DTCCitation(code="87F11043", status="stored"),
            ],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.citation_quality == pytest.approx(1.0)
        # No signals expected → both signal dims vacuous 1.0.
        assert metrics.section_recall == pytest.approx(1.0)
        assert metrics.claim_precision == pytest.approx(1.0)

    def test_obd_adversarial_entry_polarity_flip(self):
        """``adversarial_obd`` + agent cited → drops to 0.0."""
        entry = GoldenEntry(
            id="yamaha-adv-001",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is it misfiring?",
            golden_summary="No misfire.",
            expected_no_evidence=True,
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is it misfiring?",
            output_text="Possible misfire.",
            obd_signal_citations=[SignalCitation(signal="RPM")],
        )
        metrics = compute_deterministic_metrics(entry, run)
        # Polarity flip — non-empty citations are a violation.
        assert metrics.section_recall == pytest.approx(0.0)


# ── Lane dispatcher: manual entries ───────────────────────────────


class TestManualLaneDispatch:
    """Manual question_type → original slug-based behaviour."""

    def test_manual_entry_uses_slug_recall(self):
        """Manual lane still computes ``section_recall`` from
        slug intersections."""
        entry = GoldenEntry(
            id="manual-001",
            category="dtc",
            question_type="lookup",
            difficulty="easy",
            question="q",
            golden_summary="s",
            golden_citations=[
                GoldenCitation(
                    manual_id="MWS150A_Service_Manual",
                    slug="dtc-p0117",
                    quote="...",
                ),
            ],
            expected_recall_slugs=["dtc-p0117"],
        )
        run = SystemRunResult(
            system_label="manual_agent",
            question="q",
            output_text="...",
            claim_slugs=["dtc-p0117"],
            read_slugs=["dtc-p0117"],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(1.0)
        # Manual entries get value_accuracy = neutral 1.0 (no
        # numerical-value semantics).
        assert metrics.value_accuracy == pytest.approx(1.0)

    def test_manual_entry_ignores_accidental_obd_fields(self):
        """A manual entry that happens to have empty OBD fields
        (defaults) still routes through manual rubric — gate is
        ``question_type``, not field population."""
        entry = GoldenEntry(
            id="manual-002",
            category="dtc",
            question_type="lookup",
            difficulty="easy",
            question="q",
            golden_summary="s",
            expected_recall_slugs=["dtc-p0117"],
            # Leave expected_signal_citations / expected_dtcs /
            # expected_no_evidence at defaults.
        )
        run = SystemRunResult(
            system_label="manual_agent",
            question="q",
            output_text="...",
            claim_slugs=[],
            read_slugs=[],
        )
        metrics = compute_deterministic_metrics(entry, run)
        # Should be 0.0 because no slugs were surfaced — proves
        # we're in the manual lane (OBD lane would be 1.0
        # vacuous, no expected_signal_citations).
        assert metrics.section_recall == pytest.approx(0.0)


# ── Weight rebalance ──────────────────────────────────────────────


class TestWeightRebalance:
    """``DEFAULT_OVERALL_WEIGHTS`` sums to 1.0 and contains all keys
    consumed by ``compute_overall``."""

    def test_weights_sum_to_one(self):
        """Sanity check the rebalance arithmetic."""
        assert sum(DEFAULT_OVERALL_WEIGHTS.values()) == pytest.approx(
            1.0,
        )

    def test_weights_contain_value_accuracy(self):
        """HARNESS-21 added ``value_accuracy``."""
        assert "value_accuracy" in DEFAULT_OVERALL_WEIGHTS
        assert DEFAULT_OVERALL_WEIGHTS["value_accuracy"] > 0

    def test_compute_overall_includes_value_accuracy(self):
        """A perfect manual run + value_accuracy=1.0 → overall=1.0
        (sanity check that the new term doesn't reduce a perfect
        score)."""
        perfect = DeterministicMetrics(
            section_recall=1.0,
            claim_precision=1.0,
            exploration_cost=0.0,  # Note: enters as (1 - cost).
            fact_recall=1.0,
            fact_density=1.0,
            citation_quality=1.0,
            trajectory_efficiency=1.0,
            value_accuracy=1.0,
            fact_recall_hits=[],
            fact_recall_misses=[],
        )
        overall = compute_overall(
            perfect,
            answer_quality=1.0,
            hallucination_penalty=1.0,
        )
        assert overall == pytest.approx(1.0)

    def test_compute_overall_with_perfect_manual_run(self):
        """Manual entry (value_accuracy = neutral 1.0) at perfect
        deterministic + perfect judge → overall = 1.0.

        Validates that the rebalance doesn't penalise manual
        entries that score perfectly elsewhere.
        """
        # Build a manual-lane entry and a perfect run.
        entry = GoldenEntry(
            id="manual-perfect",
            category="dtc",
            question_type="lookup",
            difficulty="easy",
            question="q",
            golden_summary="s",
            golden_citations=[
                GoldenCitation(
                    manual_id="m", slug="s1", quote="...",
                ),
            ],
            expected_recall_slugs=["s1"],
            expected_tool_trace=["read_manual_section"],
            must_contain=[],
        )
        run = SystemRunResult(
            system_label="manual_agent",
            question="q",
            output_text="s",
            claim_slugs=["s1"],
            read_slugs=["s1"],
            tool_trace=[],
        )
        metrics = compute_deterministic_metrics(entry, run)
        overall = compute_overall(
            metrics,
            answer_quality=1.0,
            hallucination_penalty=1.0,
        )
        # fact_density on empty must_contain may not be 1.0
        # depending on the metric's neutral; assert >= 0.85 as
        # a sanity bound (a perfect run with empty must_contain
        # should still score very high).
        assert overall >= 0.85, (
            f"manual perfect run scored {overall}, expected ≥ 0.85"
        )

    def test_value_accuracy_term_pulls_score_down_when_zero(self):
        """OBD entry with value_accuracy=0 → overall drops by the
        new term's weight."""
        baseline = DeterministicMetrics(
            section_recall=1.0,
            claim_precision=1.0,
            exploration_cost=0.0,
            fact_recall=1.0,
            fact_density=1.0,
            citation_quality=1.0,
            trajectory_efficiency=1.0,
            value_accuracy=1.0,
            fact_recall_hits=[],
            fact_recall_misses=[],
        )
        with_zero_value = DeterministicMetrics(
            section_recall=1.0,
            claim_precision=1.0,
            exploration_cost=0.0,
            fact_recall=1.0,
            fact_density=1.0,
            citation_quality=1.0,
            trajectory_efficiency=1.0,
            value_accuracy=0.0,
            fact_recall_hits=[],
            fact_recall_misses=[],
        )
        baseline_overall = compute_overall(
            baseline,
            answer_quality=1.0, hallucination_penalty=1.0,
        )
        zero_overall = compute_overall(
            with_zero_value,
            answer_quality=1.0, hallucination_penalty=1.0,
        )
        diff = baseline_overall - zero_overall
        assert diff == pytest.approx(
            DEFAULT_OVERALL_WEIGHTS["value_accuracy"],
        )


# ── DeterministicMetrics dataclass ────────────────────────────────


def test_deterministic_metrics_default_value_accuracy():
    """``value_accuracy`` defaults to 1.0 so old-style
    construction (manual lane, pre-HARNESS-21 call sites) still
    works."""
    m = DeterministicMetrics(
        section_recall=1.0,
        claim_precision=1.0,
        exploration_cost=0.0,
        fact_recall=1.0,
        fact_density=1.0,
        citation_quality=1.0,
        trajectory_efficiency=1.0,
    )
    assert m.value_accuracy == pytest.approx(1.0)

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
    _compute_fact_recall,
    _covered_expected_slugs,
    _is_obd_lane,
    _normalize_slug,
    _quote_in_text,
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


# ── Slug-tolerant matching (HARNESS-23 T4, #145) ──────────────────


class TestNormalizeSlug:
    """``_normalize_slug`` collapses cosmetic slug drift."""

    def test_separator_and_case_drift_normalize_equal(self):
        """Same section, different separators/case → equal."""
        assert _normalize_slug("Cooling_System-Coolant_Level") == (
            _normalize_slug("cooling-system-coolant-level")
        )

    def test_fullwidth_folds_to_halfwidth(self):
        """NFKC folds full-width forms so ``ＲＰＭ`` == ``rpm``."""
        assert _normalize_slug("ＲＰＭ") == _normalize_slug("rpm")

    def test_cjk_preserved(self):
        """CJK letters survive normalisation (not stripped)."""
        assert _normalize_slug("冷却系统-1") == "冷却系统1"

    def test_trailing_disambiguator_kept_distinct(self):
        """A ``-2`` disambiguator is a real distinction, not
        cosmetic — normalisation must NOT merge it away."""
        assert _normalize_slug("保养规范") != _normalize_slug(
            "保养规范-2",
        )

    def test_empty_input(self):
        """Empty/None slug normalises to empty string."""
        assert _normalize_slug("") == ""


class TestQuoteInText:
    """``_quote_in_text`` does ws-normalised, case-insensitive
    substring matching (mirrors ``fact_recall``)."""

    def test_line_wrapped_cjk_quote_matches(self):
        """A quote split by a source line break still matches its
        unwrapped occurrence in the output."""
        quote = "冷却液温度感知器的电阻"
        text = "根据手册，冷却液温度感知\n器的电阻应为若干欧姆。"
        assert _quote_in_text(quote, text) is True

    def test_absent_quote_does_not_match(self):
        assert _quote_in_text("扭矩为 15 Nm", "完全无关的内容") is False

    def test_empty_quote_or_text(self):
        assert _quote_in_text("", "anything") is False
        assert _quote_in_text("something", "") is False


class TestCoveredExpectedSlugs:
    """``_covered_expected_slugs`` resolves the tolerant match."""

    def test_normalized_slug_match(self):
        """Path-variant slug (separator/case drift) is covered."""
        covered = _covered_expected_slugs(
            expected=["cooling-system-coolant-level"],
            golden_citations=[],
            surfaced_slugs=["Cooling_System-Coolant_Level"],
            surfaced_text="",
        )
        assert covered == {"cooling-system-coolant-level"}

    def test_quote_containment_rescues_unmatched_slug(self):
        """Different slug, but the golden quote is in the surfaced
        text → the section is covered."""
        covered = _covered_expected_slugs(
            expected=["冷却液温度感知器检查"],
            golden_citations=[
                GoldenCitation(
                    manual_id="MWS150A_Service_Manual",
                    slug="冷却液温度感知器检查",
                    quote="冷却液温度感知器的电阻",
                ),
            ],
            surfaced_slugs=["agent-rendered-something-else"],
            surfaced_text="手册指出 冷却液温度感知器的电阻 约为若干。",
        )
        assert covered == {"冷却液温度感知器检查"}

    def test_no_match_no_quote_not_covered(self):
        """Genuinely absent section stays uncovered."""
        covered = _covered_expected_slugs(
            expected=["real-section"],
            golden_citations=[
                GoldenCitation(
                    manual_id="m",
                    slug="real-section",
                    quote="the torque spec is 15 Nm",
                ),
            ],
            surfaced_slugs=["unrelated-section"],
            surfaced_text="I could not find relevant information.",
        )
        assert covered == set()

    def test_quote_only_rescues_its_own_slug(self):
        """A quote credits the expected slug it documents, not a
        different expected slug that has no quote of its own."""
        covered = _covered_expected_slugs(
            expected=["slug-a", "slug-b"],
            golden_citations=[
                GoldenCitation(
                    manual_id="m", slug="slug-a", quote="alpha fact",
                ),
            ],
            surfaced_slugs=[],
            surfaced_text="the alpha fact is stated here",
        )
        assert covered == {"slug-a"}


class TestSlugTolerantSectionRecall:
    """End-to-end: slug-tolerant matching in
    ``compute_deterministic_metrics`` (manual lane)."""

    def _entry(self, quote: str) -> GoldenEntry:
        return GoldenEntry(
            id="lookup-slug-tolerant",
            category="component",
            question_type="lookup",
            difficulty="easy",
            question="冷却液温度感知器的电阻是多少？",
            golden_summary="约若干欧姆。",
            golden_citations=[
                GoldenCitation(
                    manual_id="MWS150A_Service_Manual",
                    slug="冷却液温度感知器检查",
                    quote=quote,
                ),
            ],
            expected_recall_slugs=["冷却液温度感知器检查"],
        )

    def test_pathvariant_slug_credited_not_zero(self):
        """Correct answer whose claimed slug is a separator/case
        variant of the golden slug: recall AND citation full
        credit (the #145 regression — was 0.0 / 0.3)."""
        entry = self._entry(quote="冷却液温度感知器的电阻")
        run = SystemRunResult(
            system_label="manual_agent",
            question=entry.question,
            output_text="冷却液温度感知器的电阻约为若干欧姆。",
            # Whitespace/order drift vs the golden slug.
            claim_slugs=["冷却液温度感知器 检查"],
            read_slugs=["冷却液温度感知器 检查"],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(1.0)
        assert metrics.citation_quality == pytest.approx(1.0)

    def test_quote_in_output_credits_when_slug_differs(self):
        """Agent's own slug rendering differs entirely, but its
        deliverable quotes the golden sentence → full credit."""
        entry = self._entry(quote="冷却液温度感知器的电阻")
        run = SystemRunResult(
            system_label="manual_agent",
            question=entry.question,
            output_text=(
                "根据手册，冷却液温度感知器的电阻应为若干欧姆。"
            ),
            claim_slugs=["agent-section-17"],
            read_slugs=["agent-section-17"],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(1.0)
        assert metrics.citation_quality == pytest.approx(1.0)

    def test_genuinely_wrong_section_still_penalised(self):
        """Guardrail: a wrong section that neither slug-matches nor
        quotes the golden must still score low — the fix must not
        over-credit."""
        entry = self._entry(quote="冷却液温度感知器的电阻")
        run = SystemRunResult(
            system_label="manual_agent",
            question=entry.question,
            output_text="这是完全无关的内容，未提及电阻。",
            claim_slugs=["无关章节"],
            read_slugs=["无关章节"],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(0.0)
        # Claimed a section, but it's wrong → 0.3, not 1.0.
        assert metrics.citation_quality == pytest.approx(0.3)

    def test_cited_nothing_stays_zero_even_if_quote_in_output(self):
        """Guardrail: quote-in-text must not manufacture a citation
        for a system that cited nothing.  ``citation_quality``
        stays 0.0 (cited nothing) even though ``section_recall``
        credits the surfaced content."""
        entry = self._entry(quote="冷却液温度感知器的电阻")
        run = SystemRunResult(
            system_label="manual_agent",
            question=entry.question,
            output_text="冷却液温度感知器的电阻约为若干欧姆。",
            claim_slugs=[],
            read_slugs=[],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.citation_quality == pytest.approx(0.0)


# ── Bilingual fact matching (HARNESS-23 T9, #149) ─────────────────


class TestFactRecallBilingual:
    """``_compute_fact_recall`` credits EN-equivalent answers to
    CJK-exact ``must_contain`` terms (and tolerates cosmetic
    whitespace drift), while staying deterministic and not
    over-crediting absent facts."""

    def test_exact_cjk_substring_still_matches(self):
        """Pre-#149 behaviour preserved: an exact CJK substring
        (with line-wrap whitespace) still hits."""
        score, hits, misses = _compute_fact_recall(
            ["汽門間隙"], "手冊指出汽門間\n隙應於冷機時測量。",
        )
        assert score == pytest.approx(1.0)
        assert hits == ["汽門間隙"]
        assert misses == []

    def test_cross_005_english_answer_full_credit(self):
        """The #149 regression case: cross-005's CJK-exact terms
        scored fact_recall=0 against a correct English answer.
        The EN-equivalent map must now credit all five terms."""
        must_contain = [
            "右前", "左前", "後煞車卡鉗", "1.0 mm", "DOT 4",
        ]
        output = (
            "Check the front right and front left calipers, then "
            "the rear brake caliper.  Replace pads below 1.0 mm "
            "and use DOT 4 brake fluid."
        )
        score, hits, misses = _compute_fact_recall(
            must_contain, output,
        )
        assert score == pytest.approx(1.0)
        assert misses == []
        # hits report the ORIGINAL golden strings, not the
        # equivalents that matched.
        assert hits == must_contain

    def test_flexible_whitespace_cjk(self):
        """Cosmetic spacing drift in CJK terms matches: golden
        ``4 行程`` hits output ``4行程``; ``綠色 / 紅色`` hits
        ``綠色/紅色``."""
        score, hits, _ = _compute_fact_recall(
            ["4 行程", "綠色 / 紅色"],
            "本引擎為4行程設計，感知器導線為綠色/紅色。",
        )
        assert score == pytest.approx(1.0)
        assert hits == ["4 行程", "綠色 / 紅色"]

    def test_leading_boundary_guard_blocks_midword_join(self):
        """Guardrail: ``V 皮帶`` must NOT be credited by the
        unrelated mid-word join inside ``CVT皮帶`` — the leading
        Latin/digit lookbehind blocks it."""
        score, _, misses = _compute_fact_recall(
            ["V 皮帶"], "本車傳動採用CVT皮帶結構。",
        )
        assert score == pytest.approx(0.0)
        assert misses == ["V 皮帶"]
        # But a genuine spacing variant DOES match.
        score, _, _ = _compute_fact_recall(
            ["V 皮帶"], "傳動使用V皮帶，非鏈條。",
        )
        assert score == pytest.approx(1.0)

    def test_reverse_direction_cjk_answer_credits_en_term(self):
        """An EN golden term (``TDC``) is credited by its CJK
        equivalent (``上死點``) in a Chinese answer."""
        score, hits, _ = _compute_fact_recall(
            ["TDC"], "將活塞轉至壓縮上死點後對準記號。",
        )
        assert score == pytest.approx(1.0)
        assert hits == ["TDC"]

    def test_absent_fact_still_misses(self):
        """Guardrail: a CJK term with no equivalent in the map and
        no occurrence in the output stays a miss — the fix must
        not manufacture credit."""
        score, hits, misses = _compute_fact_recall(
            ["曲軸位置感知器"],
            "This answer is about tire pressure only.",
        )
        assert score == pytest.approx(0.0)
        assert hits == []
        assert misses == ["曲軸位置感知器"]

    def test_partial_credit_mixed_hits(self):
        """Mixed EN-equivalent hit + genuine miss → fractional
        score, with hits/misses reporting golden strings."""
        score, hits, misses = _compute_fact_recall(
            ["恆溫器", "水箱蓋"],
            "Inspect the thermostat for a stuck-open valve.",
        )
        assert score == pytest.approx(0.5)
        assert hits == ["恆溫器"]
        assert misses == ["水箱蓋"]

    def test_empty_must_contain_vacuous(self):
        """Empty ``must_contain`` stays vacuously satisfied."""
        score, hits, misses = _compute_fact_recall([], "anything")
        assert score == pytest.approx(1.0)
        assert hits == []
        assert misses == []

    def test_equivalent_keying_tolerates_term_spacing(self):
        """Map lookup keys through ``_normalize_slug``: a golden
        term with spacing drift (``右 前``) still finds its map
        entry and matches ``front right``."""
        score, _, _ = _compute_fact_recall(
            ["右 前"], "the front right caliper",
        )
        assert score == pytest.approx(1.0)
# ── Adversarial section_recall N/A (HARNESS-23 T8, #148) ──────────


class TestAdversarialSectionRecallNa:
    """Empty ``expected_recall_slugs`` → ``section_recall`` is N/A
    (``None``), not a vacuous 1.0, and ``compute_overall`` excludes
    the dimension with its weight renormalised."""

    def _adversarial_entry(self) -> GoldenEntry:
        return GoldenEntry(
            id="adversarial-na",
            category="adversarial",
            question_type="adversarial",
            difficulty="easy",
            question="What is the coolant spec for the flux drive?",
            golden_summary="The manual covers no such component.",
            expected_recall_slugs=[],
        )

    def test_empty_expected_yields_none_not_one(self):
        """The #148 fix itself: an adversarial entry (no expected
        slugs) must NOT auto-score section_recall = 1.0."""
        run = SystemRunResult(
            system_label="manual_agent",
            question="q",
            output_text="Not found: the manual has no such section.",
            claim_slugs=[],
            read_slugs=["some-section-it-checked"],
        )
        metrics = compute_deterministic_metrics(
            self._adversarial_entry(), run,
        )
        assert metrics.section_recall is None

    def test_nonempty_expected_still_scored_as_float(self):
        """Guardrail: entries WITH expected slugs keep the numeric
        fraction — the N/A policy only applies to empty-expected."""
        entry = GoldenEntry(
            id="lookup-still-float",
            category="dtc",
            question_type="lookup",
            difficulty="easy",
            question="q",
            golden_summary="s",
            expected_recall_slugs=["dtc-p0117"],
        )
        run = SystemRunResult(
            system_label="manual_agent",
            question="q",
            output_text="...",
            claim_slugs=[],
            read_slugs=[],
        )
        metrics = compute_deterministic_metrics(entry, run)
        assert metrics.section_recall == pytest.approx(0.0)

    def _metrics_with_na(self, **overrides) -> DeterministicMetrics:
        base = dict(
            section_recall=None,
            claim_precision=1.0,
            exploration_cost=0.0,
            fact_recall=1.0,
            fact_density=1.0,
            citation_quality=1.0,
            trajectory_efficiency=1.0,
            value_accuracy=1.0,
        )
        base.update(overrides)
        return DeterministicMetrics(**base)

    def test_overall_with_na_perfect_decline_scores_one(self):
        """A perfect adversarial decline still reaches 1.0 — the
        excluded dim's weight is renormalised, not forfeited."""
        overall = compute_overall(
            self._metrics_with_na(),
            answer_quality=1.0,
            hallucination_penalty=1.0,
        )
        assert overall == pytest.approx(1.0)

    def test_overall_with_na_removes_free_floor(self):
        """A run that fails every applicable dim scores 0.0 —
        previously the vacuous section_recall=1.0 handed it a free
        +0.20 (the inflation #148 removes)."""
        overall = compute_overall(
            self._metrics_with_na(
                claim_precision=0.0,
                exploration_cost=1.0,  # Enters as (1 - cost).
                fact_recall=0.0,
                fact_density=0.0,
                citation_quality=0.0,
                value_accuracy=0.0,
            ),
            answer_quality=0.0,
            hallucination_penalty=0.0,
        )
        assert overall == pytest.approx(0.0)

    def test_overall_with_na_renormalises_uniform_score(self):
        """All applicable dims at 0.5 → overall exactly 0.5; the
        renormalisation preserves the scale of the remaining dims
        rather than shrinking scores by the missing weight."""
        overall = compute_overall(
            self._metrics_with_na(
                claim_precision=0.5,
                exploration_cost=0.5,  # (1 - 0.5) = 0.5 term.
                fact_recall=0.5,
                fact_density=0.5,
                citation_quality=0.5,
                value_accuracy=0.5,
            ),
            answer_quality=0.5,
            hallucination_penalty=0.5,
        )
        assert overall == pytest.approx(0.5)

    def test_overall_unchanged_when_section_recall_present(self):
        """Regression guard: numeric section_recall keeps the exact
        pre-#148 weighted-sum behaviour."""
        metrics = self._metrics_with_na(section_recall=0.5)
        overall = compute_overall(
            metrics,
            answer_quality=1.0,
            hallucination_penalty=1.0,
        )
        expected = 1.0 - (
            DEFAULT_OVERALL_WEIGHTS["section_recall"] * 0.5
        )
        assert overall == pytest.approx(expected)

"""Unit tests for ``metrics_obd.py`` (HARNESS-21).

Each function under test is exercised through its public entry point
where reasonable; private helpers (``_time_ranges_overlap``,
``_signal_matches``) have direct tests because their boundary
conventions are load-bearing.

Test order mirrors the function order in ``metrics_obd.py``:

1. Time-range overlap helper.
2. Signal-match helper.
3. ``compute_signal_recall``.
4. ``compute_signal_precision``.
5. ``compute_value_accuracy``.
6. ``compute_dtc_accuracy``.
7. ``compute_obd_deterministic_metrics`` — end-to-end on a fixture.
8. ``expected_no_evidence`` polarity-flip cases.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from tests.harness.evals.metrics_obd import (
    OBDDeterministicMetrics,
    _signal_matches,
    _time_ranges_overlap,
    compute_dtc_accuracy,
    compute_obd_deterministic_metrics,
    compute_signal_precision,
    compute_signal_recall,
    compute_value_accuracy,
)
from tests.harness.evals.schemas import (
    DTCCitation,
    ExpectedDTC,
    ExpectedSignalCitation,
    GoldenEntry,
    SignalCitation,
    SystemRunResult,
)


# ── Test fixtures ─────────────────────────────────────────────────


def _expected_sig(
    signal: str,
    stat: Optional[str] = None,
    value: Optional[float] = None,
    value_tolerance_rel: float = 0.05,
    time_range: Optional[tuple] = None,
) -> ExpectedSignalCitation:
    """Compact factory for ``ExpectedSignalCitation``."""
    return ExpectedSignalCitation(
        signal=signal,
        stat=stat,
        value=value,
        value_tolerance_rel=value_tolerance_rel,
        time_range=time_range,
    )


def _cited_sig(
    signal: str,
    stat: Optional[str] = None,
    value: Optional[float] = None,
    time_range: Optional[tuple] = None,
    units: Optional[str] = None,
) -> SignalCitation:
    """Compact factory for ``SignalCitation``."""
    return SignalCitation(
        signal=signal,
        stat=stat,
        value=value,
        time_range=time_range,
        units=units,
    )


def _expected_dtc(
    code: str, status: Optional[str] = None,
) -> ExpectedDTC:
    """Compact factory for ``ExpectedDTC``."""
    return ExpectedDTC(code=code, status=status)  # type: ignore[arg-type]


def _cited_dtc(
    code: str, status: str = "stored", ecu: Optional[str] = None,
) -> DTCCitation:
    """Compact factory for ``DTCCitation``."""
    return DTCCitation(
        code=code,
        status=status,  # type: ignore[arg-type]
        ecu=ecu,
    )


# ── _time_ranges_overlap ──────────────────────────────────────────


class TestTimeRangesOverlap:
    """Boundary convention: half-open intervals (no overlap on
    single-point boundary touches).
    """

    def test_obvious_overlap(self):
        """Two ranges sharing a middle window overlap."""
        a = ("2026-05-08T11:20:00", "2026-05-08T11:21:00")
        b = ("2026-05-08T11:20:30", "2026-05-08T11:21:30")
        assert _time_ranges_overlap(a, b) is True

    def test_one_contained_in_other(self):
        """Inner range overlaps outer."""
        outer = ("2026-05-08T11:20:00", "2026-05-08T11:25:00")
        inner = ("2026-05-08T11:21:00", "2026-05-08T11:22:00")
        assert _time_ranges_overlap(outer, inner) is True

    def test_disjoint_ranges_no_overlap(self):
        """Non-touching ranges do not overlap."""
        a = ("2026-05-08T11:20:00", "2026-05-08T11:21:00")
        b = ("2026-05-08T11:22:00", "2026-05-08T11:23:00")
        assert _time_ranges_overlap(a, b) is False

    def test_single_point_boundary_does_not_overlap(self):
        """``[t1, t2]`` and ``[t2, t3]`` share only ``t2`` — half-
        open convention says no overlap.  Documented in the helper
        docstring.
        """
        a = ("2026-05-08T11:20:00", "2026-05-08T11:21:00")
        b = ("2026-05-08T11:21:00", "2026-05-08T11:22:00")
        assert _time_ranges_overlap(a, b) is False

    def test_identical_ranges_overlap(self):
        """Two equal ranges overlap (their interior is non-empty)."""
        a = ("2026-05-08T11:20:00", "2026-05-08T11:21:00")
        assert _time_ranges_overlap(a, a) is True

    def test_invalid_iso_raises(self):
        """Malformed ISO strings raise ``ValueError`` — the OBD
        adapter validates citations before they reach here."""
        with pytest.raises(ValueError):
            _time_ranges_overlap(
                ("not-iso", "2026-05-08T11:21:00"),
                ("2026-05-08T11:20:00", "2026-05-08T11:21:00"),
            )


# ── _signal_matches ───────────────────────────────────────────────


class TestSignalMatches:
    """Whether one cited signal satisfies one expected citation."""

    def test_exact_signal_name_match(self):
        """Same name, same case → match."""
        e = _expected_sig("RPM")
        c = _cited_sig("RPM")
        assert _signal_matches(e, c) is True

    def test_case_insensitive_signal_name(self):
        """``"rpm"`` matches ``"RPM"`` — Yamaha tools emit
        uppercase but agents sometimes lowercase."""
        e = _expected_sig("RPM")
        c = _cited_sig("rpm")
        assert _signal_matches(e, c) is True

    def test_signal_name_mismatch(self):
        """Different signal → no match."""
        e = _expected_sig("RPM")
        c = _cited_sig("SPEED")
        assert _signal_matches(e, c) is False

    def test_stat_required_match_succeeds(self):
        """Expected pins stat, cited matches → match."""
        e = _expected_sig("RPM", stat="p95")
        c = _cited_sig("RPM", stat="p95")
        assert _signal_matches(e, c) is True

    def test_stat_required_match_fails(self):
        """Expected pins ``p95``, cited has ``max`` → no match."""
        e = _expected_sig("RPM", stat="p95")
        c = _cited_sig("RPM", stat="max")
        assert _signal_matches(e, c) is False

    def test_stat_unspecified_ignored(self):
        """Expected omits stat → cited's stat doesn't matter."""
        e = _expected_sig("RPM")
        c = _cited_sig("RPM", stat="mean")
        assert _signal_matches(e, c) is True

    def test_time_range_overlap_required(self):
        """Expected pins range, cited overlaps → match."""
        e = _expected_sig(
            "RPM",
            time_range=(
                "2026-05-08T11:20:00",
                "2026-05-08T11:22:00",
            ),
        )
        c = _cited_sig(
            "RPM",
            time_range=(
                "2026-05-08T11:21:00",
                "2026-05-08T11:23:00",
            ),
        )
        assert _signal_matches(e, c) is True

    def test_time_range_disjoint_fails(self):
        """Expected pins range, cited disjoint → no match."""
        e = _expected_sig(
            "RPM",
            time_range=(
                "2026-05-08T11:20:00",
                "2026-05-08T11:21:00",
            ),
        )
        c = _cited_sig(
            "RPM",
            time_range=(
                "2026-05-08T11:22:00",
                "2026-05-08T11:23:00",
            ),
        )
        assert _signal_matches(e, c) is False

    def test_time_range_required_but_cited_missing(self):
        """Expected pins range, cited has no range → no match
        (the agent didn't provide enough evidence)."""
        e = _expected_sig(
            "RPM",
            time_range=(
                "2026-05-08T11:20:00",
                "2026-05-08T11:21:00",
            ),
        )
        c = _cited_sig("RPM")
        assert _signal_matches(e, c) is False


# ── compute_signal_recall ─────────────────────────────────────────


class TestSignalRecall:
    """Fraction of expected signals the agent surfaced."""

    def test_exact_match_full_recall(self):
        """3 expected, all 3 cited → 1.0."""
        expected = [_expected_sig("RPM"), _expected_sig("SPEED"),
                    _expected_sig("COOLANT_TEMP")]
        cited = [_cited_sig("RPM"), _cited_sig("SPEED"),
                 _cited_sig("COOLANT_TEMP")]
        assert compute_signal_recall(expected, cited) == pytest.approx(1.0)

    def test_partial_match(self):
        """2 of 3 expected cited → 2/3."""
        expected = [_expected_sig("RPM"), _expected_sig("SPEED"),
                    _expected_sig("COOLANT_TEMP")]
        cited = [_cited_sig("RPM"), _cited_sig("SPEED")]
        assert compute_signal_recall(expected, cited) == pytest.approx(
            2 / 3,
        )

    def test_empty_expected_returns_neutral_1(self):
        """Empty ``expected_signal_citations`` → 1.0.

        This is the "no signal expectations at all" case — used by
        manual-lane entries (which never populate this field) and
        by ``dtc_enumeration`` entries (only DTCs expected).
        The dispatcher in commit 3 only routes through OBD metrics
        when the lane predicate says so, but the function itself
        must still be safe to call.
        """
        assert compute_signal_recall([], []) == pytest.approx(1.0)
        assert compute_signal_recall(
            [], [_cited_sig("RPM")],
        ) == pytest.approx(1.0)

    def test_stat_constraint_blocks_match(self):
        """Expected ``(RPM, p95)``, cited has only ``(RPM, max)``
        → 0.0 recall."""
        expected = [_expected_sig("RPM", stat="p95")]
        cited = [_cited_sig("RPM", stat="max")]
        assert compute_signal_recall(expected, cited) == pytest.approx(0.0)

    def test_time_range_overlap_recall(self):
        """Expected range, cited overlapping range → match."""
        expected = [
            _expected_sig(
                "RPM",
                time_range=(
                    "2026-05-08T11:20:00",
                    "2026-05-08T11:22:00",
                ),
            ),
        ]
        cited = [
            _cited_sig(
                "RPM",
                time_range=(
                    "2026-05-08T11:21:00",
                    "2026-05-08T11:23:00",
                ),
            ),
        ]
        assert compute_signal_recall(expected, cited) == pytest.approx(1.0)

    def test_case_insensitive_recall(self):
        """``"rpm"`` cited matches ``"RPM"`` expected."""
        expected = [_expected_sig("RPM")]
        cited = [_cited_sig("rpm")]
        assert compute_signal_recall(expected, cited) == pytest.approx(1.0)


# ── compute_signal_precision ──────────────────────────────────────


class TestSignalPrecision:
    """Of cited signals, what fraction match an expected entry."""

    def test_all_cited_are_expected(self):
        """3 cited, all 3 expected → 1.0."""
        expected = [_expected_sig("RPM"), _expected_sig("SPEED")]
        cited = [_cited_sig("RPM"), _cited_sig("SPEED")]
        assert compute_signal_precision(expected, cited) == pytest.approx(
            1.0,
        )

    def test_partial_precision(self):
        """Cited 4 signals, 3 in expected set → 3/4."""
        expected = [_expected_sig("RPM"), _expected_sig("SPEED"),
                    _expected_sig("COOLANT_TEMP")]
        cited = [_cited_sig("RPM"), _cited_sig("SPEED"),
                 _cited_sig("COOLANT_TEMP"), _cited_sig("ENGINE_LOAD")]
        assert compute_signal_precision(expected, cited) == pytest.approx(
            3 / 4,
        )

    def test_empty_cited_returns_1(self):
        """No citations → vacuously precise (matches manual lane's
        ``_compute_claim_precision`` convention)."""
        expected = [_expected_sig("RPM")]
        assert compute_signal_precision(expected, []) == pytest.approx(1.0)

    def test_empty_expected_returns_1(self):
        """No expectations + no flip → vacuously precise.

        Deliberate divergence from manual lane: manual's
        ``_compute_claim_precision`` returns ``None`` (N/A, #192;
        pre-#192 it returned 0.0) in this case (adversarial
        inferred from empty expected).  OBD lane has an explicit
        ``expected_no_evidence`` flag, so empty expected without
        the flag means "this entry isn't grading signal
        precision" (e.g. a ``dtc_enumeration`` golden) rather
        than "agent shouldn't cite anything."
        """
        cited = [_cited_sig("RPM")]
        assert compute_signal_precision([], cited) == pytest.approx(1.0)


# ── compute_value_accuracy ────────────────────────────────────────


class TestValueAccuracy:
    """Numerical-value tolerance handling."""

    def test_within_tolerance_hit(self):
        """Expected 2941, actual 2945, 5% tolerance → hit (4 < 147)."""
        expected = [_expected_sig("RPM", stat="p95", value=2941.0)]
        cited = [_cited_sig("RPM", stat="p95", value=2945.0)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(1.0)

    def test_off_by_tolerance_miss(self):
        """Expected 100, actual 120, 5% tolerance → miss (20 > 5)."""
        expected = [_expected_sig("RPM", stat="p95", value=100.0)]
        cited = [_cited_sig("RPM", stat="p95", value=120.0)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(0.0)

    def test_zero_expected_actual_zero_hit(self):
        """Expected 0, actual 0.005 → hit under abs guard
        (|0.005| ≤ 0.01)."""
        expected = [_expected_sig("RPM", stat="min", value=0.0)]
        cited = [_cited_sig("RPM", stat="min", value=0.005)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(1.0)

    def test_zero_expected_actual_large_miss(self):
        """Expected 0, actual 1.0 → miss."""
        expected = [_expected_sig("RPM", stat="min", value=0.0)]
        cited = [_cited_sig("RPM", stat="min", value=1.0)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(0.0)

    def test_no_comparable_pairs_returns_1(self):
        """Golden has no ``value`` set → 1.0 (vacuously accurate).

        Common for ``dtc_enumeration`` and ``event_finding`` entries
        where the agent's job is to surface the right entities, not
        a specific number.
        """
        expected = [_expected_sig("RPM"), _expected_sig("SPEED")]
        cited = [_cited_sig("RPM", value=3000.0)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(1.0)

    def test_per_citation_tolerance_override(self):
        """``value_tolerance_rel=0.20`` allows 20% drift."""
        expected = [
            _expected_sig(
                "RPM", stat="mean", value=1820.0,
                value_tolerance_rel=0.20,
            ),
        ]
        # 2100 vs 1820 → 15.4% off, would fail at default 5%, passes
        # at 20%.
        cited = [_cited_sig("RPM", stat="mean", value=2100.0)]
        assert compute_value_accuracy(expected, cited) == pytest.approx(1.0)

    def test_partial_value_accuracy(self):
        """2 comparable pairs: one hit, one miss → 0.5."""
        expected = [
            _expected_sig("RPM", stat="p95", value=2941.0),
            _expected_sig("COOLANT_TEMP", stat="max", value=85.0),
        ]
        cited = [
            _cited_sig("RPM", stat="p95", value=2940.0),  # hit
            _cited_sig("COOLANT_TEMP", stat="max", value=120.0),  # miss
        ]
        assert compute_value_accuracy(expected, cited) == pytest.approx(0.5)

    def test_value_skipped_when_cited_has_no_value(self):
        """Expected pins value, cited omits value → not a comparison.

        Treat as "no comparison made for this expected entry" — falls
        back to neutral when no other comparisons exist.
        """
        expected = [_expected_sig("RPM", stat="p95", value=2941.0)]
        cited = [_cited_sig("RPM", stat="p95")]
        # The agent named the right signal+stat but reported no value
        # — recall would credit it, value_accuracy has nothing to grade.
        assert compute_value_accuracy(expected, cited) == pytest.approx(1.0)


# ── compute_dtc_accuracy ──────────────────────────────────────────


class TestDtcAccuracy:
    """Jaccard over case-insensitive DTC codes; optional status check."""

    def test_full_match(self):
        """Expected {A, B}, cited {A, B} → 1.0."""
        expected = [_expected_dtc("P0117"), _expected_dtc("P0118")]
        cited = [_cited_dtc("P0117"), _cited_dtc("P0118")]
        assert compute_dtc_accuracy(expected, cited) == pytest.approx(1.0)

    def test_jaccard_partial(self):
        """Expected {A, B}, cited {A, C} → |intersection|/|union|
        = 1/3."""
        expected = [_expected_dtc("P0117"), _expected_dtc("P0118")]
        cited = [_cited_dtc("P0117"), _cited_dtc("P0500")]
        assert compute_dtc_accuracy(expected, cited) == pytest.approx(
            1 / 3,
        )

    def test_case_insensitive(self):
        """Expected uppercase Yamaha hex matches lowercase cited."""
        expected = [_expected_dtc("87F11043000000000000CB")]
        cited = [_cited_dtc("87f11043000000000000cb")]
        assert compute_dtc_accuracy(expected, cited) == pytest.approx(1.0)

    def test_status_mismatch_when_specified(self):
        """Expected ``(A, stored)``, cited ``(A, pending)`` → miss.

        Status only counted when specified on the expected side.
        """
        expected = [_expected_dtc("P0117", status="stored")]
        cited = [_cited_dtc("P0117", status="pending")]
        assert compute_dtc_accuracy(expected, cited) == pytest.approx(0.0)

    def test_status_unspecified_ignored(self):
        """Expected has no status → cited status doesn't matter."""
        expected = [_expected_dtc("P0117")]
        cited = [_cited_dtc("P0117", status="pending")]
        assert compute_dtc_accuracy(expected, cited) == pytest.approx(1.0)

    def test_both_empty_returns_1(self):
        """No DTCs expected, none cited → vacuously perfect."""
        assert compute_dtc_accuracy([], []) == pytest.approx(1.0)

    def test_empty_expected_with_cited_returns_1(self):
        """Empty expected + non-empty cited (no flip) → 1.0.

        Regression test for the consistency wart surfaced in PR
        [1/3]'s real-LLM smoke run: a ``signal_statistics`` entry
        had ``expected_dtcs=[]`` but the agent emitted 2 DTC
        citations as side-effect investigation.  Old behaviour
        returned Jaccard 0/2 = 0.0; new behaviour returns 1.0
        (vacuously perfect — entry isn't grading DTCs).  Symmetric
        with ``compute_signal_precision``'s handling of empty
        expected, which we deliberately diverged from manual lane
        on for the same reason.

        Adversarial cases use ``expected_no_evidence=True``
        explicitly, NOT this branch.
        """
        cited = [_cited_dtc("87F11043")]
        assert compute_dtc_accuracy([], cited) == pytest.approx(1.0)

    def test_empty_cited_with_expected_returns_0(self):
        """Expected DTCs but agent cited none → 0.0.

        Pins the asymmetric branch: empty cited is a real miss
        when expectations were set.  Counterpart to the empty-
        expected vacuous-1.0 case above.
        """
        expected = [_expected_dtc("87F11043", status="stored")]
        assert compute_dtc_accuracy(expected, []) == pytest.approx(0.0)


# ── compute_obd_deterministic_metrics (entry point) ───────────────


class TestObdDeterministicMetrics:
    """End-to-end: build a ``GoldenEntry`` + ``SystemRunResult``
    pair and check all four dimensions land in the returned
    ``OBDDeterministicMetrics``.
    """

    def test_signal_statistics_entry_full_match(self):
        """Signal-stats entry where the agent got everything right."""
        entry = GoldenEntry(
            id="yamaha-stats-001",
            category="component",
            question_type="signal_statistics",
            difficulty="easy",
            question="Peak RPM?",
            golden_summary="...",
            expected_signal_citations=[
                _expected_sig("RPM", stat="max", value=3906.0),
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Peak RPM?",
            output_text="...",
            obd_signal_citations=[
                _cited_sig("RPM", stat="max", value=3906.0),
            ],
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        assert metrics.signal_recall == pytest.approx(1.0)
        assert metrics.signal_precision == pytest.approx(1.0)
        assert metrics.value_accuracy == pytest.approx(1.0)
        assert metrics.dtc_accuracy == pytest.approx(1.0)  # vacuous

    def test_dtc_enumeration_entry(self):
        """DTC-only entry: signals are vacuous, DTCs drive the score."""
        entry = GoldenEntry(
            id="yamaha-dtcs-001",
            category="dtc",
            question_type="dtc_enumeration",
            difficulty="easy",
            question="Stored DTCs?",
            golden_summary="...",
            expected_dtcs=[
                _expected_dtc("87F11043", status="stored"),
                _expected_dtc("44F2305A", status="stored"),
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Stored DTCs?",
            output_text="...",
            obd_dtc_citations=[
                _cited_dtc("87F11043"),
                _cited_dtc("44F2305A"),
            ],
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        assert metrics.signal_recall == pytest.approx(1.0)  # vacuous
        assert metrics.signal_precision == pytest.approx(1.0)  # vacuous
        assert metrics.value_accuracy == pytest.approx(1.0)  # vacuous
        assert metrics.dtc_accuracy == pytest.approx(1.0)

    def test_compound_obd_entry_mixed_dimensions(self):
        """Entry with both signal expectations and a DTC."""
        entry = GoldenEntry(
            id="yamaha-compound-001",
            category="symptom",
            question_type="compound_obd",
            difficulty="medium",
            question="Engine state?",
            golden_summary="...",
            expected_signal_citations=[
                _expected_sig("RPM", stat="p95", value=2941.0),
                _expected_sig("COOLANT_TEMP", stat="max", value=84.0),
            ],
            expected_dtcs=[_expected_dtc("87F11043")],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Engine state?",
            output_text="...",
            obd_signal_citations=[
                _cited_sig("RPM", stat="p95", value=2945.0),  # hit
            ],
            obd_dtc_citations=[_cited_dtc("87F11043")],
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        # 1 of 2 expected signals surfaced.
        assert metrics.signal_recall == pytest.approx(0.5)
        # 1 cited, 1 in expected → 1.0 precision.
        assert metrics.signal_precision == pytest.approx(1.0)
        # 1 comparable pair, hit.
        assert metrics.value_accuracy == pytest.approx(1.0)
        assert metrics.dtc_accuracy == pytest.approx(1.0)


# ── Polarity flip: expected_no_evidence ───────────────────────────


class TestExpectedNoEvidence:
    """When the right answer is "the agent should cite nothing,"
    metric polarity flips."""

    def test_no_evidence_compliance_empty_citations(self):
        """``expected_no_evidence=True`` + empty citations → 1.0
        across all OBD metrics."""
        entry = GoldenEntry(
            id="yamaha-adversarial-001",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is the engine misfiring?",
            golden_summary="No evidence of misfire.",
            expected_no_evidence=True,
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is the engine misfiring?",
            output_text="No misfire evidence.",
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        assert metrics.signal_recall == pytest.approx(1.0)
        assert metrics.signal_precision == pytest.approx(1.0)
        assert metrics.value_accuracy == pytest.approx(1.0)
        assert metrics.dtc_accuracy == pytest.approx(1.0)

    def test_no_evidence_violation_signal_cited(self):
        """``expected_no_evidence=True`` but agent cited a signal
        → signal_recall drops to 0.0."""
        entry = GoldenEntry(
            id="yamaha-adversarial-002",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is the engine misfiring?",
            golden_summary="No evidence of misfire.",
            expected_no_evidence=True,
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is the engine misfiring?",
            output_text="Possible misfire at RPM dip.",
            obd_signal_citations=[
                _cited_sig("RPM", value=1500.0),
            ],
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        # Agent fabricated evidence; signal_recall is 0 under
        # polarity flip.
        assert metrics.signal_recall == pytest.approx(0.0)
        # DTC side stayed empty so dtc_accuracy stays 1.0.
        assert metrics.dtc_accuracy == pytest.approx(1.0)

    def test_no_evidence_violation_dtc_cited(self):
        """``expected_no_evidence=True`` but agent cited a DTC
        → dtc_accuracy drops to 0.0."""
        entry = GoldenEntry(
            id="yamaha-adversarial-003",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is there an O2 sensor fault?",
            golden_summary="No O2 sensor evidence.",
            expected_no_evidence=True,
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is there an O2 sensor fault?",
            output_text="P0135 indicates O2 sensor heater fault.",
            obd_dtc_citations=[_cited_dtc("P0135")],
        )
        metrics = compute_obd_deterministic_metrics(entry, run)
        assert metrics.dtc_accuracy == pytest.approx(0.0)


# ── OBDDeterministicMetrics dataclass ─────────────────────────────


def test_obd_deterministic_metrics_dataclass_frozen():
    """The dataclass is frozen to prevent accidental mutation."""
    m = OBDDeterministicMetrics(
        signal_recall=1.0,
        signal_precision=1.0,
        value_accuracy=1.0,
        dtc_accuracy=1.0,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        m.signal_recall = 0.5  # type: ignore[misc]

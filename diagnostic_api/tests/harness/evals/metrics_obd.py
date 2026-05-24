"""Deterministic metrics for the OBD eval lane (HARNESS-21).

The OBD sub-agent emits structured ``SignalCitation`` and
``DTCCitation`` lists rather than the slug-anchored citations the
manual sub-agent produces, so the manual rubric's signal-recall
analogue (``section_recall``) doesn't fit.  This module provides
four OBD-native dimensions:

- ``signal_recall``    — fraction of expected signals surfaced.
- ``signal_precision`` — of cited signals, fraction in expected set.
- ``value_accuracy``   — for citations where both sides specify a
  numerical ``value``, fraction within tolerance.
- ``dtc_accuracy``     — Jaccard over case-insensitive DTC codes,
  with optional status check.

These four are folded into the eval's overall score by the
dispatcher in ``metrics.py`` (commit 3).  When a golden entry's
``expected_no_evidence`` flag is set (adversarial / dtc_decode
cases), the polarity of all four metrics flips: the agent is
graded on its restraint rather than on what it cited.

The module is pure: no LLM calls, no I/O, no dependency on
``metrics.py``.  Numerical comparison uses a 5% relative tolerance
by default, overrideable per-citation via
``ExpectedSignalCitation.value_tolerance_rel``.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from tests.harness.evals.schemas import (
    DTCCitation,
    ExpectedDTC,
    ExpectedSignalCitation,
    GoldenEntry,
    SignalCitation,
    SystemRunResult,
)


# ── Constants ────────────────────────────────────────────────────


_ZERO_EXPECTED_ABS_TOL = 0.01
"""Absolute-tolerance fallback when the expected value is exactly 0.

Relative tolerance collapses to ``0 * rel = 0`` at the origin, so a
deterministic stat like ``min_speed=0`` would only ever match an
agent that reported exactly ``0`` — too strict.  The 0.01 bound
accepts ``0.005`` (floating-point noise) but rejects ``1.0``."""


# ── Output dataclass ─────────────────────────────────────────────


@dataclass(frozen=True)
class OBDDeterministicMetrics:
    """Four OBD-native dimensions, each in ``[0.0, 1.0]``.

    Returned by ``compute_obd_deterministic_metrics``; consumed by
    the dispatcher in ``metrics.py`` which slots these values into
    the shared ``DeterministicMetrics`` envelope alongside the
    manual-lane dims (``fact_recall``, ``fact_density``,
    ``trajectory_efficiency``).

    Attributes:
        signal_recall: Fraction of ``expected_signal_citations``
            matched by the agent's ``obd_signal_citations``.  See
            ``compute_signal_recall``.
        signal_precision: Fraction of the agent's
            ``obd_signal_citations`` that appear in the expected
            set.  See ``compute_signal_precision``.
        value_accuracy: For citations where both sides specify a
            numerical ``value``, fraction within the per-citation
            relative tolerance (default 5%, zero-expected guard
            ``≤0.01`` absolute).  ``1.0`` when no comparable pairs.
            See ``compute_value_accuracy``.
        dtc_accuracy: Jaccard over case-insensitive DTC codes
            from ``expected_dtcs`` vs ``obd_dtc_citations``,
            with optional status equality.  See
            ``compute_dtc_accuracy``.
    """

    signal_recall: float
    signal_precision: float
    value_accuracy: float
    dtc_accuracy: float


# ── Helpers ──────────────────────────────────────────────────────


def _time_ranges_overlap(
    a: Tuple[str, str], b: Tuple[str, str],
) -> bool:
    """Return ``True`` iff two ISO time ranges share an open interior.

    Half-open convention: ``[t1, t2]`` and ``[t2, t3]`` do NOT
    overlap (they share only the boundary point ``t2``).  Two
    intervals overlap when ``a_start < b_end AND b_start < a_end``.

    Args:
        a: ``(start_iso, end_iso)`` pair (each parsable by
            ``datetime.fromisoformat``).
        b: Same shape.

    Returns:
        ``True`` if the interiors overlap.

    Raises:
        ValueError: If either boundary fails ISO parsing.  The
            OBD adapter validates citation shapes before they
            reach here, so this is treated as a programmer error
            rather than a graceful-degradation path.
    """
    a_start = datetime.fromisoformat(a[0])
    a_end = datetime.fromisoformat(a[1])
    b_start = datetime.fromisoformat(b[0])
    b_end = datetime.fromisoformat(b[1])
    return a_start < b_end and b_start < a_end


def _signal_matches(
    expected: ExpectedSignalCitation, cited: SignalCitation,
) -> bool:
    """Return ``True`` iff ``cited`` satisfies the ``expected`` shape.

    Match policy:

    - ``signal``     — case-insensitive equality, always required.
    - ``stat``       — equality when ``expected.stat`` is set; ignored
                       otherwise.
    - ``time_range`` — overlap when ``expected.time_range`` is set
                       (cited must also have a range — if cited
                       omitted its range, the agent didn't ground
                       its claim well enough to count).

    ``value`` and ``value_tolerance_rel`` are intentionally NOT used
    here — they're handled separately by ``compute_value_accuracy``,
    which is a distinct dimension from recall/precision.  A right-
    signal-wrong-value citation still counts for recall but loses
    on value_accuracy, so the report shows which part of the claim
    failed.

    Args:
        expected: Golden reference.
        cited: One ``SignalCitation`` from the agent.

    Returns:
        ``True`` if cited satisfies expected.
    """
    if expected.signal.casefold() != cited.signal.casefold():
        return False
    if expected.stat is not None and expected.stat != cited.stat:
        return False
    if expected.time_range is not None:
        if cited.time_range is None:
            return False
        if not _time_ranges_overlap(
            expected.time_range, cited.time_range,
        ):
            return False
    return True


def _value_within_tolerance(
    expected: float, actual: float, rel_tolerance: float,
) -> bool:
    """Numerical equality check with a zero-expected guard.

    For non-zero expected: ``|actual - expected| ≤ expected *
    rel_tolerance``.  For zero expected: ``|actual| ≤
    _ZERO_EXPECTED_ABS_TOL`` (relative tolerance is meaningless at
    the origin).
    """
    if expected == 0.0:
        return abs(actual) <= _ZERO_EXPECTED_ABS_TOL
    return abs(actual - expected) <= abs(expected) * rel_tolerance


# ── Public per-metric functions ──────────────────────────────────


def compute_signal_recall(
    expected: List[ExpectedSignalCitation],
    cited: List[SignalCitation],
    expected_no_evidence: bool = False,
) -> float:
    """Fraction of expected signal citations the agent surfaced.

    Definition: ``|{e in expected : exists c in cited with
    _signal_matches(e, c)}| / max(len(expected), 1)``.

    Empty ``expected`` returns 1.0 (no expectations to fail).

    When ``expected_no_evidence=True`` the polarity flips: the
    function returns 1.0 only if the agent cited NO signals, 0.0
    otherwise.  Used for adversarial-OBD entries where the right
    answer is honest refusal.

    Args:
        expected: Golden's expected signal citations.
        cited: Agent's ``SignalCitation`` list.
        expected_no_evidence: Adversarial flag from the golden.

    Returns:
        Score in ``[0.0, 1.0]``.
    """
    if expected_no_evidence:
        return 1.0 if not cited else 0.0
    if not expected:
        return 1.0
    matched = sum(
        1
        for e in expected
        if any(_signal_matches(e, c) for c in cited)
    )
    return matched / len(expected)


def compute_signal_precision(
    expected: List[ExpectedSignalCitation],
    cited: List[SignalCitation],
    expected_no_evidence: bool = False,
) -> float:
    """Fraction of cited signals that match an expected entry.

    Definition: ``|{c in cited : exists e in expected with
    _signal_matches(e, c)}| / max(len(cited), 1)``.

    Empty ``cited`` returns 1.0 (vacuously precise).  Empty
    ``expected`` (when ``expected_no_evidence`` is False) also
    returns 1.0 — this is a deliberate divergence from the manual
    lane's ``_compute_claim_precision``, which treats empty-expected
    as an adversarial signal and scores 0.0.  In the OBD lane,
    adversarial cases are flagged explicitly via
    ``expected_no_evidence``; an entry with empty
    ``expected_signal_citations`` and no flag (e.g. a
    ``dtc_enumeration`` entry) means "this entry isn't grading
    signal precision," so the agent gets a free pass.

    When ``expected_no_evidence=True`` the polarity flips: empty
    cited scores 1.0; non-empty scores 0.0.

    Args:
        expected: Golden's expected signal citations.
        cited: Agent's ``SignalCitation`` list.
        expected_no_evidence: Adversarial flag from the golden.

    Returns:
        Score in ``[0.0, 1.0]``.
    """
    if expected_no_evidence:
        return 1.0 if not cited else 0.0
    if not cited:
        return 1.0
    if not expected:
        # Entry isn't testing signal precision (typical for
        # dtc_enumeration / event_finding-only goldens); don't
        # penalize the agent for side-effect citations.
        return 1.0
    matched = sum(
        1
        for c in cited
        if any(_signal_matches(e, c) for e in expected)
    )
    return matched / len(cited)


def compute_value_accuracy(
    expected: List[ExpectedSignalCitation],
    cited: List[SignalCitation],
    expected_no_evidence: bool = False,
) -> float:
    """Fraction of comparable (signal, stat, value) pairs within
    tolerance.

    A "comparable pair" is one where:

    1. ``_signal_matches(expected, cited)`` is True (same signal,
       same stat if pinned, overlapping range if pinned), AND
    2. Both ``expected.value`` and ``cited.value`` are non-``None``.

    For each comparable pair, the per-citation
    ``value_tolerance_rel`` (default 5%) is applied via
    ``_value_within_tolerance``.

    When no comparable pairs exist (golden has no ``value`` fields,
    or the agent didn't report values), returns 1.0 — there's
    nothing to grade, so no penalty.

    When ``expected_no_evidence=True`` returns 1.0 unconditionally
    (the polarity is captured by signal_recall and dtc_accuracy;
    value_accuracy has no meaning when no values are expected).

    Args:
        expected: Golden's expected signal citations.
        cited: Agent's ``SignalCitation`` list.
        expected_no_evidence: Adversarial flag from the golden.

    Returns:
        Score in ``[0.0, 1.0]``.
    """
    if expected_no_evidence:
        return 1.0

    hits = 0
    comparisons = 0
    for e in expected:
        if e.value is None:
            continue
        for c in cited:
            if c.value is None:
                continue
            if not _signal_matches(e, c):
                continue
            comparisons += 1
            if _value_within_tolerance(
                e.value, c.value, e.value_tolerance_rel,
            ):
                hits += 1
            # Don't break — multiple cited entries could match
            # the same expected (e.g. agent reports both a value
            # and a stat on the same signal).  Each is graded.

    if comparisons == 0:
        return 1.0
    return hits / comparisons


def compute_dtc_accuracy(
    expected: List[ExpectedDTC],
    cited: List[DTCCitation],
    expected_no_evidence: bool = False,
) -> float:
    """Jaccard over case-insensitive DTC codes; optional status check.

    Definition (no-flip): ``|expected ∩ cited| / |expected ∪ cited|``
    where set membership is decided by case-insensitive code
    equality plus, when ``expected[i].status`` is set, status
    equality.

    Vacuous-1.0 cases (none of which mean "perfect agent" — they
    mean "this entry isn't grading DTC accuracy"):

    - Both empty: no DTC expectations, no DTC citations → 1.0.
    - Empty expected, non-empty cited (without
      ``expected_no_evidence``): the golden isn't testing DTC
      accuracy (e.g. a ``signal_statistics`` question), but the
      agent cited DTCs as a side effect of investigation.  Returns
      1.0 — don't penalise.  Deliberate divergence from a strict
      Jaccard 0/N; symmetric with ``compute_signal_precision``'s
      handling of empty-expected (see that function's docstring
      for the rationale).  Adversarial cases use
      ``expected_no_evidence`` explicitly, not this branch.

    When ``expected_no_evidence=True`` the polarity flips: empty
    cited scores 1.0; non-empty scores 0.0.

    Args:
        expected: Golden's expected DTCs.
        cited: Agent's ``DTCCitation`` list.
        expected_no_evidence: Adversarial flag from the golden.

    Returns:
        Score in ``[0.0, 1.0]``.
    """
    if expected_no_evidence:
        return 1.0 if not cited else 0.0
    if not expected:
        # Entry isn't testing DTC accuracy (typical for
        # signal_statistics / event_finding goldens); don't
        # penalise side-effect DTC citations.  Matches the
        # symmetric handling in compute_signal_precision.
        return 1.0
    if not cited:
        # Expected DTCs but agent cited none — strict miss
        # (Jaccard 0/|expected|).  Falls through to the main
        # path which returns 0/|expected|=0.0.
        pass

    # Match policy: each cited entry is considered "in the
    # expected set" iff its (lowercased code, status?) tuple
    # matches an expected entry.  We compute intersection by
    # matching cited entries against expected ones.
    def _dtc_match(e: ExpectedDTC, c: DTCCitation) -> bool:
        if e.code.casefold() != c.code.casefold():
            return False
        if e.status is not None and e.status != c.status:
            return False
        return True

    matched_expected = {
        i for i, e in enumerate(expected)
        if any(_dtc_match(e, c) for c in cited)
    }
    matched_cited = {
        j for j, c in enumerate(cited)
        if any(_dtc_match(e, c) for e in expected)
    }
    intersection = max(len(matched_expected), len(matched_cited))
    # |union| = |expected| + |cited| − |intersection|; using the
    # "matched count" symmetry keeps the formula honest when one
    # cited entry covers two expected ones (treat as min(|m_e|,
    # |m_c|) for the intersection size to avoid double-counting).
    intersection = min(len(matched_expected), len(matched_cited))
    union = (
        len(expected) + len(cited) - intersection
    )
    if union == 0:
        return 1.0
    return intersection / union


# ── Public entry point ───────────────────────────────────────────


def compute_obd_deterministic_metrics(
    entry: GoldenEntry, run: SystemRunResult,
) -> OBDDeterministicMetrics:
    """Compute all four OBD dimensions for one (entry, run) pair.

    Called by the dispatcher in ``metrics.py`` when
    ``entry.question_type`` is one of the six OBD types (see
    ``OBD_QUESTION_TYPES`` in ``schemas.py``).

    Args:
        entry: Golden reference.  ``expected_signal_citations``,
            ``expected_dtcs``, and ``expected_no_evidence`` drive
            the scoring.
        run: System run.  ``obd_signal_citations`` and
            ``obd_dtc_citations`` are the citation evidence
            graded against the expectations.

    Returns:
        ``OBDDeterministicMetrics`` populated for all four dims.
    """
    flip = entry.expected_no_evidence

    signal_recall = compute_signal_recall(
        entry.expected_signal_citations,
        run.obd_signal_citations,
        expected_no_evidence=flip,
    )
    signal_precision = compute_signal_precision(
        entry.expected_signal_citations,
        run.obd_signal_citations,
        expected_no_evidence=flip,
    )
    value_accuracy = compute_value_accuracy(
        entry.expected_signal_citations,
        run.obd_signal_citations,
        expected_no_evidence=flip,
    )
    dtc_accuracy = compute_dtc_accuracy(
        entry.expected_dtcs,
        run.obd_dtc_citations,
        expected_no_evidence=flip,
    )

    return OBDDeterministicMetrics(
        signal_recall=signal_recall,
        signal_precision=signal_precision,
        value_accuracy=value_accuracy,
        dtc_accuracy=dtc_accuracy,
    )

"""Deterministic metric computation for the comparative eval suite.

The judge model is responsible only for the subjective
``answer_quality`` rating; everything else in the rubric is
computed here from the ``GoldenEntry`` and ``SystemRunResult``
without LLM involvement.  This split keeps the bulk of the
score reproducible across runs and avoids paying for judge
calls on metrics that don't need them.

Inputs:
- ``GoldenEntry``  — the authoritative reference for one
  question.
- ``SystemRunResult`` — what one system (agent or RAG) produced
  for that question.

Output:
- ``DeterministicMetrics`` — a typed dict of rubric dimensions
  that the judge later combines with its own
  ``answer_quality`` rating into a final ``Grade``.

Whitespace normalisation matches the conventions established in
``scripts.generate_golden_candidates`` (collapse CJK gaps so a
line-wrapped Chinese phrase still substring-matches its
unwrapped counterpart).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from tests.harness.evals.schemas import GoldenEntry, SystemRunResult


# ── Whitespace normalisation (mirrors generator script) ──────────


_CJK_CLASS = r"[一-鿿　-〿＀-￯]"
"""CJK ideographs + symbols/punctuation + fullwidth forms.
A run of whitespace between two CJK characters almost always
came from a line break in the source PDF; collapse it."""


_CJK_WS_CJK_RE = re.compile(rf"({_CJK_CLASS})\s+(?={_CJK_CLASS})")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    """Collapse CJK gaps then any remaining whitespace runs.

    See ``scripts.generate_golden_candidates._normalize_ws`` —
    same logic, lifted here so the eval doesn't depend on a
    script under ``scripts/``.

    Args:
        text: Raw string.

    Returns:
        Normalised copy suitable for substring comparison.
    """
    prev: Optional[str] = None
    while prev != text:
        prev = text
        text = _CJK_WS_CJK_RE.sub(r"\1", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


# ── Output container ─────────────────────────────────────────────


@dataclass(frozen=True)
class DeterministicMetrics:
    """Rubric dimensions that don't need an LLM judge.

    Computed by ``compute_deterministic_metrics``; combined with
    ``answer_quality`` from the judge to form the final ``Grade``.

    Attributes:
        section_recall: Fraction of golden slugs the system
            surfaced anywhere (claim ∪ read).
        claim_precision: Fraction of CITED slugs that match
            golden.  Replaces the older ``section_precision``
            which conflated reads and citations.
        exploration_cost: Fraction of READ slugs that were
            NOT cited.  Higher = more navigation waste.  Always
            0.0 for RAG (no synthesis step).
        fact_recall: Fraction of must_contain present in output.
        fact_density: Fact hits × conciseness factor.
        hallucination_penalty: ``1 - min(1, count * 0.5)``.
        citation_quality: Tiered (0.0 / 0.3 / 1.0), against
            claim_slugs.
        trajectory_efficiency: Agent-only; 1.0 for RAG.
        fact_recall_hits: Concrete list of must_contain that hit
            (for reasoning / debugging).
        fact_recall_misses: Concrete list of must_contain that
            didn't hit.
        hallucination_hits: Concrete list of must_not_contain
            terms that did appear (= hallucinations).
    """

    section_recall: float
    claim_precision: float
    exploration_cost: float
    fact_recall: float
    fact_density: float
    hallucination_penalty: float
    citation_quality: float
    trajectory_efficiency: float
    # Diagnostics — not metrics themselves, but useful for
    # report-generation and judge prompting.
    fact_recall_hits: List[str]
    fact_recall_misses: List[str]
    hallucination_hits: List[str]


# ── Per-metric helpers ───────────────────────────────────────────


def _compute_section_recall(
    expected: List[str], surfaced: List[str],
) -> float:
    """``|surfaced ∩ expected| / |expected|``, 1 when expected empty.

    ``surfaced`` should be the union of ``claim_slugs`` and
    ``read_slugs`` — recall asks "did the system make this
    section available at all," regardless of whether it was
    explicitly cited or merely read during navigation.

    Empty ``expected`` (typical for adversarial entries where
    no slug is the right answer) returns 1.0 — the system did
    not need to retrieve anything specific to be correct.
    """
    if not expected:
        return 1.0
    expected_set = {s for s in expected if s}
    surfaced_set = {s for s in surfaced if s}
    if not expected_set:
        return 1.0
    overlap = expected_set & surfaced_set
    return len(overlap) / len(expected_set)


def _compute_claim_precision(
    expected: List[str], claim: List[str],
) -> float:
    """``|claim ∩ expected| / |claim|``, 1 when claim empty.

    Replaces the older ``_compute_section_precision`` which
    operated over the union of claim + read.  This version
    only counts what the system **explicitly cited as an
    answer source** — for the agent, slugs from
    ``result.citations[].slug``; for RAG, slugs from each
    retrieved chunk (RAG has no separate synthesis step).

    Empty ``claim`` returns 1.0 (vacuously precise).  But a
    system that claimed nothing also scores 0 on
    ``section_recall``, so it can't ride this freebie to a
    high overall score.

    Adversarial entries (empty ``expected``) are a special
    case: if the system claimed anything, it's all "wrong"
    — precision = 0.  If the system correctly stayed silent,
    precision = 1.
    """
    if not claim:
        return 1.0
    if not expected:
        # Adversarial: anything claimed is incorrect.
        return 0.0
    expected_set = {s for s in expected if s}
    claim_set = {s for s in claim if s}
    if not claim_set:
        return 1.0
    overlap = expected_set & claim_set
    return len(overlap) / len(claim_set)


def _compute_exploration_cost(
    read: List[str], claim: List[str],
) -> float:
    """``1 - |claim ∩ read| / max(|read|, 1)``.  LOWER is better.

    Captures "how much navigation overhead did the agent pay?"
    Of the slugs the agent actually accessed via
    ``read_manual_section``, what fraction did NOT make it
    into the final claim?  A perfectly efficient agent reads
    only what it cites (cost = 0.0); an agent that reads many
    sections and cites few pays a higher exploration cost.

    For RAG: ``read_slugs == claim_slugs`` (no synthesis),
    so cost is always 0.0.  This is intentional — RAG doesn't
    pay an exploration cost because there's no separate
    "navigation vs grounding" distinction in its workflow.

    Args:
        read: Slugs the system accessed.
        claim: Slugs the system explicitly cited.

    Returns:
        Cost in [0.0, 1.0].
    """
    read_set = {s for s in read if s}
    if not read_set:
        return 0.0  # No reads → no waste.
    claim_set = {s for s in claim if s}
    cited_count = len(read_set & claim_set)
    return 1.0 - (cited_count / len(read_set))


def _compute_fact_recall(
    must_contain: List[str], output_text: str,
) -> tuple[float, List[str], List[str]]:
    """Substring scan of ``must_contain`` over ``output_text``.

    Both sides are whitespace-normalised, then a case-
    insensitive substring match.  Empty ``must_contain``
    returns ``1.0`` (no facts to recall, vacuously satisfied).

    Returns:
        ``(score, hits, misses)`` — score in [0, 1] plus the
        concrete strings that did and did not match.
    """
    if not must_contain:
        return 1.0, [], []
    norm_text = _normalize_ws(output_text or "").lower()
    hits: List[str] = []
    misses: List[str] = []
    for term in must_contain:
        norm_term = _normalize_ws(term or "").lower()
        if norm_term and norm_term in norm_text:
            hits.append(term)
        else:
            misses.append(term)
    score = len(hits) / len(must_contain)
    return score, hits, misses


def _compute_fact_density(
    fact_hits: List[str],
    must_contain: List[str],
    output_text: str,
) -> float:
    """Recall × conciseness factor.

    Rewards an answer that hits all the facts AND does so
    concisely.  Two factors combined:

    - ``recall = hits / max(must_contain, 1)`` — fraction of
      facts the output covers.
    - ``conciseness = min(1, 100 / max(words, 1))`` — caps at
      1.0 below 100 output words; decays linearly above.

    ``density = recall * conciseness``.

    Behaviour:

    - 50 words, 5/5 hits → 1.0 × 1.0 = 1.0  (concise + complete)
    - 500 words, 5/5 hits → 1.0 × 0.2 = 0.2  (correct but verbose)
    - 50 words, 1/5 hits → 0.2 × 1.0 = 0.2  (concise but missing facts)
    - 500 words, 1/5 hits → 0.2 × 0.2 = 0.04  (verbose AND missing)

    The earlier formula ``hits / max(words / 100, 1)`` over-
    rewarded short outputs that had even one fact — RAG's
    fragmentary chunks could score 1.0 density on 1/5 facts.

    Empty output or empty must_contain returns 0.0.
    """
    if not output_text or not must_contain:
        return 0.0
    words = len(output_text.split())
    if words == 0:
        return 0.0
    recall = len(fact_hits) / len(must_contain)
    conciseness = min(1.0, 100.0 / max(words, 1))
    return recall * conciseness


def _compute_hallucination_penalty(
    must_not_contain: List[str], output_text: str,
) -> tuple[float, List[str]]:
    """Continuous penalty: ``1 - min(1, count * 0.5)``.

    First hallucination costs 0.5; second brings the penalty
    to 1.0 (score 0); further ones don't matter.  Captures
    "one bad fact poisons the answer, two is irrecoverable."

    Returns:
        ``(score, hits)`` — score in [0, 1], higher = better;
        ``hits`` is the list of must_not_contain terms that
        appeared in the output.
    """
    if not must_not_contain:
        return 1.0, []
    norm_text = _normalize_ws(output_text or "").lower()
    hits = [
        term for term in must_not_contain
        if _normalize_ws(term or "").lower() in norm_text
        and term  # ignore empty guard strings
    ]
    penalty_factor = min(1.0, len(hits) * 0.5)
    return 1.0 - penalty_factor, hits


def _compute_citation_quality(
    expected: List[str], claim: List[str],
) -> float:
    """Tiered citation quality, computed against ``claim_slugs``.

    Citation quality reflects the system's CLAIM about which
    sections are answers, not its navigation history — so
    this is checked against ``claim_slugs`` only, not the
    union of claim + read.

    - 0.0 — system claimed no slugs (empty citations).
    - 0.3 — system claimed slugs but none match the golden
      (cited but wrong).
    - 1.0 — at least one claimed slug matches a golden slug.

    Adversarial entries (empty ``expected``) are graded
    inversely: 1.0 if ``claim`` is empty (correctly silent),
    0.3 if ``claim`` is non-empty (cited a wrong section
    when the question had no answer).
    """
    if not expected:
        # Adversarial — silence is the correct citation.
        return 1.0 if not claim else 0.3
    if not claim:
        return 0.0
    expected_set = {s for s in expected if s}
    claim_set = {s for s in claim if s}
    if expected_set & claim_set:
        return 1.0
    return 0.3


def _compute_trajectory_efficiency(
    expected_calls: int, actual_calls: int,
) -> float:
    """``min(1, expected / max(actual, expected))``.

    Linear decay above expected count — 1.0 at-or-below expected,
    0.5 at 2× expected, 0.33 at 3× expected.  RAG always scores
    1.0 (single retrieval call).

    A floor of 0.0 protects against div-by-zero when
    ``expected_calls`` is 0 (uncalibrated entries) — in that
    case we return 1.0 to avoid penalising entries we haven't
    calibrated yet.
    """
    if expected_calls <= 0:
        return 1.0
    if actual_calls <= 0:
        return 1.0  # System didn't run; not the trajectory's fault.
    denominator = max(actual_calls, expected_calls)
    return min(1.0, expected_calls / denominator)


# ── Public entry point ───────────────────────────────────────────


def compute_deterministic_metrics(
    entry: GoldenEntry, run: SystemRunResult,
) -> DeterministicMetrics:
    """Compute all non-LLM-judge rubric dimensions.

    Args:
        entry: Golden reference.
        run: One system's output for the same question.

    Returns:
        ``DeterministicMetrics`` with all dimensions populated.
        The judge later adds ``answer_quality`` to form the
        final ``Grade``.
    """
    # Surfaced = claim ∪ read.  section_recall asks "did the
    # system make this section available anywhere," which
    # includes both the cited and the merely-read.
    surfaced = list({*run.claim_slugs, *run.read_slugs})

    section_recall = _compute_section_recall(
        entry.expected_recall_slugs, surfaced,
    )
    claim_precision = _compute_claim_precision(
        entry.expected_recall_slugs, run.claim_slugs,
    )
    exploration_cost = _compute_exploration_cost(
        run.read_slugs, run.claim_slugs,
    )

    fact_recall, fact_hits, fact_misses = _compute_fact_recall(
        entry.must_contain, run.output_text,
    )
    fact_density = _compute_fact_density(
        fact_hits, entry.must_contain, run.output_text,
    )

    hallucination_penalty, hallucination_hits = (
        _compute_hallucination_penalty(
            entry.must_not_contain, run.output_text,
        )
    )

    citation_quality = _compute_citation_quality(
        entry.expected_recall_slugs, run.claim_slugs,
    )

    # Trajectory only meaningful for the agent.  RAG scores 1.0
    # (single embedding + single pgvector query — no choices to
    # make).
    if run.system_label == "manual_agent":
        trajectory_efficiency = _compute_trajectory_efficiency(
            len(entry.expected_tool_trace),
            len(run.tool_trace),
        )
    else:
        trajectory_efficiency = 1.0

    return DeterministicMetrics(
        section_recall=section_recall,
        claim_precision=claim_precision,
        exploration_cost=exploration_cost,
        fact_recall=fact_recall,
        fact_density=fact_density,
        hallucination_penalty=hallucination_penalty,
        citation_quality=citation_quality,
        trajectory_efficiency=trajectory_efficiency,
        fact_recall_hits=fact_hits,
        fact_recall_misses=fact_misses,
        hallucination_hits=hallucination_hits,
    )


# ── Overall-score combiner ───────────────────────────────────────


# Weights for the comparative-eval rubric.  Exposed as a
# constant (not hard-coded into a single formula) so we can
# tune without rewriting callers.  Sums to 1.0.
#
# Rebalanced 2026-05-03 alongside the
# section_precision → claim_precision + exploration_cost
# split:
# - claim_precision keeps the previous section_precision
#   weight (0.15) — it's the like-for-like replacement.
# - (1 - exploration_cost) gets a small weight (0.05) — keeps
#   navigation honest without dominating.
# - The +0.05 budget for exploration_cost comes from
#   fact_density (0.10 → 0.05), which we flagged as
#   misleadingly low when output_text includes raw_sections.
#   De-weighting the broken metric is a fair trade until we
#   redesign it.
DEFAULT_OVERALL_WEIGHTS: dict = {
    "section_recall":         0.25,
    "claim_precision":        0.15,
    "exploration_cost":       0.05,  # applied as (1 - cost)
    "fact_recall":            0.20,
    "fact_density":           0.05,
    "hallucination_penalty":  0.15,
    "citation_quality":       0.05,
    "answer_quality":         0.10,
}


def compute_overall(
    metrics: DeterministicMetrics,
    answer_quality: float,
    weights: Optional[dict] = None,
) -> float:
    """Combine deterministic metrics + judge's answer_quality.

    Note: ``exploration_cost`` is a "lower is better" metric;
    it enters the formula as ``(1 - cost)`` so all terms
    contribute positively toward the overall score.

    Args:
        metrics: Output of ``compute_deterministic_metrics``.
        answer_quality: LLM-judge rating in [0, 1].
        weights: Optional override (defaults to
            ``DEFAULT_OVERALL_WEIGHTS``).  Must contain all
            eight rubric keys; sum is not enforced (caller can
            experiment with different weighting schemes).

    Returns:
        Weighted score in [0, 1].
    """
    w = weights if weights is not None else DEFAULT_OVERALL_WEIGHTS
    return (
        w["section_recall"]        * metrics.section_recall
        + w["claim_precision"]     * metrics.claim_precision
        + w["exploration_cost"]    * (1.0 - metrics.exploration_cost)
        + w["fact_recall"]         * metrics.fact_recall
        + w["fact_density"]        * metrics.fact_density
        + w["hallucination_penalty"] * metrics.hallucination_penalty
        + w["citation_quality"]    * metrics.citation_quality
        + w["answer_quality"]      * answer_quality
    )

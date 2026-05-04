"""Prompt templates for the answer-quality judge.

In the comparative-eval (HARNESS-15 / Issue #74) redesign, the
judge is no longer responsible for substring-based metrics —
those are computed deterministically in
``tests.harness.evals.metrics`` from the ``GoldenEntry`` and
``SystemRunResult``.  The judge's only job is to produce the
``answer_quality`` rating: a holistic 0.0–1.0 score for "does
this output correctly, completely, and clearly answer the
question?"

Why split the work:

- Deterministic metrics (section_recall, fact_recall, etc.)
  produce identical scores across runs.  Reproducibility is a
  hard requirement for benchmark-grade reporting.
- The remaining subjective dimension (does the answer make
  sense, is it well-organised, does it skip key steps) needs
  judgment that substring matching can't capture.
- This split also makes the judge call cheaper: the prompt is
  much shorter without the 5-dimension rubric, and the response
  is a single float plus 2–3 sentences of reasoning.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import List

from tests.harness.evals.schemas import (
    GoldenEntry,
    RetrievedChunkMetadata,
    SystemRunResult,
)


# ── Constants ─────────────────────────────────────────────────────


_MAX_OUTPUT_CHARS = 4000
"""Cap on system output text passed to the judge.  RAG outputs
in particular can be very long (top-5 chunks concatenated =
~5–15 KB of text).  4 KB is enough for the judge to assess
quality without overwhelming the prompt."""


_MAX_GOLDEN_CHARS = 1500
"""Cap on the golden_summary length included in the prompt.
Golden summaries are 3–8 sentences (under 1 KB typically), so
this is a soft ceiling."""


# ── System prompt ────────────────────────────────────────────────


JUDGE_SYSTEM_PROMPT = """\
You are grading a vehicle-service-manual answer.

You will see:
  1. The QUESTION a technician asked.
  2. The GOLDEN ANSWER (human-written reference).
  3. The SYSTEM OUTPUT (what one of two systems produced — you
     are told which one, but score on substance, not source).

Your only job is to rate the SYSTEM OUTPUT on **answer quality**:
how correctly, completely, and clearly does it answer the question
compared to the golden?

Return ONLY a JSON object with this exact shape:

{
  "answer_quality": <float in [0.0, 1.0]>,
  "reasoning": "<2–4 sentences citing specific evidence>"
}

Do not wrap in markdown fences.  Do not output prose before or
after the JSON.

## Rating scale for answer_quality

- 1.0 — Output answers the question correctly, completely, and
  clearly.  A technician could act on it without consulting
  another source.  Matches the golden's substance even if
  phrased differently.
- 0.7–0.9 — Mostly correct, with minor omissions or
  unclear phrasing.  Technician would still arrive at the right
  conclusion but might need to re-read.
- 0.4–0.6 — Partially correct.  Key facts are present but the
  output is missing critical steps, contains misleading
  emphasis, or buries the answer in irrelevant content.
  Technician might still get there but would risk error.
- 0.1–0.3 — Wrong direction.  Output cites the wrong system,
  the wrong DTC, or describes a different procedure.  Technician
  acting on this would make things worse.
- 0.0 — No usable content.  Empty output, refusal when the
  manual could have answered, or completely fabricated content.

## Special cases

- For ADVERSARIAL questions (where the golden indicates the
  manual cannot answer), a correct refusal scores 1.0.  A
  fabricated answer scores ≤ 0.2 regardless of how confident
  it sounds.
- For RAG outputs (no synthesised summary, just retrieved
  chunks): grade as if a technician were reading the chunks
  directly.  If the right chunk is in the retrieval set, the
  technician can find the answer — score accordingly.  If
  the retrieval set has the right chunks but a casual reader
  would miss the answer in the noise, score lower.
- Phrasing differences from the golden are NOT a penalty.
  English vs Chinese, terse vs verbose, narrative vs
  bullet-list — all fine if the substance matches.

## Substance focus

Reasoning should cite concrete evidence: which fact is missing,
which step is wrong, which DTC was confused with which.  Do
not reason about format, length, or style — those don't enter
this score.
"""


# ── User-prompt builder ──────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    """Truncate with a trailing marker if clipped."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n[truncated — {len(text)} chars total]"
    )


def _format_chunk_summary(
    chunks: List[RetrievedChunkMetadata],
) -> str:
    """Compact chunk-level breakdown for the RAG prompt.

    The judge sees ``output_text`` (concatenated chunks) for
    must_contain scanning, but a per-chunk metadata list helps
    it understand what was retrieved (scores, slugs, DTC tags).
    """
    if not chunks:
        return "  (no chunks retrieved)"
    lines = []
    for i, c in enumerate(chunks, 1):
        flags = []
        if c.has_image:
            flags.append("image")
        if c.dtc_codes:
            flags.append(f"dtc={','.join(c.dtc_codes[:3])}")
        flag_str = f"  [{' / '.join(flags)}]" if flags else ""
        lines.append(
            f"  [{i}] score={c.score:.3f}  slug={c.slug}{flag_str}"
        )
    return "\n".join(lines)


def build_user_prompt(
    entry: GoldenEntry, run: SystemRunResult,
) -> str:
    """Assemble the user message given the golden + system output.

    The prompt deliberately omits ``must_contain`` /
    ``must_not_contain`` / ``expected_recall_slugs`` — those
    are graded deterministically and don't need the judge's
    attention.  The judge focuses purely on answer quality
    against the golden_summary.

    Args:
        entry: Golden reference.
        run: System output (agent or RAG, both unified into
            ``SystemRunResult``).

    Returns:
        Fully-rendered user prompt.
    """
    golden_summary = _truncate(entry.golden_summary, _MAX_GOLDEN_CHARS)
    output_text = _truncate(run.output_text, _MAX_OUTPUT_CHARS)
    chunk_block = (
        _format_chunk_summary(run.retrieved_chunk_metadata)
        if run.system_label == "rag" else "  (n/a — agent output)"
    )

    claim_block = (
        ', '.join(run.claim_slugs) if run.claim_slugs else '(none)'
    )
    read_block = (
        ', '.join(run.read_slugs) if run.read_slugs else '(none)'
    )
    return f"""\
## QUESTION
{entry.question}

## GOLDEN ANSWER
{golden_summary}

## SYSTEM UNDER TEST: {run.system_label}

### Cited slugs (the system's claim about which sections are answers)
{claim_block}

### Read slugs (sections the system actually accessed; may include
### navigation/index pages even when not cited)
{read_block}

### Retrieved chunks (RAG only)
{chunk_block}

### Output text
{output_text}

---

Return only the JSON object with `answer_quality` (float 0.0–1.0)
and `reasoning` (2–4 sentences).  No prose, no code fences.
"""

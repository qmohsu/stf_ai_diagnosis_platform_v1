"""Prompt templates for the LLM-as-judge (``z-ai/glm-5.1``).

The judge scores a ``ManualAgentResult`` against a ``GoldenEntry``
using a 5-dimension rubric and a weighted ``overall`` score.  The
system prompt pins the rubric; ``build_user_prompt`` serialises the
golden and agent data into a compact, stable form.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import List

from tests.harness.evals.schemas import (
    GoldenEntry,
    ManualAgentResult,
    SectionRef,
    ToolCallTrace,
)


# Per-section raw-text cap.  Large manual sections can be tens of
# thousands of characters; the judge only needs enough prose to
# verify must_contain / must_not_contain presence.  Keep input
# tokens bounded.
_MAX_SECTION_CHARS = 3000


JUDGE_SYSTEM_PROMPT = """\
You are grading a vehicle-service-manual search agent.

Your job: compare the AGENT OUTPUT against the GOLDEN reference and
return a JSON object exactly matching the rubric below.  You MUST
return JSON only — no prose before or after.

## Rubric (keys and allowed values)

- section_match (integer 0 or 1): 1 if the agent cited at least one
  of the manual_id#slug pairs listed in the golden citations, else 0.
- fact_recall (float 0.0 to 1.0): fraction of the golden's
  `must_contain` items whose text appears (case-insensitive,
  substring) in either the agent's summary or the concatenated
  raw_sections text.  Example: 3 items, 2 present -> 0.67.
- hallucination (integer 0 or 1): 1 if ANY of the golden's
  `must_not_contain` strings appears (case-insensitive, substring)
  in the agent's summary OR raw_sections, else 0.
- citation_present (integer 0 or 1): 1 if the agent's citations
  list is non-empty, else 0.
- trajectory_ok (integer 0 or 1): 1 if the agent made at most
  ~1.5x the number of tool calls in `expected_tool_trace` AND did
  not brute-force read every section of the manual; else 0.
  This is diagnostic only — the `overall` score does not use it.
- overall (float 0.0 to 1.0): weighted score, computed as
  0.4*section_match + 0.3*fact_recall
  + 0.2*(1 - hallucination) + 0.1*citation_present.
- reasoning (string): 2-3 sentences citing specific evidence
  (e.g., "Agent cited 3-2-fuel-system-troubleshooting, which
  matches the golden; must_contain 'P0171' appears in the
  summary; one of four expected tools was skipped but the
  answer is complete.").  Max 400 characters.

## Special cases

- Adversarial entries (category "adversarial") may have empty
  golden_citations.  For these, `section_match` should be 1 if
  the agent correctly declined to guess (its summary includes
  a phrase like "not found", "unknown code", or similar) and
  its citations list is empty or cites nothing fabricated.
- If the agent output is clearly malformed or truncated, score
  generously on recall where evidence is present, but set
  hallucination=1 if fabricated facts appear.

Return only the JSON object.  Do not wrap in markdown fences.
"""


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text with a trailing marker if clipped."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n[truncated — {len(text)} chars total]"
    )


def _fmt_citations(
    citations: List,  # noqa: ANN001 — List[GoldenCitation | Citation]
) -> str:
    """Format citation list as a bullet block."""
    if not citations:
        return "  (none)"
    lines = []
    for cit in citations:
        quote = getattr(cit, "quote", "") or ""
        suffix = f" — \"{quote}\"" if quote else ""
        lines.append(
            f"  - {cit.manual_id}#{cit.slug}{suffix}",
        )
    return "\n".join(lines)


def _fmt_section(section: SectionRef) -> str:
    """Format one ``SectionRef`` with truncated text."""
    img_flag = "yes" if section.had_images else "no"
    text = _truncate(section.text, _MAX_SECTION_CHARS)
    return (
        f"[{section.manual_id}#{section.slug}] "
        f"(images: {img_flag})\n{text}"
    )


def _fmt_tool_trace(trace: List[ToolCallTrace]) -> str:
    """Format tool trace as a compact name+count summary."""
    if not trace:
        return "  (no tool calls recorded)"
    counts: dict[str, int] = {}
    for call in trace:
        counts[call.name] = counts.get(call.name, 0) + 1
    order = " -> ".join(call.name for call in trace)
    summary = ", ".join(
        f"{name}:{n}" for name, n in sorted(counts.items())
    )
    return (
        f"  order: {order}\n"
        f"  counts: {summary}"
    )


def _fmt_list(items: List[str]) -> str:
    """Format a list of strings as a bullet block."""
    if not items:
        return "  (none)"
    return "\n".join(f"  - {item}" for item in items)


def build_user_prompt(
    entry: GoldenEntry,
    result: ManualAgentResult,
) -> str:
    """Build the judge's user prompt from a (golden, result) pair.

    Args:
        entry: The golden reference entry.
        result: The manual agent's output for the same question.

    Returns:
        A fully-rendered user prompt string.
    """
    golden_citations_block = _fmt_citations(
        entry.golden_citations,
    )
    must_contain_block = _fmt_list(entry.must_contain)
    must_not_contain_block = _fmt_list(entry.must_not_contain)
    expected_tools_block = _fmt_list(entry.expected_tool_trace)
    agent_citations_block = _fmt_citations(result.citations)

    if result.raw_sections:
        raw_sections_block = "\n\n".join(
            _fmt_section(sec) for sec in result.raw_sections
        )
    else:
        raw_sections_block = "(no sections recorded)"

    tool_trace_block = _fmt_tool_trace(result.tool_trace)

    obd_context = entry.obd_context or "(none)"

    return f"""\
## QUESTION
{entry.question}

## OBD CONTEXT
{obd_context}

## GOLDEN (authoritative)
Category: {entry.category}
Difficulty: {entry.difficulty}
Summary:
{entry.golden_summary}

Citations:
{golden_citations_block}

Must contain:
{must_contain_block}

Must not contain:
{must_not_contain_block}

Expected tools (loose guide):
{expected_tools_block}

Requires image: {entry.requires_image}

## AGENT OUTPUT
Summary:
{result.summary}

Citations:
{agent_citations_block}

Tool trace:
{tool_trace_block}

Iterations: {result.iterations}
Stopped reason: {result.stopped_reason}

Raw sections returned (text truncated to {_MAX_SECTION_CHARS} chars each):
{raw_sections_block}

## INSTRUCTIONS
Return the JSON object matching the rubric.  JSON only.
"""

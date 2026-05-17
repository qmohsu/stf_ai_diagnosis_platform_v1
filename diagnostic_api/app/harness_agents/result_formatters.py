"""Markdown serializers for sub-agent results.

Turns ``OBDAgentResult`` / ``ManualAgentResult`` Pydantic objects
into structured text the main agent can quote.  These formatters
are used by the delegation tool wrappers
(``delegate_to_obd_agent``, ``delegate_to_manual_agent``) to
produce a tool result the main agent sees.

Keeping the formatters next to the agent types (rather than in
the tool module) makes the sub-agent contract self-contained:
``run_*_agent`` produces the result, ``format_*_agent_result``
renders it, and the delegation tool just composes them.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import List

from app.harness_agents.types import (
    Citation,
    DataExcerpt,
    DTCCitation,
    ManualAgentResult,
    OBDAgentResult,
    SectionRef,
    SignalCitation,
)


# ── Shared rendering helpers ─────────────────────────────────────


def _stopped_reason_label(reason: str) -> str:
    """Friendly label for a ``StoppedReason`` value."""
    return {
        "complete": "completed",
        "timeout": "TIMED OUT",
        "max_iterations": "ITERATION CAP REACHED",
        "error": "ERROR",
    }.get(reason, reason)


def _truncate_for_quote(text: str, max_chars: int = 1200) -> str:
    """Cap a text block at ``max_chars`` with a marker."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated — {len(text)} chars total]"


# ── OBD agent result formatter ───────────────────────────────────


def _format_signal_citation(c: SignalCitation) -> str:
    """Render one ``SignalCitation`` as a single bullet line."""
    parts: List[str] = [c.signal]
    if c.time_range:
        parts.append(f"{c.time_range[0]} → {c.time_range[1]}")
    if c.stat:
        parts.append(c.stat)
    if c.value is not None:
        if c.units:
            parts.append(f"{c.value:g} {c.units}")
        else:
            parts.append(f"{c.value:g}")
    elif c.units:
        parts.append(c.units)
    return "- " + ", ".join(parts)


def _format_dtc_citation(c: DTCCitation) -> str:
    """Render one ``DTCCitation`` as a single bullet line."""
    ecu = c.ecu or "ECU unspecified"
    return f"- {c.status.upper():8s} {ecu}  {c.code}"


def _format_data_excerpt(e: DataExcerpt) -> str:
    """Render one ``DataExcerpt`` as a fenced text block."""
    text = e.payload.get("text") if isinstance(
        e.payload, dict,
    ) else None
    if not isinstance(text, str):
        # Fall back to a compact str() representation of the
        # payload dict — defensive against future shapes.
        text = str(e.payload)
    body = _truncate_for_quote(text)
    return f"#### {e.kind}\n```\n{body}\n```"


def format_obd_agent_result(result: OBDAgentResult) -> str:
    """Render an ``OBDAgentResult`` as structured markdown.

    Sections:
    1. Header — iteration/tool-call counts + stopped_reason
    2. Summary — the agent's natural-language answer
    3. Signal citations
    4. DTC citations
    5. Data excerpts (verbatim tool outputs)
    6. Limitations

    Sections with no content are omitted.

    Args:
        result: The sub-agent's structured output.

    Returns:
        Markdown text suitable for embedding in a tool result.
    """
    lines: List[str] = []
    lines.append(
        f"## OBD sub-agent finding "
        f"({result.iterations} iterations, "
        f"{len(result.tool_trace)} tool calls, "
        f"{_stopped_reason_label(result.stopped_reason)})"
    )
    lines.append("")
    lines.append("### Summary")
    lines.append(result.summary.strip() or "(empty)")
    lines.append("")

    if result.signal_citations:
        lines.append("### Signal citations")
        for c in result.signal_citations:
            lines.append(_format_signal_citation(c))
        lines.append("")

    if result.dtc_citations:
        lines.append("### DTC citations")
        for c in result.dtc_citations:
            lines.append(_format_dtc_citation(c))
        lines.append("")

    if result.raw_data:
        lines.append("### Data excerpts")
        for e in result.raw_data:
            lines.append(_format_data_excerpt(e))
            lines.append("")

    if result.limitations:
        lines.append("### Limitations")
        for lim in result.limitations:
            lines.append(f"- {lim}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── Manual agent result formatter ────────────────────────────────


def _format_manual_citation(c: Citation) -> str:
    """Render one manual ``Citation`` as a single bullet line."""
    quote = c.quote.strip()
    if quote:
        # Cap quote display for readability.
        if len(quote) > 200:
            quote = quote[:200] + "..."
        return f"- `{c.manual_id}#{c.slug}` — \"{quote}\""
    return f"- `{c.manual_id}#{c.slug}`"


def _format_section_ref(s: SectionRef) -> str:
    """Render one ``SectionRef`` as a fenced text block."""
    body = _truncate_for_quote(s.text)
    img_note = (
        " (contains image content)"
        if s.had_images else ""
    )
    return (
        f"#### `{s.manual_id}#{s.slug}`{img_note}\n"
        f"```\n{body}\n```"
    )


def format_manual_agent_result(result: ManualAgentResult) -> str:
    """Render a ``ManualAgentResult`` as structured markdown.

    Sections:
    1. Header — iteration/tool-call counts + stopped_reason
    2. Summary — the agent's natural-language answer
    3. Citations — sections the agent explicitly cited
    4. Raw sections — full section text the agent pulled

    Args:
        result: The sub-agent's structured output.

    Returns:
        Markdown text suitable for embedding in a tool result.
    """
    lines: List[str] = []
    lines.append(
        f"## Manual sub-agent finding "
        f"({result.iterations} iterations, "
        f"{len(result.tool_trace)} tool calls, "
        f"{_stopped_reason_label(result.stopped_reason)})"
    )
    lines.append("")
    lines.append("### Summary")
    lines.append(result.summary.strip() or "(empty)")
    lines.append("")

    if result.citations:
        lines.append("### Citations")
        for c in result.citations:
            lines.append(_format_manual_citation(c))
        lines.append("")

    if result.raw_sections:
        lines.append("### Raw sections")
        for s in result.raw_sections:
            lines.append(_format_section_ref(s))
            lines.append("")

    return "\n".join(lines).rstrip()

"""Render sub-agent results as markdown for the main agent (copied from V2).

Author: Xiangzhu Yan
"""

from __future__ import annotations

from typing import List

from stf_v3.diagnosis.agent.types import (
    Citation,
    DataExcerpt,
    DTCCitation,
    ManualAgentResult,
    OBDAgentResult,
    SectionRef,
    SignalCitation,
)


def _stopped_reason_label(reason: str) -> str:
    return {
        "complete": "completed",
        "timeout": "TIMED OUT",
        "max_iterations": "ITERATION CAP REACHED",
        "budget": "BUDGET EXHAUSTED",
        "error": "ERROR",
    }.get(reason, reason)


def _truncate_for_quote(text: str, max_chars: int = 1200) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated — {len(text)} chars total]"


def _format_signal_citation(c: SignalCitation) -> str:
    parts: List[str] = [c.signal]
    if c.time_range:
        parts.append(f"{c.time_range[0]} → {c.time_range[1]}")
    if c.stat:
        parts.append(c.stat)
    if c.value is not None:
        parts.append(f"{c.value:g} {c.units}" if c.units else f"{c.value:g}")
    elif c.units:
        parts.append(c.units)
    return "- " + ", ".join(parts)


def _format_dtc_citation(c: DTCCitation) -> str:
    return f"- {c.status.upper():8s} {c.ecu or 'ECU unspecified'}  {c.code}"


def _format_data_excerpt(e: DataExcerpt) -> str:
    text = e.payload.get("text") if isinstance(e.payload, dict) else None
    if not isinstance(text, str):
        text = str(e.payload)
    return f"#### {e.kind}\n```\n{_truncate_for_quote(text)}\n```"


def format_obd_agent_result(result: OBDAgentResult) -> str:
    """Markdown rendering of an OBD sub-agent finding."""
    lines: List[str] = [
        f"## OBD sub-agent finding ({result.iterations} iterations, {len(result.tool_trace)} tool calls, "
        f"{_stopped_reason_label(result.stopped_reason)})", "", "### Summary",
        result.summary.strip() or "(empty)", "",
    ]
    if result.signal_citations:
        lines += ["### Signal citations"] + [_format_signal_citation(c) for c in result.signal_citations] + [""]
    if result.dtc_citations:
        lines += ["### DTC citations"] + [_format_dtc_citation(c) for c in result.dtc_citations] + [""]
    if result.raw_data:
        lines.append("### Data excerpts")
        for e in result.raw_data:
            lines += [_format_data_excerpt(e), ""]
    if result.limitations:
        lines += ["### Limitations"] + [f"- {lim}" for lim in result.limitations] + [""]
    return "\n".join(lines).rstrip()


def _format_manual_citation(c: Citation) -> str:
    quote = c.quote.strip()
    if quote:
        if len(quote) > 200:
            quote = quote[:200] + "..."
        return f"- `{c.manual_id}#{c.slug}` — \"{quote}\""
    return f"- `{c.manual_id}#{c.slug}`"


def _format_section_ref(s: SectionRef) -> str:
    img_note = " (contains image content)" if s.had_images else ""
    return f"#### `{s.manual_id}#{s.slug}`{img_note}\n```\n{_truncate_for_quote(s.text)}\n```"


def format_manual_agent_result(result: ManualAgentResult) -> str:
    """Markdown rendering of a manual sub-agent finding."""
    lines: List[str] = [
        f"## Manual sub-agent finding ({result.iterations} iterations, {len(result.tool_trace)} tool calls, "
        f"{_stopped_reason_label(result.stopped_reason)})", "", "### Summary",
        result.summary.strip() or "(empty)", "",
    ]
    if result.citations:
        lines += ["### Citations"] + [_format_manual_citation(c) for c in result.citations] + [""]
    if result.raw_sections:
        lines.append("### Raw sections")
        for s in result.raw_sections:
            lines += [_format_section_ref(s), ""]
    return "\n".join(lines).rstrip()

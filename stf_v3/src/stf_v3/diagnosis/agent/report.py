"""Diagnosis report object + deterministic citation extraction.

The model ends with plain text (five-section markdown); the runtime
packs it with the citations it can PROVE from the tool trace:

* every manual section actually read (main agent or manual sub-agent)
  → ``manual`` citation, ``source="trace"``;
* every DTC the DTC tools surfaced → ``dtc`` citation, ``source="trace"``;
* ``<manual_id>#<slug>`` references and DTC codes that appear in the
  text but never in the trace → ``source="NO_SOURCE"`` (FM-35; project
  citation rule).

PROD-09 adds two residue checks before the text is packed: complete
``<think>…</think>`` blocks are stripped (a safety net behind the
profile's thinking-off switches; only tagged blocks, never prose --
FM-1 / FM-42) and unparsed tool-call markup marks the report partial
(FM-41).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from stf_v3.diagnosis.agent.deps import ToolCallTrace

_DTC_RE = re.compile(r"\b([PCBU][0-9][0-9A-F]{3})\b")
_MANUAL_REF_RE = re.compile(r"`?([0-9a-fA-F-]{8,}|[A-Za-z0-9_.-]{2,})#([A-Za-z0-9_.-]+)`?")
# Complete thinking blocks only (open + matching close); an unterminated tag
# or prose that merely mentions "thinking" is left alone (FM-1).
_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>[ \t]*\n?", re.DOTALL | re.IGNORECASE)
# Tool-call markup that reached the text = the endpoint's parser missed it (FM-41).
_TOOL_CALL_RESIDUE_RE = re.compile(r"</?(tool_call|function_call)>|<function=", re.IGNORECASE)

TOOL_CALL_RESIDUE_LIMITATION = (
    "Tool-call markup reached the report unparsed (the endpoint's tool-call "
    "parser did not recognise it); findings are unverified."
)


def strip_thinking_residue(text: str) -> Tuple[str, int, int]:
    """Remove complete ``<think>…</think>`` blocks from ``text``.

    Returns:
        ``(clean_text, blocks_removed, chars_removed)``; ``(text, 0, 0)``
        when nothing matched, so the report is never altered silently
        (the counts go into the ``done`` event).
    """
    hits = 0
    removed = 0

    def _drop(m: "re.Match[str]") -> str:
        nonlocal hits, removed
        hits += 1
        removed += len(m.group(0))
        return ""

    clean = _THINK_BLOCK_RE.sub(_drop, text)
    return (clean, hits, removed) if hits else (text, 0, 0)


def has_tool_call_residue(text: str) -> bool:
    """Whether unparsed tool-call markup is present in the text."""
    return bool(_TOOL_CALL_RESIDUE_RE.search(text or ""))


@dataclass
class ReportCitation:
    """One reference the report relies on."""

    kind: str          # "manual" | "dtc"
    ref: str           # "<manual_id>#<slug>" or the DTC code
    source: str        # "trace" | "NO_SOURCE"
    tool: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "source": self.source, "tool": self.tool}


@dataclass
class DiagnosisReport:
    """What ``reports`` stores: markdown + citations + run facts."""

    content_md: str
    citations: List[ReportCitation] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    stopped_reason: str = "complete"
    model: str = "unknown"
    total_tokens: Optional[int] = None
    requests: int = 0
    tool_calls: int = 0
    elapsed_s: float = 0.0
    partial: bool = False
    # PROD-09: where the model ran + residue counters (FM-31 / FM-18 / FM-1)
    model_source: str = "test"
    thinking_chars: int = 0
    filter_hits: int = 0
    filter_removed_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_md": self.content_md,
            "citations": [c.to_dict() for c in self.citations],
            "limitations": list(self.limitations),
            "stopped_reason": self.stopped_reason,
            "model": self.model,
            "model_source": self.model_source,
            "total_tokens": self.total_tokens,
            "requests": self.requests,
            "tool_calls": self.tool_calls,
            "elapsed_s": round(self.elapsed_s, 2),
            "partial": self.partial,
            "thinking_chars": self.thinking_chars,
            "filter_hits": self.filter_hits,
            "filter_removed_chars": self.filter_removed_chars,
        }


def _dtcs_in_text(text: str) -> List[str]:
    return [m.upper() for m in _DTC_RE.findall(text)]


def _manual_refs_in_text(text: str, known_manual_ids: Sequence[str]) -> List[str]:
    refs: List[str] = []
    for manual_id, slug in _MANUAL_REF_RE.findall(text):
        if manual_id in known_manual_ids:
            refs.append(f"{manual_id}#{slug.rstrip('.,;:)')}")
    return refs


def extract_citations(
    content_md: str,
    trace: Sequence[ToolCallTrace],
    known_manual_ids: Sequence[str],
    dtcs_seen: Sequence[str] = (),
) -> List[ReportCitation]:
    """Citations proven by the trace, plus text references marked NO_SOURCE.

    Args:
        content_md: The final report text.
        trace: Every tool call of the run (main + sub-agents).
        known_manual_ids: Manual ids the run could read.
        dtcs_seen: DTC codes the DTC tools surfaced (from the log).
    """
    out: List[ReportCitation] = []
    seen: set = set()
    for t in trace:
        if t.is_error:
            continue
        if t.name == "read_manual_section":
            ref = f"{t.input.get('manual_id')}#{t.input.get('section')}"
            if ref not in seen:
                seen.add(ref)
                out.append(ReportCitation("manual", ref, "trace", t.name))
        elif t.name == "lookup_dtc":
            code = str(t.input.get("code", "")).upper()
            if code and code not in seen:
                seen.add(code)
                out.append(ReportCitation("dtc", code, "trace", t.name))
    for code in dtcs_seen:
        code = code.upper()
        if code not in seen:
            seen.add(code)
            out.append(ReportCitation("dtc", code, "trace", "list_dtcs"))
    for ref in _manual_refs_in_text(content_md, known_manual_ids):
        if ref in seen:
            continue
        # The exact section was never read in this run → unproven.
        seen.add(ref)
        out.append(ReportCitation("manual", ref, "NO_SOURCE"))
    for code in _dtcs_in_text(content_md):
        if code not in seen:
            seen.add(code)
            out.append(ReportCitation("dtc", code, "NO_SOURCE"))
    return out

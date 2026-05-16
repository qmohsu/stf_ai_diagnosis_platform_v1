"""Production-side Pydantic shapes for harness sub-agents.

These are the types that production code (``app.harness_agents.*``)
produces.  They are imported by the evaluation suite under
``tests/harness/evals/schemas.py``, but they do not depend on any
eval-specific types — keeping the production/test layering clean.

Eval-only shapes (``GoldenEntry``, ``GoldenCitation``, ``Grade``)
live in ``tests/harness/evals/schemas.py`` and consume these via
re-export.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Citation produced by a sub-agent in its final output.

    Attributes:
        manual_id: Manual filename stem the agent cited.
        slug: Section slug the agent cited.
        quote: Optional verbatim span the agent references.
    """

    manual_id: str
    slug: str
    quote: str = ""


class SectionRef(BaseModel):
    """Raw manual content a sub-agent pulled during investigation.

    Attributes:
        manual_id: Manual filename stem.
        slug: Section slug.
        text: Full section text (images stripped — ``had_images``
            records whether the original content block list
            included image blocks).
        had_images: Whether the original tool output contained at
            least one ``image_url`` content block.
    """

    manual_id: str
    slug: str
    text: str
    had_images: bool = False


class ToolCallTrace(BaseModel):
    """One tool invocation recorded during an agent run.

    Attributes:
        name: Registered tool name (e.g. ``get_manual_toc``).
        input: Tool input dict passed through the registry, with
            any ``_`` -prefixed injections stripped and long
            values truncated for report-friendliness.
        latency_ms: Wall-clock duration of the tool call.
        is_error: Whether the registry flagged the result as an
            error.
    """

    name: str
    input: Dict[str, Any]
    latency_ms: float
    is_error: bool = False


StoppedReason = Literal[
    "complete", "max_iterations", "timeout", "error",
]
"""Why a sub-agent loop ended."""


class ManualAgentResult(BaseModel):
    """Full output of one manual-agent run.

    Attributes:
        summary: Agent's final summary answering the question.
        citations: Sections the agent explicitly cited.
        raw_sections: Full section text the agent pulled via
            ``read_manual_section`` during investigation.
        tool_trace: Ordered list of tool invocations.
        iterations: Number of ReAct cycles consumed (including
            the terminal step that produced the final answer).
        total_tokens: Approximate input + output token total
            across all LLM calls in the agent run.  ``0`` when
            the underlying ``LLMClient`` does not surface usage.
        stopped_reason: Why the loop ended.
    """

    summary: str
    citations: List[Citation] = Field(default_factory=list)
    raw_sections: List[SectionRef] = Field(default_factory=list)
    tool_trace: List[ToolCallTrace] = Field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: StoppedReason = "complete"


# ── OBD sub-agent types ───────────────────────────────────────────


class SignalCitation(BaseModel):
    """One signal/timestamp reference produced by the OBD sub-agent.

    Parallel to ``Citation`` for the manual sub-agent — same role but
    addresses time-series data rather than manual prose.

    Attributes:
        signal: Signal/column name (e.g. ``RPM`` or
            ``A_YAM_INJ_MS``).
        time_range: Optional ISO start/end the citation refers to.
            Omitted for whole-session references.
        value: Optional point or statistic value.
        stat: Name of the statistic when ``value`` is an aggregate
            (e.g. ``"p95"``, ``"mean"``, ``"max"``).
        units: Engineering units for ``value``.
    """

    signal: str
    time_range: Optional[Tuple[str, str]] = None
    value: Optional[float] = None
    stat: Optional[str] = None
    units: Optional[str] = None


class DTCCitation(BaseModel):
    """One DTC reference produced by the OBD sub-agent.

    Attributes:
        code: DTC code string — either standard P/C/B/U format
            (e.g. ``"P0117"``) or Yamaha-proprietary raw hex
            (e.g. ``"87F11043000000000000CB"``).
        status: Whether the code is stored or pending.
        ecu: Originating ECU label (e.g. ``"K-Line"``,
            ``"CAN-ABS"``).
    """

    code: str
    status: Literal["stored", "pending"]
    ecu: Optional[str] = None


DataExcerptKind = Literal["stats", "events", "window", "dtcs"]
"""Tag for the kind of tool output preserved in ``DataExcerpt``."""


class DataExcerpt(BaseModel):
    """Verbatim tool-output block the OBD sub-agent pulled.

    Used so the main agent can quote the sub-agent's evidence
    directly rather than re-deriving it.  ``payload`` preserves the
    shape of the tool's output dict — schema varies per ``kind``.

    Attributes:
        kind: Which tool produced this excerpt.
        payload: Tool-output content (stats dict, event list,
            window samples, DTC entry).
    """

    kind: DataExcerptKind
    payload: Dict[str, Any]


class OBDAgentResult(BaseModel):
    """Full output of one OBD-agent run.

    Mirrors ``ManualAgentResult`` where structurally identical and
    diverges where OBD data shape requires it.  Two citation lists
    (signal + DTC) instead of one polymorphic list — signals and
    DTCs live in different conceptual spaces.  ``limitations`` is
    additive: the OBD sub-agent frequently needs to flag missing
    data (Channel B not present, Yamaha-hex not decodable,
    freeze-frame absent) for the main agent.

    Attributes:
        summary: 3-5 sentence answer to the inquiry.
        signal_citations: Signal/time-range references.
        dtc_citations: DTC references.
        raw_data: Tool-output excerpts the main agent can quote.
        limitations: Concrete reasons something could not be
            answered (e.g. ``"Yamaha hex DTC not decodable"``).
        tool_trace: Ordered list of tool invocations.
        iterations: ReAct cycles consumed.
        total_tokens: Approximate input + output token total.
        stopped_reason: Why the loop ended.
    """

    summary: str
    signal_citations: List[SignalCitation] = Field(
        default_factory=list,
    )
    dtc_citations: List[DTCCitation] = Field(default_factory=list)
    raw_data: List[DataExcerpt] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    tool_trace: List[ToolCallTrace] = Field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: StoppedReason = "complete"

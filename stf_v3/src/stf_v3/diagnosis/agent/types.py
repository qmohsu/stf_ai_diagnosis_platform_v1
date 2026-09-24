"""Structured results of the two sub-agents (copied from V2 ``types.py``).

Author: Xiangzhu Yan
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

StoppedReason = Literal["complete", "max_iterations", "timeout", "error", "budget"]


class Citation(BaseModel):
    """A manual section the sub-agent cited."""

    manual_id: str
    slug: str
    quote: str = ""


class SectionRef(BaseModel):
    """A section the sub-agent actually read (evidence for grading)."""

    manual_id: str
    slug: str
    text: str
    had_images: bool = False


class ToolTraceEntry(BaseModel):
    """Serialisable tool-call trace entry."""

    name: str
    input: Dict[str, Any]
    latency_ms: float
    is_error: bool = False


class ManualAgentResult(BaseModel):
    """Full output of one manual sub-agent run."""

    summary: str
    citations: List[Citation] = Field(default_factory=list)
    raw_sections: List[SectionRef] = Field(default_factory=list)
    tool_trace: List[ToolTraceEntry] = Field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: StoppedReason = "complete"
    nudged: bool = False   # PROD-10: the one-shot "finish" nudge fired (eval stats)


class SignalCitation(BaseModel):
    """One signal / statistic reference from the OBD sub-agent."""

    signal: str
    time_range: Optional[Tuple[str, str]] = None
    value: Optional[float] = None
    stat: Optional[str] = None
    units: Optional[str] = None


class DTCCitation(BaseModel):
    """One DTC reference from the OBD sub-agent."""

    code: str
    status: Literal["stored", "pending"]
    ecu: Optional[str] = None


DataExcerptKind = Literal["stats", "events", "window", "dtcs"]


class DataExcerpt(BaseModel):
    """Verbatim tool-output block the OBD sub-agent pulled."""

    kind: DataExcerptKind
    payload: Dict[str, Any]


class OBDAgentResult(BaseModel):
    """Full output of one OBD sub-agent run."""

    summary: str
    signal_citations: List[SignalCitation] = Field(default_factory=list)
    dtc_citations: List[DTCCitation] = Field(default_factory=list)
    raw_data: List[DataExcerpt] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    tool_trace: List[ToolTraceEntry] = Field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: StoppedReason = "complete"
    nudged: bool = False   # PROD-10: the one-shot "finish" nudge fired (eval stats)

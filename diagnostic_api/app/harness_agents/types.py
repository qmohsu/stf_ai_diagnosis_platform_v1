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

from typing import Any, Dict, List, Literal

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

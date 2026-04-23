"""Pydantic schemas for the manual-agent evaluation suite.

Defines the contract between three layers:

- **Golden dataset** (``GoldenEntry``): human-reviewed question/answer
  pairs with citations to authoritative manual sections.  Stored as
  JSONL under ``golden/v1/``.
- **Agent output** (``ManualAgentResult``): what the manual sub-agent
  produces for a given question — a summary, citations, raw section
  text, and a trace of tool calls.
- **Judge output** (``Grade``): the LLM-as-judge's structured score
  against the golden entry, returned as JSON per the rubric.

All models use ``BaseModel`` with explicit type annotations so that
``model_dump()`` produces JSON-serializable payloads suitable for
both the golden JSONL files and the eval report artifact.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── Golden dataset models ─────────────────────────────────────────


class GoldenCitation(BaseModel):
    """Authoritative source anchor for a golden entry.

    Attributes:
        manual_id: Manual filename stem (e.g.
            ``MWS150A_Service_Manual``).
        slug: Section slug as produced by
            ``manual_fs.parse_heading_tree``.
        quote: Short verbatim span from the section.  Used by the
            judge's grounding check — must appear in the cited
            manual section.
    """

    manual_id: str
    slug: str
    quote: str


GoldenCategory = Literal[
    "dtc", "symptom", "component", "adversarial", "image",
]
"""Category of diagnostic inquiry the golden entry represents."""


GoldenDifficulty = Literal["easy", "medium", "hard"]
"""Expected difficulty for the manual agent."""


class GoldenEntry(BaseModel):
    """Single human-reviewed question/answer pair.

    Immutable once frozen under ``golden/v1/``.  Corrections bump
    the version directory (``v2/``) rather than editing in place,
    preventing silent drift of the eval set.

    Attributes:
        id: Stable identifier, e.g.
            ``mws150a-dtc-p0171-001``.
        category: One of ``dtc``, ``symptom``, ``component``,
            ``adversarial``, ``image``.
        difficulty: ``easy``, ``medium``, or ``hard``.
        question: Inquiry posed to the manual agent.  Typically
            derived from ``read_obd_data`` output in production.
        obd_context: Optional short snippet of OBD context that
            primes the agent (e.g. observed DTC list, symptom
            description).  None for pure manual-lookup tasks.
        golden_summary: Human-written reference summary, 3–5
            sentences.  Judge compares the agent's summary
            against this.
        golden_citations: Authoritative source locations the
            agent should have consulted.
        expected_tool_trace: Loose guide for trajectory scoring
            (order matters qualitatively, not strictly).
        must_contain: Key facts that MUST appear in the agent's
            summary or ``raw_sections``.
        must_not_contain: Hallucination guards — strings that
            MUST NOT appear in the agent output.
        requires_image: Whether the answer is only complete if
            the agent returns a multimodal block containing an
            image (wiring diagram, exploded view, flowchart).
        notes: Free-form reviewer comments.
    """

    id: str
    category: GoldenCategory
    difficulty: GoldenDifficulty
    question: str
    obd_context: Optional[str] = None
    golden_summary: str
    golden_citations: List[GoldenCitation]
    expected_tool_trace: List[str]
    must_contain: List[str] = Field(default_factory=list)
    must_not_contain: List[str] = Field(default_factory=list)
    requires_image: bool = False
    notes: str = ""


# ── Agent output models ───────────────────────────────────────────


class Citation(BaseModel):
    """Citation produced by the agent in its final output.

    Attributes:
        manual_id: Manual filename stem the agent cited.
        slug: Section slug the agent cited.
        quote: Optional verbatim span the agent references.
    """

    manual_id: str
    slug: str
    quote: str = ""


class SectionRef(BaseModel):
    """Raw manual content the agent pulled during investigation.

    Attributes:
        manual_id: Manual filename stem.
        slug: Section slug.
        text: Full section text (images stripped — the agent may
            separately record whether image blocks were present).
        had_images: Whether the original content block list
            included at least one ``image_url`` block.
    """

    manual_id: str
    slug: str
    text: str
    had_images: bool = False


class ToolCallTrace(BaseModel):
    """One tool invocation recorded during the agent run.

    Attributes:
        name: Registered tool name (e.g. ``get_manual_toc``).
        input: Tool input dict passed through the registry.
        latency_ms: Wall-clock duration of the tool call.
        is_error: Whether the registry flagged the result as an
            error.
    """

    name: str
    input: Dict[str, Any]
    latency_ms: float
    is_error: bool = False


class ManualAgentResult(BaseModel):
    """Full output of one manual-agent run.

    Attributes:
        summary: Agent's final summary answering the question.
        citations: Sections the agent explicitly cited.
        raw_sections: Full section text the agent pulled.
        tool_trace: Ordered list of tool invocations.
        iterations: Number of ReAct cycles consumed.
        total_tokens: Approximate input + output token total
            across all LLM calls in the agent run.
        stopped_reason: Why the loop ended — ``"complete"``,
            ``"max_iterations"``, ``"timeout"``, or ``"error"``.
    """

    summary: str
    citations: List[Citation] = Field(default_factory=list)
    raw_sections: List[SectionRef] = Field(default_factory=list)
    tool_trace: List[ToolCallTrace] = Field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    stopped_reason: Literal[
        "complete", "max_iterations", "timeout", "error",
    ] = "complete"


# ── Judge output ──────────────────────────────────────────────────


class Grade(BaseModel):
    """Structured judge verdict for one agent run.

    The judge (``z-ai/glm-5.1``) returns this shape as JSON;
    Pydantic validates and retries once on parse failure per
    the project-wide error-handling rule.

    Attributes:
        section_match: 1 if the agent cited at least one golden
            slug, else 0.
        fact_recall: Fraction of ``must_contain`` items present
            in the agent's summary or raw_sections (0.0 to 1.0).
        hallucination: 1 if any ``must_not_contain`` string
            appears in the agent output, else 0.  (Inverted in
            the weighted overall so higher = worse.)
        citation_present: 1 if ``citations`` is non-empty, else
            0.
        trajectory_ok: 1 if the agent used at most ~1.5x the
            expected tool-call count and did not brute-force
            read every section, else 0.  Reported, not enforced
            via ``overall``.
        overall: Weighted score in [0.0, 1.0].  Formula:
            0.4*section_match + 0.3*fact_recall
            + 0.2*(1 - hallucination) + 0.1*citation_present.
        reasoning: 2–3 sentences citing specific evidence for
            the scores above.
    """

    section_match: int = Field(ge=0, le=1)
    fact_recall: float = Field(ge=0.0, le=1.0)
    hallucination: int = Field(ge=0, le=1)
    citation_present: int = Field(ge=0, le=1)
    trajectory_ok: int = Field(ge=0, le=1)
    overall: float = Field(ge=0.0, le=1.0)
    reasoning: str

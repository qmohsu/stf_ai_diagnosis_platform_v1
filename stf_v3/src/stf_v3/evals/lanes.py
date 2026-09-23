"""The two eval lanes: run a V3 sub-agent on one golden, map its result.

Mapping (verbatim V2 copy, the part that decides comparability):
``_agent_result_to_system_run`` / ``_extract_surfaced_images`` come from
``diagnostic_api/tests/harness/evals/runner.py`` and
``_format_signal_citation`` / ``_format_dtc_citation`` /
``_serialize_output_text`` / ``_obd_result_to_system_run`` from
``obd_runner.py`` (both @ 9c9e7a9).  They turn a sub-agent result into
the text the scorer grades; any drift here moves every score (PROD-10
FM-1 / FM-41, locked by ``tests/test_evals_equivalence.py``).

Execution (new for V3): each golden runs the production sub-agent
(``run_manual_agent`` / ``run_obd_agent``) with production settings on a
FRESH ``DiagDeps`` (FM-50): vehicle = Yamaha TRICITY155 with the fixture
VIN (never a real one), the OBD log = the copied road-test fixture, the
manual inventory = the V3 library.  Nothing is persisted (FM-15).

Author: Li-Ta Hsu (mapping) / Xiangzhu Yan (V3 execution)
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog
from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.deps import (
    Budgets,
    DiagDeps,
    LogInfo,
    ManualInfo,
    VehicleInfo,
)
from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.evals.schemas import (
    DTCCitation,
    GoldenEntry,
    ManualAgentResult,
    OBDAgentResult,
    SignalCitation,
    SurfacedImage,
    SystemRunResult,
)

logger = structlog.get_logger(__name__)


# ═════ V2 mapping helpers (verbatim copy) ═══════════════════════════

_VISION_DESC_RE = re.compile(
    r"\*Vision description:\s*(.*?)\*",
    re.DOTALL,
)
"""``*Vision description: ...*`` paragraphs per the manual markdown
schema (docs/manual_markdown_schema.md §5.2).  ``build_multimodal_
section`` strips the ``![...](...)`` image ref when it loads the
image bytes, but the italic vision paragraph that FOLLOWS each image
stays in the text blocks — so it survives into
``SectionRef.text`` and is the richest image evidence the eval
adapter can recover without re-reading the manual from disk."""


_MD_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
"""Residual markdown image refs.  Present in ``SectionRef.text``
only when the image bytes could NOT be loaded (missing file) —
loaded images have their refs stripped by
``build_multimodal_section``.  Still evidence that the section
carries a figure."""


def _extract_surfaced_images(
    result: ManualAgentResult,
    claim_slugs: List[str],
) -> List[SurfacedImage]:
    """Collect per-section image evidence for the judge (#193).

    ``SystemRunResult.output_text`` is text-only, so without this
    the judge is never told which figures the agent surfaced and
    image-required entries are structurally capped on
    ``answer_quality``.  For every read section that carried
    image content — an ``image_url`` block in the tool output
    (``SectionRef.had_images``), a vision-description paragraph,
    or a residual markdown image ref — emit one
    ``SurfacedImage`` with the section identity, whether it was
    cited, a best-effort figure count, and the vision
    descriptions found in the section text.

    Args:
        result: The agent loop's return value.
        claim_slugs: Deduplicated cited slugs, used to flag
            which image-bearing sections the agent cited as
            answer sources (vs merely browsed).

    Returns:
        One ``SurfacedImage`` per image-bearing section, in
        first-read order, deduplicated by slug.
    """
    cited = set(claim_slugs)
    surfaced: List[SurfacedImage] = []
    seen: set = set()
    for sec in result.raw_sections or []:
        if not sec.slug or sec.slug in seen:
            continue
        text = sec.text or ""
        vision_descs = [
            " ".join(m.split())
            for m in _VISION_DESC_RE.findall(text)
        ]
        md_refs = len(_MD_IMAGE_REF_RE.findall(text))
        image_count = max(
            len(vision_descs),
            md_refs,
            1 if sec.had_images else 0,
        )
        if image_count == 0:
            continue
        seen.add(sec.slug)
        surfaced.append(SurfacedImage(
            slug=sec.slug,
            manual_id=sec.manual_id,
            cited=sec.slug in cited,
            image_count=image_count,
            vision_descriptions=vision_descs,
        ))
    return surfaced


def _agent_result_to_system_run(
    question: str,
    result: ManualAgentResult,
    latency_ms_wall: float,
) -> SystemRunResult:
    """Adapt a ``ManualAgentResult`` into the unified shape.

    The unified ``SystemRunResult`` is what the comparative
    judge consumes — both ``run_manual_agent_unified`` and
    ``run_rag`` produce it, so the rubric is identical for both
    systems.

    Mapping rules:

    - ``output_text`` ← agent's **synthesised summary plus the
      CITED section text** (sections whose slug appears in
      ``claim_slugs``), joined by a clear separator.  This
      treats the agent's "deliverable" as the synthesis PLUS
      the source sections it actually relied on — NOT every
      section it browsed during navigation.  Exploration
      overhead (sections read but not cited) is captured by
      ``exploration_cost``, not double-counted here.  Cross-
      language ``fact_recall`` still works because Chinese
      ``must_contain`` terms come from the cited sections by
      construction (that's where they came from when the
      golden was authored).  Mirrors RAG's ``output_text``
      shape (concatenated content) but filters the agent's
      navigation noise that would otherwise dilute the
      conciseness signal in ``fact_density``.
    - ``claim_slugs`` ← parser-canonical slugs from
      ``result.citations[].slug``, deduplicated.  These are
      the sections the agent **explicitly cited as answer
      sources** in its final JSON.  Used by
      ``claim_precision`` and ``citation_quality``.
    - ``read_slugs`` ← parser-canonical slugs from
      ``result.raw_sections[].slug``, deduplicated.  These
      are sections the agent **actually accessed** via
      ``read_manual_section`` calls — including index/TOC
      sections used for navigation, even when they didn't
      end up in the final answer.  Used by
      ``exploration_cost``.
    - ``surfaced_images`` ← per-section image evidence from
      ``_extract_surfaced_images`` (#193): slug, cited flag,
      figure count, and vision descriptions for every read
      section that carried image content.  Rendered in the
      judge prompt so image-required entries aren't capped.
    - ``tool_trace``, ``stopped_reason``, ``iterations`` ←
      passed through.
    - ``latency_ms_llm`` ← sum of tool-call latencies as a
      proxy for LLM time (the OpenAI SDK doesn't surface
      per-call ``usage.duration`` reliably across providers).
      Reasonable approximation; tighten later if needed.
    - ``cost_usd`` ← left at 0.0 here; the eval driver
      (``eval_one_golden``) computes it from OpenRouter
      response metadata.

    Args:
        question: Original inquiry, echoed for report-building.
        result: The agent loop's return value.
        latency_ms_wall: External wall-clock timing captured
            by the caller (the agent loop doesn't time itself).

    Returns:
        Normalised ``SystemRunResult``.
    """
    # Slugs the agent explicitly CITED as answer sources.  The
    # slug-canonicalisation fix in ``manual_agent._parse_final_json``
    # ensures these are parser-canonical even when the LLM
    # echoed the section's display title.
    claim_slugs: List[str] = []
    seen_claim: set = set()
    for cit in result.citations or []:
        if cit.slug and cit.slug not in seen_claim:
            seen_claim.add(cit.slug)
            claim_slugs.append(cit.slug)

    # Slugs the agent actually READ via read_manual_section.
    # May overlap with claim_slugs (when the agent cites what
    # it read) or diverge (when the LLM cites from memory).
    read_slugs: List[str] = []
    seen_read: set = set()
    for sec in result.raw_sections or []:
        if sec.slug and sec.slug not in seen_read:
            seen_read.add(sec.slug)
            read_slugs.append(sec.slug)

    # Compose the "deliverable" — summary plus CITED sections.
    # Filtering by ``claim_slugs`` excludes navigation overhead:
    # sections the agent merely browsed to triangulate the answer
    # (TOC entries, ruled-out hypotheses) shouldn't bloat the
    # downstream LLM's context, and shouldn't be double-counted
    # against the agent in ``fact_density``.  The exploration
    # cost is already captured by the ``exploration_cost`` metric.
    # Mirrors RAG's ``output_text`` shape (concatenated content);
    # ensures cross-language ``fact_recall`` is symmetric across
    # systems because ``must_contain`` terms come from cited
    # sections by golden-authoring convention.  The separator is
    # human-readable and unambiguous so downstream tooling (judge
    # prompts, report viewers) can cleanly split the synthesis
    # from the source evidence.
    summary = result.summary or ""
    cited_slugs_set = set(claim_slugs)
    section_blocks = [
        f"[{sec.slug}]\n{sec.text}"
        for sec in (result.raw_sections or [])
        if sec.text and sec.slug in cited_slugs_set
    ]
    if section_blocks:
        sections_text = "\n\n".join(section_blocks)
        output_text = (
            f"{summary}\n\n--- Cited sections "
            f"({len(section_blocks)}) ---\n\n{sections_text}"
        )
    else:
        output_text = summary

    # Sum tool-call latencies as a proxy for LLM time.  Imperfect
    # — tool-call duration includes the round-trip but not the
    # LLM-side reasoning time spent BETWEEN tool calls.  Wall
    # clock is the more honest number here.
    llm_proxy = sum(
        (t.latency_ms or 0.0)
        for t in (result.tool_trace or [])
    )

    return SystemRunResult(
        system_label="manual_agent",
        question=question,
        output_text=output_text,
        claim_slugs=claim_slugs,
        read_slugs=read_slugs,
        retrieved_chunk_metadata=[],
        latency_ms_wall=latency_ms_wall,
        latency_ms_llm=llm_proxy,
        cost_usd=0.0,
        tool_trace=list(result.tool_trace or []),
        stopped_reason=str(result.stopped_reason or "complete"),
        iterations=result.iterations or 0,
        surfaced_images=_extract_surfaced_images(
            result, claim_slugs,
        ),
    )


def _format_signal_citation(c: SignalCitation) -> str:
    """Render one ``SignalCitation`` as a human-readable line.

    Format: ``<signal>[ (<stat>)][ = <value>[ <units>]][  @ [t1, t2]]``.
    Trailing parts only appear when populated.
    """
    parts = [c.signal]
    if c.stat:
        parts.append(f"({c.stat})")
    if c.value is not None:
        val_str = f"= {c.value}"
        if c.units:
            val_str += f" {c.units}"
        parts.append(val_str)
    line = " ".join(parts)
    if c.time_range is not None:
        line += f"  @ [{c.time_range[0]}, {c.time_range[1]}]"
    return line


def _format_dtc_citation(c: DTCCitation) -> str:
    """Render one ``DTCCitation`` as a human-readable line.

    Format: ``<code> (<status>[, <ecu>])``.
    """
    inner = c.status
    if c.ecu:
        inner += f", {c.ecu}"
    return f"{c.code} ({inner})"


def _serialize_output_text(result: OBDAgentResult) -> str:
    """Compose the ``output_text`` for the judge prompt.

    Blocks (each omitted when its source list is empty):

    1. The agent's ``summary`` (always present, even if a
       placeholder).
    2. ``--- Signal citations (N) ---`` followed by one formatted
       line per citation.
    3. ``--- DTC citations (N) ---`` followed by one formatted line
       per citation.
    4. ``--- Limitations ---`` followed by one bullet per entry.

    The judge sees both the prose summary and the structured
    claims as text, so ``must_contain`` / pitfall_directives /
    answer_quality all grade against the same artefact.
    """
    blocks: List[str] = [result.summary or ""]

    if result.signal_citations:
        lines = [
            f"--- Signal citations "
            f"({len(result.signal_citations)}) ---",
        ]
        for cite in result.signal_citations:
            lines.append(_format_signal_citation(cite))
        blocks.append("\n".join(lines))

    if result.dtc_citations:
        lines = [
            f"--- DTC citations "
            f"({len(result.dtc_citations)}) ---",
        ]
        for cite in result.dtc_citations:
            lines.append(_format_dtc_citation(cite))
        blocks.append("\n".join(lines))

    if result.limitations:
        lines = ["--- Limitations ---"]
        for lim in result.limitations:
            lines.append(f"- {lim}")
        blocks.append("\n".join(lines))

    return "\n\n".join(b for b in blocks if b)


def _obd_result_to_system_run(
    question: str,
    result: OBDAgentResult,
    latency_ms_wall: float,
) -> SystemRunResult:
    """Adapt an ``OBDAgentResult`` into ``SystemRunResult``.

    Mapping:

    - ``system_label="obd_agent"``.
    - ``output_text`` ← deterministic serialization of summary +
      signal/DTC citations + limitations (see
      ``_serialize_output_text``).
    - ``claim_slugs=[]``, ``read_slugs=[]`` — OBD has no slug
      concept; the manual lane's slug-based metrics short-circuit
      to neutral values in the dispatcher.
    - ``obd_signal_citations`` / ``obd_dtc_citations`` ← passed
      through.
    - ``tool_trace``, ``iterations``, ``stopped_reason`` ← passed
      through.
    - ``latency_ms_llm`` ← sum of tool-call latencies (proxy;
      matches the manual lane's convention).
    - ``cost_usd=0.0`` — left for a future driver-level
      computation against OpenRouter usage records.

    Args:
        question: Original inquiry, echoed for report-building.
        result: Agent loop's return value.
        latency_ms_wall: External wall-clock timing captured by
            the caller (the agent loop doesn't time itself).

    Returns:
        Normalised ``SystemRunResult``.
    """
    output_text = _serialize_output_text(result)
    llm_proxy = sum(
        (t.latency_ms or 0.0)
        for t in (result.tool_trace or [])
    )
    return SystemRunResult(
        system_label="obd_agent",
        question=question,
        output_text=output_text,
        claim_slugs=[],
        read_slugs=[],
        retrieved_chunk_metadata=[],
        latency_ms_wall=latency_ms_wall,
        latency_ms_llm=llm_proxy,
        cost_usd=0.0,
        tool_trace=list(result.tool_trace or []),
        stopped_reason=str(result.stopped_reason or "complete"),
        iterations=result.iterations or 0,
        obd_signal_citations=list(result.signal_citations or []),
        obd_dtc_citations=list(result.dtc_citations or []),
    )



# ═════ V3 execution layer ════════════════════════════════════════════

LANE_MANUAL = "manual_agent"
LANE_OBD = "obd_agent"
LANES = (LANE_MANUAL, LANE_OBD)

FAKE_VIN = "JHMGK5830HX202404"
"""The repo's fixture VIN: evals never carry a real vehicle identity."""

CORPUS_MANUFACTURER = "Yamaha"
CORPUS_MODEL = "TRICITY155"
"""The vehicle every golden is about (V2 ``lanes.CORPUS_VEHICLE``)."""

OBD_FIXTURE_NAME = "yamaha_road_test.csv"
OBD_LOG_FORMAT = "yamaha"


@dataclass
class LaneContext:
    """What every golden run shares (the model and the read-only data).

    Attributes:
        model: The Pydantic AI model object (local vLLM or cloud).
        manuals: The V3 manual inventory (all ingested manuals, as in
            production; the vehicle pin picks MWS-150-A).
        manual_root: The manual library root (mounted read-only).
        fixture_dir: Directory holding the OBD road-test fixture.
        budgets_factory: Builds the per-run ``Budgets`` (fresh each time).
        model_is_local: False for the cloud comparison (VIN pseudonym).
        images_enabled: Manual images to the model (production: off).
    """

    model: Any
    manuals: List[ManualInfo]
    manual_root: Path
    fixture_dir: Path
    budgets_factory: Callable[[], Budgets]
    model_is_local: bool = True
    images_enabled: bool = False


@dataclass
class ItemStats:
    """Per-golden run statistics recorded next to the grade (FM-30)."""

    stopped_reason: str = "complete"
    requests: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    wall_s: float = 0.0
    nudged: bool = False
    thinking_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stopped_reason": self.stopped_reason, "requests": self.requests,
            "tool_calls": self.tool_calls, "total_tokens": self.total_tokens,
            "wall_s": round(self.wall_s, 2), "nudged": self.nudged,
            "thinking_chars": self.thinking_chars,
        }


def build_item_deps(ctx: LaneContext) -> DiagDeps:
    """A fresh ``DiagDeps`` for one golden (no state shared between items)."""
    vehicle_id = uuid.uuid4()
    return DiagDeps(
        vehicle=VehicleInfo(id=vehicle_id, manufacturer=CORPUS_MANUFACTURER,
                            model=CORPUS_MODEL, vin=FAKE_VIN),
        log=LogInfo(id=uuid.uuid4(), vehicle_id=vehicle_id, raw_path=OBD_FIXTURE_NAME,
                    format=OBD_LOG_FORMAT, original_filename=OBD_FIXTURE_NAME),
        manuals=list(ctx.manuals),
        log_root=ctx.fixture_dir,
        manual_root=ctx.manual_root,
        budgets=ctx.budgets_factory(),
        locale="en",
        model_is_local=ctx.model_is_local,
        images_enabled=ctx.images_enabled,
        events=EventSink(),
    )


def _thinking_chars(sink: EventSink) -> int:
    return sum(len(e.payload.get("text", "")) for e in sink.events if e.event_type == "reasoning")


def _check_entry(entry: GoldenEntry) -> None:
    if entry.vehicle is not None:
        # V2 could render any vehicle string (or none); V3 always pins the
        # vehicle record.  No locked golden uses the override (T-2 checks).
        raise ValueError(f"golden {entry.id}: per-entry vehicle overrides are not supported in V3")


async def run_manual_item(entry: GoldenEntry, ctx: LaneContext) -> Tuple[SystemRunResult, ItemStats]:
    """Run the V3 manual sub-agent on one golden and map the result."""
    from stf_v3.diagnosis.agent.manual_agent import run_manual_agent

    _check_entry(entry)
    deps = build_item_deps(ctx)
    t0 = time.perf_counter()
    result = await run_manual_agent(deps, ctx.model, entry.question, entry.obd_context, None, RunUsage())
    wall_ms = (time.perf_counter() - t0) * 1000
    await deps.events.drain()
    run = _agent_result_to_system_run(entry.question, result, wall_ms)
    return run, ItemStats(
        stopped_reason=str(result.stopped_reason), requests=result.iterations,
        tool_calls=len(result.tool_trace), total_tokens=result.total_tokens,
        wall_s=wall_ms / 1000, nudged=result.nudged, thinking_chars=_thinking_chars(deps.events),
    )


async def run_obd_item(entry: GoldenEntry, ctx: LaneContext) -> Tuple[SystemRunResult, ItemStats]:
    """Run the V3 OBD sub-agent on one golden (the road-test fixture)."""
    from stf_v3.diagnosis.agent.obd_agent import run_obd_agent

    _check_entry(entry)
    deps = build_item_deps(ctx)
    t0 = time.perf_counter()
    result = await run_obd_agent(deps, ctx.model, entry.question, None, RunUsage())
    wall_ms = (time.perf_counter() - t0) * 1000
    await deps.events.drain()
    run = _obd_result_to_system_run(entry.question, result, wall_ms)
    return run, ItemStats(
        stopped_reason=str(result.stopped_reason), requests=result.iterations,
        tool_calls=len(result.tool_trace), total_tokens=result.total_tokens,
        wall_s=wall_ms / 1000, nudged=result.nudged, thinking_chars=_thinking_chars(deps.events),
    )


RUNNERS = {LANE_MANUAL: run_manual_item, LANE_OBD: run_obd_item}

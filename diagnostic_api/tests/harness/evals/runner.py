"""Manual-agent runner for the evaluation suite.

Thin wrapper that the parametrized eval test uses to execute the
manual sub-agent once per golden entry.  Responsibilities:

- Build default ``ManualAgentDeps`` pointing at the local Ollama
  endpoint (``settings.llm_endpoint``) so the agent runs on the
  same model that ships (``qwen3.5:27b-q8_0``).
- Allow tests / plumbing runs to inject alternate deps via the
  ``deps`` kwarg — this is how the ``--mock-agent`` flag works.
- Return a fully-populated ``ManualAgentResult`` ready for the
  judge.

The actual ReAct loop lives in ``app.harness_agents.manual_agent``;
this module only handles the "how do I configure a production-ish
agent for one eval run" concern.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import time
from typing import List, Optional

import structlog

from app.config import settings
from app.harness.deps import OllamaNativeLLMClient
from app.harness_agents.manual_agent import (
    ManualAgentConfig,
    ManualAgentDeps,
    create_manual_agent_registry,
    run_manual_agent as _run_agent_loop,
)
from app.harness_agents.types import ManualAgentResult
from tests.harness.evals.schemas import SystemRunResult

logger = structlog.get_logger(__name__)


# Module-level cache for the real deps so we don't rebuild the
# OpenAI client + tool registry once per golden entry.  Tests
# that need isolation pass their own ``deps`` kwarg.
_cached_deps: Optional[ManualAgentDeps] = None


def _build_default_deps() -> ManualAgentDeps:
    """Construct deps pointing at local Ollama.

    The eval suite's primary target is the model that ships —
    ``qwen3.5:27b-q8_0`` served by Ollama on the PolyU box.  For
    ceiling comparison (phase 5), callers can override
    ``ManualAgentConfig.model`` to an OpenRouter identifier and
    pass their own deps.

    Uses ``OllamaNativeLLMClient`` (Ollama's native ``/api/chat``
    with ``think=False``) rather than the OpenAI-compatible ``/v1``
    adapter: ``/v1`` cannot suppress qwen3's reasoning channel, so
    the agent ran at ~36 s/call in thinking mode and timed out
    adversarial goldens before it could navigate AND synthesise
    (HARNESS-23 / #144).  Production delegation is unaffected — it
    runs on the shared OpenRouter client.

    Returns:
        ``ManualAgentDeps`` ready to pass into
        ``run_manual_agent``.
    """
    return ManualAgentDeps(
        llm_client=OllamaNativeLLMClient(
            settings.llm_endpoint, think=False, timeout_seconds=300.0,
        ),
        tool_registry=create_manual_agent_registry(),
        config=ManualAgentConfig(),
    )


def _get_default_deps() -> ManualAgentDeps:
    """Return a process-cached default deps instance."""
    global _cached_deps
    if _cached_deps is None:
        _cached_deps = _build_default_deps()
    return _cached_deps


async def run_manual_agent(
    question: str,
    obd_context: Optional[str] = None,
    deps: Optional[ManualAgentDeps] = None,
) -> ManualAgentResult:
    """Run the manual sub-agent for one golden entry.

    Args:
        question: The inquiry to answer.
        obd_context: Optional OBD context snippet.
        deps: Pre-built dependency container.  Tests use this to
            inject a fake ``LLMClient``.  When ``None``, a
            process-cached default pointing at local Ollama is
            lazily constructed from ``settings``.

    Returns:
        A ``ManualAgentResult`` with summary, citations,
        raw_sections, tool_trace, and diagnostics metadata.
    """
    effective_deps = deps or _get_default_deps()

    logger.info(
        "manual_agent_runner_invoked",
        question_preview=question[:80],
        has_obd_context=obd_context is not None,
        model=effective_deps.config.model,
    )

    return await _run_agent_loop(
        question, obd_context, effective_deps,
    )


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
    )


async def run_manual_agent_unified(
    question: str,
    obd_context: Optional[str] = None,
    deps: Optional[ManualAgentDeps] = None,
) -> SystemRunResult:
    """Run the manual sub-agent and return a unified ``SystemRunResult``.

    Convenience wrapper that times the agent run and adapts the
    result into the unified shape.  The legacy
    ``run_manual_agent`` (returns ``ManualAgentResult``) is kept
    for callers that need the raw shape — they should migrate
    to this when convenient.

    Args:
        question: The inquiry to answer.
        obd_context: Optional OBD context snippet.
        deps: Optional pre-built dependency container.

    Returns:
        A ``SystemRunResult`` with ``system_label="manual_agent"``,
        ready for direct comparison with ``run_rag`` output via
        the shared judge.
    """
    wall_start = time.perf_counter()
    result = await run_manual_agent(question, obd_context, deps)
    wall_end = time.perf_counter()
    latency_ms_wall = (wall_end - wall_start) * 1000
    return _agent_result_to_system_run(
        question, result, latency_ms_wall,
    )


def _reset_cache_for_testing() -> None:
    """Test-only helper: drop the cached deps.

    Tests that swap environment variables between cases must
    call this so a previously-built cached deps doesn't leak.
    """
    global _cached_deps
    _cached_deps = None

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
from openai import AsyncOpenAI

from app.config import settings
from app.harness.deps import OpenAILLMClient
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

    Returns:
        ``ManualAgentDeps`` ready to pass into
        ``run_manual_agent``.
    """
    base_url = f"{settings.llm_endpoint}/v1"
    client = AsyncOpenAI(
        api_key="ollama",
        base_url=base_url,
        timeout=300.0,
    )
    return ManualAgentDeps(
        llm_client=OpenAILLMClient(client),
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

    - ``output_text`` ← agent's synthesised summary.
    - ``retrieved_slugs`` ← union of citation slugs and
      raw_section slugs, deduplicated, order preserved.  The
      slug-canonicalisation fix in ``manual_agent`` already
      ensures these are parser-canonical.
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
    slug_seq: List[str] = []
    seen: set = set()
    for cit in result.citations or []:
        if cit.slug and cit.slug not in seen:
            seen.add(cit.slug)
            slug_seq.append(cit.slug)
    for sec in result.raw_sections or []:
        if sec.slug and sec.slug not in seen:
            seen.add(sec.slug)
            slug_seq.append(sec.slug)

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
        output_text=result.summary or "",
        retrieved_slugs=slug_seq,
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

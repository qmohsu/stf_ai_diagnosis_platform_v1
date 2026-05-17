"""OBD-agent runner + adapter for the evaluation suite.

Mirrors ``runner.py`` (manual-agent equivalent).  Responsibilities:

- Build default ``OBDAgentDeps`` pointing at the local Ollama
  endpoint.  Honor ``OBD_EVAL_AGENT_MODEL`` env var so phase-3
  ceiling runs can swap to OpenRouter (e.g. ``z-ai/glm-5.1``)
  without code changes.
- Adapt the agent's ``OBDAgentResult`` into the unified
  ``SystemRunResult`` shape the judge consumes.  Critically, the
  ``output_text`` field is the serialized "deliverable" the judge
  grades — summary plus a deterministic rendering of signal/DTC
  citations + limitations.  See ``_obd_result_to_system_run`` for
  the exact format.

The actual ReAct loop lives in ``app.harness_agents.obd_agent``;
this module only handles eval-side configuration and adaptation.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

import structlog
from openai import AsyncOpenAI

from app.config import settings
from app.harness.deps import OpenAILLMClient
from app.harness_agents.obd_agent import (
    OBDAgentConfig,
    OBDAgentDeps,
    create_obd_agent_registry,
    run_obd_agent as _run_agent_loop,
)
from app.harness_agents.types import (
    DTCCitation,
    OBDAgentResult,
    SignalCitation,
)
from tests.harness.evals.schemas import SystemRunResult

logger = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────


_EVAL_MODEL_ENV_VAR = "OBD_EVAL_AGENT_MODEL"
"""Env var that overrides the agent model for ceiling runs.

When set to an OpenRouter-style identifier (contains ``/``), the
runner builds an ``AsyncOpenAI`` client pointed at
``settings.premium_llm_base_url`` instead of Ollama.  When unset
or set to a plain Ollama tag, builds the Ollama client.

This lets phase-3 ceiling comparison work without duplicating the
agent code:

    OBD_EVAL_AGENT_MODEL=z-ai/glm-5.1 \\
        pytest -m eval --run-eval tests/harness/evals/test_obd_agent_eval.py
"""


# ── Default-deps factory ─────────────────────────────────────────


_cached_deps: Optional[OBDAgentDeps] = None
"""Per-process cache so we don't rebuild the OpenAI client + tool
registry once per golden entry.  Tests that need isolation pass
their own ``deps`` kwarg."""


def _build_default_deps() -> OBDAgentDeps:
    """Construct deps pointing at the configured model.

    Reads ``OBD_EVAL_AGENT_MODEL``:

    - Unset or no ``/`` in the value → local Ollama path
      (``settings.llm_endpoint``).
    - Contains ``/`` (OpenRouter convention) → OpenRouter
      via ``settings.premium_llm_base_url`` +
      ``settings.premium_llm_api_key``.

    Returns:
        ``OBDAgentDeps`` ready to pass into ``run_obd_agent``.
    """
    env_model = os.environ.get(_EVAL_MODEL_ENV_VAR, "").strip()
    is_openrouter = "/" in env_model

    if is_openrouter:
        if not settings.premium_llm_api_key:
            raise RuntimeError(
                f"{_EVAL_MODEL_ENV_VAR}={env_model!r} requested an "
                "OpenRouter agent but PREMIUM_LLM_API_KEY is empty.",
            )
        client = AsyncOpenAI(
            api_key=settings.premium_llm_api_key,
            base_url=settings.premium_llm_base_url,
            timeout=300.0,
            default_headers={
                "HTTP-Referer": "https://stf-diagnosis.dev",
                "X-Title": "STF OBD eval agent",
            },
        )
        config = OBDAgentConfig(model=env_model)
    else:
        client = AsyncOpenAI(
            api_key="ollama",
            base_url=f"{settings.llm_endpoint}/v1",
            timeout=300.0,
        )
        config = (
            OBDAgentConfig(model=env_model)
            if env_model
            else OBDAgentConfig()
        )

    return OBDAgentDeps(
        llm_client=OpenAILLMClient(client),
        tool_registry=create_obd_agent_registry(),
        config=config,
    )


def _get_default_deps() -> OBDAgentDeps:
    """Return a process-cached default deps instance."""
    global _cached_deps
    if _cached_deps is None:
        _cached_deps = _build_default_deps()
    return _cached_deps


# ── Core: invoke + adapt ─────────────────────────────────────────


async def run_obd_agent(
    inquiry: str,
    session_id: str,
    deps: Optional[OBDAgentDeps] = None,
) -> OBDAgentResult:
    """Run the OBD sub-agent for one golden entry.

    Args:
        inquiry: The investigation question.
        session_id: OBD analysis session UUID.  The OBD tools
            auto-receive this via ``_session_id`` injection in
            the agent loop.
        deps: Pre-built deps.  Tests inject fakes; production
            uses the lazy default.

    Returns:
        Fully-populated ``OBDAgentResult``.
    """
    effective_deps = deps or _get_default_deps()
    logger.info(
        "obd_agent_runner_invoked",
        inquiry_preview=inquiry[:80],
        session_id=session_id,
        model=effective_deps.config.model,
    )
    return await _run_agent_loop(
        inquiry, session_id, effective_deps,
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


async def run_obd_agent_unified(
    inquiry: str,
    session_id: str,
    deps: Optional[OBDAgentDeps] = None,
) -> SystemRunResult:
    """Run the OBD sub-agent and return a unified ``SystemRunResult``.

    Times the agent run end-to-end (wall clock) and adapts the
    result into the unified shape.  This is the public API for
    eval tests — same role as ``run_manual_agent_unified``.

    Args:
        inquiry: The investigation question.
        session_id: OBD analysis session UUID.
        deps: Optional pre-built dependency container.

    Returns:
        ``SystemRunResult`` with ``system_label="obd_agent"``,
        ready for ``grade_run``.
    """
    wall_start = time.perf_counter()
    result = await run_obd_agent(inquiry, session_id, deps)
    wall_end = time.perf_counter()
    latency_ms_wall = (wall_end - wall_start) * 1000
    return _obd_result_to_system_run(
        inquiry, result, latency_ms_wall,
    )


def _reset_cache_for_testing() -> None:
    """Test-only helper: drop the cached deps."""
    global _cached_deps
    _cached_deps = None

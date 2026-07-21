"""Delegation tool wrappers for the hybrid (Pattern 2) architecture.

Two tools the main diagnosis agent can call to delegate compound
investigations to specialist sub-agents:

- ``delegate_to_obd_agent``    — hands an inquiry to the OBD
  sub-agent (restricted 6-tool registry).
- ``delegate_to_manual_agent`` — hands an inquiry to the existing
  manual sub-agent (restricted 3-tool registry).

Each tool builds a fresh sub-agent deps object that shares the
main agent's ``LLMClient`` but uses an independent, restricted
``ToolRegistry``.  The sub-agent runs its own minimal ReAct loop
and returns a structured result, which we serialise to markdown
for the main agent to consume.

The sub-agent registries do NOT include these delegation tools —
preventing infinite recursion (a sub-agent cannot delegate to
itself or to the other sub-agent).  Verified by unit tests.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

from app.harness.deps import LLMClient
from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import (
    DelegateToManualAgentInput,
    DelegateToOBDAgentInput,
)

logger = structlog.get_logger(__name__)


# ── Shared deps resolution ───────────────────────────────────────


_LLM_CLIENT_OVERRIDE: Optional[LLMClient] = None
"""Optional override for the shared LLM client.

Set by the harness when it spins up the main loop so sub-agents
reuse the same ``OpenAILLMClient`` (and the same OpenRouter /
Ollama backend) without each delegation tool needing to construct
its own.  Falls back to a freshly built client when the override
is None — useful for tests that exercise the tools standalone.
"""


def set_shared_llm_client(client: Optional[LLMClient]) -> None:
    """Install (or clear) the shared LLM client for sub-agents.

    The main harness ``run_diagnosis_loop`` should call this on
    startup so any ``delegate_to_*`` call routes the sub-agent's
    LLM traffic through the same client + connection pool.

    Args:
        client: The shared ``LLMClient`` instance, or ``None`` to
            clear.
    """
    global _LLM_CLIENT_OVERRIDE
    _LLM_CLIENT_OVERRIDE = client


def _default_llm_client() -> LLMClient:
    """Construct a fresh ``LLMClient`` matching the harness config.

    Used as the fallback when no shared client has been installed
    (e.g. in unit tests that exercise a delegation tool with a
    mocked registry but no ``set_shared_llm_client`` call).  Reads
    config from ``app.config.settings``.

    Returns:
        A ``OpenAILLMClient`` ready for ``chat()`` calls.
    """
    # Lazy import — keeps the module loadable in environments
    # where ``openai`` isn't installed.
    from openai import AsyncOpenAI

    from app.config import settings
    from app.harness.deps import OpenAILLMClient

    raw_client = AsyncOpenAI(
        api_key=settings.premium_llm_api_key,
        base_url=settings.premium_llm_base_url,
    )
    return OpenAILLMClient(raw_client)


def _resolve_llm_client() -> LLMClient:
    """Resolve the shared client, falling back to a fresh build."""
    if _LLM_CLIENT_OVERRIDE is not None:
        return _LLM_CLIENT_OVERRIDE
    return _default_llm_client()


def _resolve_session_vehicle(
    session_id: str,
) -> Optional[str]:
    """Look up the harness-verified vehicle identity for a session.

    HARNESS-29 (#213): the manual sub-agent must receive the
    vehicle identity deterministically — the main agent's
    free-text ``inquiry`` may omit it (observed on golden
    cross-004: a vehicle-less inquiry made manual selection a
    coin flip and produced a car-manual spec for a scooter).
    Reads the make/model that APP-60 requires at upload plus the
    ``vehicle_id`` (VIN) from the ``OBDAnalysisSession`` row.

    Args:
        session_id: Session UUID string (loop-injected).

    Returns:
        ``"<Manufacturer> <Model> (VIN <vehicle_id>)"`` (VIN part
        omitted when absent/unknown), or ``None`` when the session
        cannot be resolved or has no vehicle identity — the
        sub-agent then falls back to the legacy behaviour.
    """
    # Lazy imports — keep the module loadable without the DB
    # stack (mirrors the handlers' lazy-import convention).
    import uuid as _uuid

    from app.db.session import SessionLocal
    from app.models_db import OBDAnalysisSession

    try:
        sid = _uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        return None

    try:
        db = SessionLocal()
    except Exception:  # pragma: no cover — DB not configured
        return None
    try:
        row = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == sid)
            .first()
        )
        if row is None:
            return None
        canonical = row.canonical_name
        vehicle_id = (row.vehicle_id or "").strip()
        has_vin = vehicle_id and vehicle_id.lower() not in (
            "unknown", "v-unknown",
        )
        if canonical and has_vin:
            return f"{canonical} (VIN {vehicle_id})"
        if canonical:
            return canonical
        if has_vin:
            return vehicle_id
        return None
    except Exception as exc:
        logger.warning(
            "resolve_session_vehicle_failed",
            session_id=session_id,
            error=str(exc),
        )
        return None
    finally:
        db.close()


# ── delegate_to_obd_agent ────────────────────────────────────────


async def delegate_to_obd_agent(
    input_data: Dict[str, Any],
) -> str:
    """Hand a diagnostic inquiry to the OBD sub-agent.

    Spins up a fresh OBD sub-agent with the restricted 6-tool
    registry, awaits its run, and renders the structured
    ``OBDAgentResult`` to markdown.

    Args:
        input_data: Validated ``DelegateToOBDAgentInput`` fields
            plus the loop-injected ``_session_id``.

    Returns:
        Markdown rendering of the sub-agent finding.
    """
    # Lazy imports to avoid circular references and to keep the
    # module loadable in test environments where ``app.harness``
    # isn't fully constructed.
    from app.harness_agents.obd_agent import (
        OBDAgentConfig,
        OBDAgentDeps,
        create_obd_agent_registry,
        run_obd_agent,
    )
    from app.harness_agents.result_formatters import (
        format_obd_agent_result,
    )
    from app.config import settings

    inquiry: str = input_data["inquiry"]
    session_id: str = input_data["_session_id"]

    llm_client = _resolve_llm_client()
    config = OBDAgentConfig(
        model=getattr(
            settings, "premium_llm_model", None,
        ) or "qwen3.5:27b-q8_0",
    )
    deps = OBDAgentDeps(
        llm_client=llm_client,
        tool_registry=create_obd_agent_registry(),
        config=config,
    )

    logger.info(
        "delegate_to_obd_agent_start",
        session_id=session_id,
        inquiry_preview=inquiry[:120],
    )

    result = await run_obd_agent(inquiry, session_id, deps)

    logger.info(
        "delegate_to_obd_agent_done",
        session_id=session_id,
        iterations=result.iterations,
        stopped_reason=result.stopped_reason,
        signal_citations=len(result.signal_citations),
        dtc_citations=len(result.dtc_citations),
    )

    return format_obd_agent_result(result)


# ── delegate_to_manual_agent ─────────────────────────────────────


async def delegate_to_manual_agent(
    input_data: Dict[str, Any],
) -> str:
    """Hand a manual-lookup inquiry to the manual sub-agent.

    Wraps the existing ``run_manual_agent`` infrastructure.
    Spins up a fresh manual sub-agent with the restricted 3-tool
    registry, awaits its run, and renders the structured
    ``ManualAgentResult`` to markdown.

    Args:
        input_data: Validated ``DelegateToManualAgentInput`` fields
            plus the loop-injected ``_session_id`` (used to
            resolve the harness-verified vehicle identity;
            HARNESS-29, #213).

    Returns:
        Markdown rendering of the sub-agent finding.
    """
    from app.harness_agents.manual_agent import (
        ManualAgentConfig,
        ManualAgentDeps,
        create_manual_agent_registry,
        run_manual_agent,
    )
    from app.harness_agents.result_formatters import (
        format_manual_agent_result,
    )
    from app.config import settings

    inquiry: str = input_data["inquiry"]
    obd_context: Optional[str] = input_data.get("obd_context")
    # HARNESS-29: vehicle identity is resolved from the session
    # row, NOT trusted from the main agent's free text — an
    # inquiry that omits (or mis-states) the vehicle no longer
    # degrades manual selection to a guess.
    vehicle: Optional[str] = _resolve_session_vehicle(
        input_data.get("_session_id", ""),
    )

    llm_client = _resolve_llm_client()
    config = ManualAgentConfig(
        model=getattr(
            settings, "premium_llm_model", None,
        ) or "qwen3.5:27b-q8_0",
    )
    deps = ManualAgentDeps(
        llm_client=llm_client,
        tool_registry=create_manual_agent_registry(),
        config=config,
    )

    logger.info(
        "delegate_to_manual_agent_start",
        inquiry_preview=inquiry[:120],
        has_obd_context=obd_context is not None,
        vehicle=vehicle,
    )

    result = await run_manual_agent(
        inquiry, obd_context, deps, vehicle=vehicle,
    )

    logger.info(
        "delegate_to_manual_agent_done",
        iterations=result.iterations,
        stopped_reason=result.stopped_reason,
        citations=len(result.citations),
        raw_sections=len(result.raw_sections),
    )

    return format_manual_agent_result(result)


# ── ToolDefinition exports ───────────────────────────────────────


_DELEGATE_OBD_DESC = (
    "Delegate an end-to-end OBD investigation to the OBD "
    "sub-agent. Use for compound questions that require multiple "
    "tool calls in sequence (e.g. 'investigate stored DTCs and "
    "the engine state', 'characterise the charging behaviour'). "
    "For focused single-question lookups (e.g. 'what's the RPM "
    "max?'), call the primitive tools (list_signals, "
    "get_signal_stats, etc.) directly instead — that's cheaper. "
    "Returns a structured finding with signal_citations, "
    "dtc_citations, raw data excerpts, and limitations."
)

_DELEGATE_MANUAL_DESC = (
    "Delegate an end-to-end service-manual lookup to the manual "
    "sub-agent. Use for compound questions that require "
    "navigating the manual (e.g. 'what's the diagnostic "
    "procedure for P0117 on MWS-150-A?'). Returns a structured "
    "finding with cited sections and verbatim quotes. Pass "
    "optional obd_context to help the sub-agent disambiguate."
)


DELEGATE_TO_OBD_AGENT_DEF = ToolDefinition(
    name="delegate_to_obd_agent",
    description=_DELEGATE_OBD_DESC,
    input_schema=DelegateToOBDAgentInput.model_json_schema(),
    handler=delegate_to_obd_agent,
    input_model=DelegateToOBDAgentInput,
    is_read_only=True,
    max_result_chars=80_000,
)


DELEGATE_TO_MANUAL_AGENT_DEF = ToolDefinition(
    name="delegate_to_manual_agent",
    description=_DELEGATE_MANUAL_DESC,
    input_schema=DelegateToManualAgentInput.model_json_schema(),
    handler=delegate_to_manual_agent,
    input_model=DelegateToManualAgentInput,
    is_read_only=True,
    max_result_chars=80_000,
)

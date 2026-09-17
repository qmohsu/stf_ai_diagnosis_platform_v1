"""Delegation tools: the two sub-agents mounted as tools of the main agent.

Each tool runs its sub-agent with the parent's usage object (shared
budget, FM-22) under the parent's event sink (nested events carry this
call's id, FM-20 / FM-53).  A sub-agent that hits its own budget, the
parent's budget, or a model error returns a prefixed partial finding
(``[delegation …]``) instead of an empty string or an exception
(FM-8 / FM-46).  Sub-agents never carry these tools (FM-36).

Author: Xiangzhu Yan
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from pydantic_ai import RunContext

from stf_v3.diagnosis.agent.deps import core_deps
from stf_v3.diagnosis.agent.formatters import format_manual_agent_result, format_obd_agent_result
from stf_v3.diagnosis.tools._common import execute

logger = structlog.get_logger(__name__)

_MIN_INQUIRY = 10
_MAX_INQUIRY = 500


def _partial_prefix(stopped_reason: str) -> str:
    return {
        "timeout": "[delegation timed out — partial finding]\n",
        "budget": "[delegation exceeded its budget — partial finding]\n",
        "cancelled": "[delegation cancelled — partial finding]\n",
        "error": "[delegation failed — partial finding]\n",
    }.get(stopped_reason, "")


async def delegate_to_obd_agent(ctx: RunContext[Any], inquiry: str) -> str:
    """Delegate an end-to-end OBD investigation to the OBD sub-agent.

    Use for compound questions that require multiple tool calls in
    sequence (e.g. 'investigate stored DTCs and the engine state',
    'characterise the charging behaviour'). For focused single-question
    lookups (e.g. 'what's the RPM max?'), call the primitive tools
    (list_signals, get_signal_stats, etc.) directly instead — that's
    cheaper. Returns a structured finding with signal_citations,
    dtc_citations, raw data excerpts, and limitations.

    Args:
        inquiry: The investigation question to pose. Examples: 'Investigate
            stored DTCs and tell me what's normal vs. abnormal.', 'What does
            the charging behaviour look like across the trip?', 'Are there
            any thermal anomalies in the coolant or cylinder head
            temperature?'.
    """
    return await execute(ctx, "delegate_to_obd_agent", {"inquiry": inquiry}, lambda: _delegate_obd(ctx, inquiry))


async def _delegate_obd(ctx: RunContext[Any], inquiry: str) -> str:
    from stf_v3.diagnosis.agent.obd_agent import run_obd_agent

    problem = _validate_inquiry(inquiry)
    if problem:
        return problem
    core = core_deps(ctx.deps)
    result = await run_obd_agent(core, ctx.model, inquiry, ctx.tool_call_id, ctx.usage)
    return _partial_prefix(result.stopped_reason) + format_obd_agent_result(result)


async def delegate_to_manual_agent(ctx: RunContext[Any], inquiry: str, obd_context: Optional[str] = None) -> str:
    """Delegate an end-to-end service-manual lookup to the manual sub-agent.

    Use for compound questions that require navigating the manual (e.g.
    'what's the diagnostic procedure for P0117 on MWS-150-A?'). Returns a
    structured finding with cited sections and verbatim quotes. Pass
    optional obd_context to help the sub-agent disambiguate.

    Args:
        inquiry: The lookup question to pose. Examples: 'What is the
            diagnostic procedure for DTC P0117 on MWS-150-A?', 'What is the
            spark plug torque specification?', 'How do I test the coolant
            temperature sensor circuit?'.
        obd_context: Optional OBD findings context to help the manual agent
            disambiguate (e.g. observed DTCs, key signal anomalies).
    """
    return await execute(ctx, "delegate_to_manual_agent", {"inquiry": inquiry, "obd_context": obd_context},
                         lambda: _delegate_manual(ctx, inquiry, obd_context))


async def _delegate_manual(ctx: RunContext[Any], inquiry: str, obd_context: Optional[str]) -> str:
    from stf_v3.diagnosis.agent.manual_agent import run_manual_agent

    problem = _validate_inquiry(inquiry)
    if problem:
        return problem
    core = core_deps(ctx.deps)
    if obd_context and len(obd_context) > 2000:
        obd_context = obd_context[:2000]
    result = await run_manual_agent(core, ctx.model, inquiry, obd_context, ctx.tool_call_id, ctx.usage)
    return _partial_prefix(result.stopped_reason) + format_manual_agent_result(result)


def _validate_inquiry(inquiry: str) -> Optional[str]:
    text = (inquiry or "").strip()
    if len(text) < _MIN_INQUIRY:
        return f"Validation error: `inquiry` must be at least {_MIN_INQUIRY} characters."
    if len(text) > _MAX_INQUIRY:
        return f"Validation error: `inquiry` must be at most {_MAX_INQUIRY} characters."
    return None


DELEGATION_TOOLS = [delegate_to_obd_agent, delegate_to_manual_agent]

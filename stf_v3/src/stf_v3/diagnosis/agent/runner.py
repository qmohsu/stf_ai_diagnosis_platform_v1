"""Shared run driver: iterate a Pydantic AI agent, emit events, enforce budgets.

Used by the main agent and both sub-agents so every run has the same
shape: ``agent.iter`` node by node (works with real models AND the
non-streaming test models), events derived from each node, four gates
(wall clock, requests, tool calls, total tokens) that end the run with a
``stopped_reason`` instead of an exception (FM-7 / FM-17 / FM-46 /
FM-47), and the message history kept even when a gate trips so a
partial report can be built.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

import structlog
from pydantic_ai import (
    Agent,
    RunCancelled,
    RunContext,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import AgentRunError, ModelAPIError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.agent.context import CompactionRecord, make_history_processor
from stf_v3.diagnosis.agent.deps import core_deps
from stf_v3.diagnosis.agent.events import EventSink

logger = structlog.get_logger(__name__)

_MAX_ERROR_CHARS = 300
_SECRET_MARKERS = ("bearer ", "api_key", "authorization", "sk-")


def redact_error(exc: BaseException) -> str:
    """Exception type + message with anything that looks like a secret removed (FM-27)."""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    for marker in _SECRET_MARKERS:
        idx = lowered.find(marker)
        if idx >= 0:
            text = text[:idx] + "[redacted]"
            lowered = text.lower()
    return text[:_MAX_ERROR_CHARS]


@dataclass
class RunOutcome:
    """Result of one driven run (agent or sub-agent)."""

    output: Any                    # str, or the output-tool model (manual final_answer)
    messages: List[ModelMessage]
    usage: RunUsage
    stopped_reason: str            # complete | timeout | budget | cancelled | error
    error: Optional[str] = None
    requests: int = 0
    elapsed_s: float = 0.0
    tool_calls: int = 0
    events_emitted: int = 0
    limitations: List[str] = field(default_factory=list)
    # Which gate stopped the run: (kind, limit) with kind wall_clock /
    # request / tool_calls / total_tokens; the report words it per locale.
    gate: Optional[Tuple[str, int]] = None


_LIMIT_RE = re.compile(r"the (request|tool_calls|total_tokens|input_tokens|output_tokens)_limit of (\d+)")


def usage_gate(exc: BaseException) -> Optional[Tuple[str, int]]:
    """``(kind, limit)`` from a ``UsageLimitExceeded`` message (None if unknown)."""
    m = _LIMIT_RE.search(str(exc))
    return (m.group(1), int(m.group(2))) if m else None


def _emit_response(response: ModelResponse, sink: EventSink, parent: Optional[str]) -> None:
    for part in response.parts:
        if isinstance(part, ThinkingPart):
            if part.content:
                sink.emit(ev.REASONING, {"text": part.content}, parent)
        elif isinstance(part, TextPart):
            if part.content:
                sink.emit(ev.TOKEN, {"text": part.content}, parent)
        elif isinstance(part, ToolCallPart):
            sink.emit(ev.TOOL_CALL, {
                "tool": part.tool_name,
                "tool_call_id": part.tool_call_id,
                "args": part.args_as_dict() if part.args is not None else {},
            }, parent)


def _emit_request(request: ModelRequest, sink: EventSink, parent: Optional[str], durations: dict) -> None:
    for part in request.parts:
        if isinstance(part, ToolReturnPart):
            content = part.content
            chars = len(content) if isinstance(content, str) else len(str(content))
            sink.emit(ev.TOOL_RESULT, {
                "tool": part.tool_name,
                "tool_call_id": part.tool_call_id,
                "chars": chars,
                "duration_ms": round(durations.get(part.tool_call_id, 0.0), 1),
                "is_error": False,
                "repeated": isinstance(content, str) and content.startswith("[repeated call"),
            }, parent)
        elif isinstance(part, RetryPromptPart) and part.tool_call_id:
            text = part.content if isinstance(part.content, str) else str(part.content)
            sink.emit(ev.TOOL_RESULT, {
                "tool": part.tool_name,
                "tool_call_id": part.tool_call_id,
                "chars": len(text),
                "duration_ms": round(durations.get(part.tool_call_id, 0.0), 1),
                "is_error": True,
            }, parent)


def _cancel_processor(ctx: RunContext[Any], messages: List[ModelMessage]) -> List[ModelMessage]:
    check = getattr(core_deps(ctx.deps), "cancel_check", None)
    if check is not None and check():
        ctx.cancel()
    return messages


async def drive(
    agent: Agent[Any, str],
    prompt: str,
    *,
    model: Model,
    deps: Any,
    sink: EventSink,
    parent_tool_call_id: Optional[str],
    usage_limits: UsageLimits,
    model_settings: ModelSettings,
    wall_clock_s: float,
    usage: Optional[RunUsage] = None,
    message_history: Optional[Sequence[ModelMessage]] = None,
    compact_threshold: Optional[int] = None,
    extra_capabilities: Sequence[Any] = (),
) -> RunOutcome:
    """Run ``agent`` to completion or to the first gate; never raises for gates.

    Args:
        agent: The (model-less) agent definition.
        prompt: Opening user message.
        model: Model object (single source, ``model.build_model``).
        deps: ``DiagDeps`` or ``SubAgentDeps``.
        sink: Event sink; nested runs share the parent's sink.
        parent_tool_call_id: Delegation call id for nested runs.
        usage_limits: Request / tool-call / token gates.
        model_settings: max tokens, temperature, timeout.
        wall_clock_s: Whole-run wall-clock gate.
        usage: Parent usage to accumulate into (sub-agents).
        message_history: Prior messages (resumed conversations).
        compact_threshold: Estimated-token trigger for compaction.
        extra_capabilities: More run-time capabilities (e.g. force-final).
    """
    core = core_deps(deps)
    started = time.monotonic()
    events_before = len(sink.events)
    messages: List[ModelMessage] = list(message_history or [])
    output: Any = None
    stopped = "complete"
    error: Optional[str] = None
    run_usage = usage if usage is not None else RunUsage()
    run_ref: Any = None
    limitations: List[str] = []
    gate: Optional[Tuple[str, int]] = None

    def _on_compact(record: CompactionRecord) -> None:
        sink.emit(ev.CONTEXT_COMPACT, {
            "before_tokens": record.before_tokens, "after_tokens": record.after_tokens,
            "compacted_pairs": record.compacted_pairs, "kept_pairs": record.kept_pairs,
        }, parent_tool_call_id)

    capabilities: List[Any] = [ProcessHistory(_cancel_processor)]
    if compact_threshold:
        capabilities.append(ProcessHistory(make_history_processor(compact_threshold, _on_compact)))
    capabilities.extend(extra_capabilities)

    try:
        async with asyncio.timeout(wall_clock_s):
            async with agent.iter(
                prompt, model=model, deps=deps, usage=run_usage, usage_limits=usage_limits,
                model_settings=model_settings, message_history=message_history,
                capabilities=capabilities,
            ) as run:
                run_ref = run
                async for node in run:
                    if Agent.is_call_tools_node(node):
                        _emit_response(node.model_response, sink, parent_tool_call_id)
                    elif Agent.is_model_request_node(node):
                        _emit_request(node.request, sink, parent_tool_call_id, core.durations)
                result = run.result
                if result is not None:
                    output = result.output
                    messages = result.all_messages()
                    run_usage = result.usage
    except TimeoutError:
        stopped = "timeout"
        gate = ("wall_clock", int(wall_clock_s))
        limitations.append(f"run exceeded its wall-clock budget ({wall_clock_s:.0f} s)")
    except UsageLimitExceeded as exc:
        stopped = "budget"
        error = redact_error(exc)
        gate = usage_gate(exc)
        # Never the raw library message (it carries a docs URL, PROD-11 finding).
        limitations.append("run exceeded a usage budget"
                           + (f" ({gate[0]} limit {gate[1]:,})" if gate else ""))
    except RunCancelled:
        stopped = "cancelled"
        limitations.append("run was cancelled")
    except (UnexpectedModelBehavior, ModelAPIError, AgentRunError) as exc:
        stopped = "error"
        error = redact_error(exc)
        limitations.append(f"model/runtime error: {error}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — never let a run die without an outcome
        stopped = "error"
        error = redact_error(exc)
        limitations.append(f"unexpected error: {error}")
        logger.exception("agent.run_failed", parent_tool_call_id=parent_tool_call_id)
    if stopped != "complete" and run_ref is not None:
        try:
            messages = list(run_ref.ctx.state.message_history)
            run_usage = run_ref.ctx.state.usage
        except Exception:  # noqa: BLE001
            pass
    elapsed = time.monotonic() - started
    logger.info(
        "agent.run_done", stopped_reason=stopped, elapsed_s=round(elapsed, 1),
        requests=run_usage.requests, tool_calls=run_usage.tool_calls,
        total_tokens=run_usage.total_tokens, parent_tool_call_id=parent_tool_call_id, error=error,
    )
    return RunOutcome(
        output=output, messages=messages, usage=run_usage, stopped_reason=stopped, error=error,
        requests=run_usage.requests, elapsed_s=elapsed, tool_calls=run_usage.tool_calls,
        events_emitted=len(sink.events) - events_before, limitations=limitations, gate=gate,
    )


def make_limits(request_limit: int, tool_calls_limit: Optional[int] = None,
                total_tokens_limit: Optional[int] = None) -> UsageLimits:
    """UsageLimits from settings-style ints (0 / None disables a gate)."""
    return UsageLimits(
        request_limit=request_limit or None,
        tool_calls_limit=tool_calls_limit or None,
        total_tokens_limit=total_tokens_limit or None,
    )


__all__ = ["RunOutcome", "drive", "make_limits", "redact_error", "usage_gate", "Callable"]

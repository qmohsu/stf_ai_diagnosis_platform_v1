"""Core agent loop implementing the ReAct diagnosis cycle.

The loop is an async generator that yields ``HarnessEvent`` objects
consumable by SSE streaming.  It calls the LLM with tool schemas,
dispatches tool calls through ``ToolRegistry.execute()``, appends
results to the conversation, and iterates until the LLM produces a
final diagnosis or the iteration / timeout budget is exhausted.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import structlog

from app.harness.deps import (
    HarnessDeps,
    HarnessEvent,
    LLMResponse,
    ToolCallInfo,
)
from app.harness.harness_prompts import (
    build_system_prompt,
    build_user_message,
)

logger = structlog.get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _build_initial_messages(
    session_id: str,
    parsed_summary: Dict[str, Any],
    tool_names: List[str],
) -> List[Dict[str, Any]]:
    """Assemble the opening system + user messages.

    Args:
        session_id: OBD session UUID string.
        parsed_summary: Session's ``parsed_summary_payload``.
        tool_names: Sorted list of registered tool names.

    Returns:
        Two-element message list (system, user).
    """
    return [
        {
            "role": "system",
            "content": build_system_prompt(tool_names),
        },
        {
            "role": "user",
            "content": build_user_message(
                session_id, parsed_summary,
            ),
        },
    ]


def _parse_tool_arguments(
    raw: str,
) -> Dict[str, Any]:
    """Safely parse a JSON arguments string.

    Args:
        raw: JSON string from ``ToolCallInfo.arguments``.

    Returns:
        Parsed dict, or a dict with ``"_parse_error"`` key on
        failure so the registry can return a validation error.

    Raises:
        Never — all parse errors are caught and wrapped.
    """
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"_parse_error": f"Expected object, got {type(parsed).__name__}"}
    except (json.JSONDecodeError, TypeError) as exc:
        return {"_parse_error": str(exc)}


def _make_assistant_message(
    response: LLMResponse,
) -> Dict[str, Any]:
    """Build the assistant message to append to history.

    Args:
        response: The LLM response containing tool calls.

    Returns:
        OpenAI-format assistant message dict.
    """
    msg: Dict[str, Any] = {
        "role": "assistant",
        "content": response.content,
    }
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                },
            }
            for tc in response.tool_calls
        ]
    return msg


def _make_tool_message(
    tool_call_id: str,
    output: str,
) -> Dict[str, Any]:
    """Build a tool-result message for the conversation.

    Args:
        tool_call_id: Correlating ID from the tool call.
        output: Tool execution output string.

    Returns:
        OpenAI-format tool message dict.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": output,
    }


def _extract_diagnosis(content: Optional[str]) -> str:
    """Extract diagnosis text from the LLM's final response.

    Args:
        content: The ``content`` field of the LLM response.

    Returns:
        Diagnosis text, or a fallback string if empty.
    """
    if content and content.strip():
        return content.strip()
    return (
        "The agent completed its investigation but did not "
        "produce a final diagnosis text."
    )


def _extract_partial_diagnosis(
    messages: List[Dict[str, Any]],
) -> str:
    """Scan conversation history for the best diagnosis attempt.

    Looks for the last assistant message with non-empty content
    (which may be an intermediate reasoning step).

    Args:
        messages: Full conversation history.

    Returns:
        Best available diagnosis text.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if content and content.strip():
                return content.strip()
    return (
        "Max iterations reached. The agent could not produce "
        "a diagnosis within the allowed number of steps."
    )


# ── Core loop ────────────────────────────────────────────────────────


async def run_diagnosis_loop(
    session_id: uuid.UUID,
    parsed_summary: Dict[str, Any],
    deps: HarnessDeps,
) -> AsyncIterator[HarnessEvent]:
    """Core agent loop for diagnosis investigation.

    Implements the ReAct cycle: call LLM → dispatch tool calls →
    append results → iterate until the LLM stops or the budget
    is exhausted.

    Args:
        session_id: OBD analysis session to diagnose.
        parsed_summary: Pre-computed summary from V1 pipeline.
        deps: Injected dependencies (LLM client, tools, config).

    Yields:
        ``HarnessEvent`` with ``event_type`` and ``payload``.
    """
    sid = str(session_id)
    cfg = deps.config
    tool_schemas = deps.tool_registry.schemas
    tool_names = deps.tool_registry.tool_names

    messages = _build_initial_messages(
        sid, parsed_summary, tool_names,
    )

    iteration = 0
    total_tool_calls: List[str] = []

    logger.info(
        "harness_loop_start",
        session_id=sid,
        model=cfg.model,
        max_iterations=cfg.max_iterations,
    )

    try:
        async with asyncio.timeout(cfg.timeout_seconds):
            while iteration < cfg.max_iterations:
                # ── 1. Call LLM ──────────────────────────────
                try:
                    response = await deps.llm_client.chat(
                        messages=messages,
                        tools=tool_schemas,
                        model=cfg.model,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                    )
                except Exception as exc:
                    logger.error(
                        "harness_llm_error",
                        session_id=sid,
                        iteration=iteration,
                        error=str(exc),
                    )
                    yield HarnessEvent(
                        "error",
                        {
                            "error_type": "llm_error",
                            "message": str(exc),
                            "iteration": iteration,
                        },
                    )
                    partial = _extract_partial_diagnosis(
                        messages,
                    )
                    yield HarnessEvent(
                        "done",
                        {
                            "diagnosis": partial,
                            "partial": True,
                            "iterations": iteration,
                            "tools_called": total_tool_calls,
                        },
                    )
                    return

                # ── 2. Check if LLM wants to stop ───────────
                if (
                    response.finish_reason == "stop"
                    or not response.tool_calls
                ):
                    diagnosis = _extract_diagnosis(
                        response.content,
                    )
                    logger.info(
                        "harness_loop_done",
                        session_id=sid,
                        iterations=iteration + 1,
                        tools_called=total_tool_calls,
                    )
                    yield HarnessEvent(
                        "done",
                        {
                            "diagnosis": diagnosis,
                            "partial": False,
                            "iterations": iteration + 1,
                            "tools_called": total_tool_calls,
                        },
                    )
                    return

                # ── 3. Execute tool calls ────────────────────
                messages.append(
                    _make_assistant_message(response),
                )

                for tc in response.tool_calls:
                    args = _parse_tool_arguments(
                        tc.arguments,
                    )

                    yield HarnessEvent(
                        "tool_call",
                        {
                            "name": tc.name,
                            "input": args,
                            "iteration": iteration,
                            "tool_call_id": tc.id,
                        },
                    )

                    result = (
                        await deps.tool_registry.execute(
                            tc.name, args,
                        )
                    )
                    total_tool_calls.append(tc.name)

                    yield HarnessEvent(
                        "tool_result",
                        {
                            "name": tc.name,
                            "output": result.output,
                            "duration_ms": result.duration_ms,
                            "is_error": result.is_error,
                            "iteration": iteration,
                        },
                    )

                    messages.append(
                        _make_tool_message(
                            tc.id, result.output,
                        ),
                    )

                iteration += 1

    except TimeoutError:
        logger.warning(
            "harness_loop_timeout",
            session_id=sid,
            iteration=iteration,
            timeout_seconds=cfg.timeout_seconds,
        )
        yield HarnessEvent(
            "error",
            {
                "error_type": "timeout",
                "message": (
                    f"Agent loop timed out after "
                    f"{cfg.timeout_seconds}s"
                ),
                "iteration": iteration,
            },
        )
        partial = _extract_partial_diagnosis(messages)
        yield HarnessEvent(
            "done",
            {
                "diagnosis": partial,
                "partial": True,
                "iterations": iteration,
                "tools_called": total_tool_calls,
            },
        )
        return

    # ── Max iterations reached ───────────────────────────────────
    logger.warning(
        "harness_loop_max_iterations",
        session_id=sid,
        max_iterations=cfg.max_iterations,
    )
    partial = _extract_partial_diagnosis(messages)
    yield HarnessEvent(
        "done",
        {
            "diagnosis": partial,
            "partial": True,
            "iterations": cfg.max_iterations,
            "tools_called": total_tool_calls,
        },
    )

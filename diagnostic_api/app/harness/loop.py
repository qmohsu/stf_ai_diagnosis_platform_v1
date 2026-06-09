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
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import structlog

from app.harness.context import (
    maybe_compact,
    truncate_tool_result,
)
from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    HarnessEvent,
    LLMResponse,
    LLMStreamChunk,
    ToolCallInfo,
)
from app.harness.harness_prompts import (
    build_system_prompt,
    build_user_message,
)
from app.harness.session_log import emit_event

logger = structlog.get_logger(__name__)

_MAX_ERROR_LEN = 200  # Cap for user-facing error messages.


def _sanitize_llm_error(exc: Exception) -> str:
    """Build a safe error string from an LLM exception.

    Extracts only the exception class name and a truncated
    message.  API keys, internal URLs, and stack traces are
    logged internally but never sent to the SSE stream.

    Args:
        exc: The caught exception.

    Returns:
        Sanitised error string (max ``_MAX_ERROR_LEN`` chars).
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc)
    if len(exc_msg) > _MAX_ERROR_LEN:
        exc_msg = exc_msg[:_MAX_ERROR_LEN] + "..."
    return f"LLM call failed ({exc_name}: {exc_msg})"


# ── Helpers ──────────────────────────────────────────────────────────


def _build_initial_messages(
    session_id: str,
    parsed_summary: Dict[str, Any],
    tool_names: List[str],
    locale: str = "en",
) -> List[Dict[str, Any]]:
    """Assemble the opening system + user messages.

    Args:
        session_id: OBD session UUID string.
        parsed_summary: Session's ``parsed_summary_payload``.
        tool_names: Sorted list of registered tool names.
        locale: Response language code.

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
                session_id, parsed_summary, locale,
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
    output: Any,
) -> Dict[str, Any]:
    """Build a tool-result message for the conversation.

    Supports both plain string and multimodal content-block list
    outputs.  When ``output`` is a list, it is passed directly as
    the ``content`` field (OpenAI multimodal format).

    Args:
        tool_call_id: Correlating ID from the tool call.
        output: Tool execution output (``str`` or
            ``List[ContentBlock]``).

    Returns:
        OpenAI-format tool message dict.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": output,
    }


def _extract_text_for_sse(output: Any) -> str:
    """Extract text-only summary from a tool output for SSE events.

    SSE payloads should not contain base64-encoded images.  For
    multimodal list outputs, concatenate only text blocks and
    replace images with ``[image]`` markers.

    Args:
        output: Tool output (``str``, ``List[ContentBlock]``,
            or any fallback type).

    Returns:
        Text-only string suitable for SSE serialization.
    """
    if isinstance(output, str):
        return output
    if not isinstance(output, list):
        return str(output)
    parts: List[str] = []
    for block in output:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "image_url":
            parts.append("[image]")
    return "\n".join(parts)


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


async def _stream_llm_turn(
    deps: HarnessDeps,
    messages: List[Dict[str, Any]],
    tool_schemas: List[Dict[str, Any]],
    cfg: HarnessConfig,
    iteration: int,
) -> AsyncIterator[Union[HarnessEvent, LLMResponse]]:
    """Run one streaming LLM turn, yielding live token events.

    Yields ``HarnessEvent`` objects (``reasoning`` for thinking
    tokens, ``token`` for answer tokens) as they stream, then
    yields the terminal ``LLMResponse``.  If the streaming call
    fails (e.g. a backend that won't stream with tools), falls
    back to a single blocking ``chat()`` and yields its
    ``LLMResponse`` — so a streaming quirk degrades gracefully to
    the prior behaviour instead of failing the diagnosis.

    Args:
        deps: Injected dependencies (LLM client, config).
        messages: Conversation history.
        tool_schemas: Tool schemas in OpenAI function-calling form.
        cfg: Harness configuration.
        iteration: Current iteration index (for event payloads).

    Yields:
        ``HarnessEvent`` token deltas, then a final ``LLMResponse``.
    """
    response: Optional[LLMResponse] = None
    try:
        async for item in deps.llm_client.chat_stream(
            messages=messages,
            tools=tool_schemas,
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        ):
            if isinstance(item, LLMResponse):
                response = item
            elif isinstance(item, LLMStreamChunk):
                if item.kind == "reasoning":
                    yield HarnessEvent(
                        "reasoning",
                        {
                            "text": item.text,
                            "iteration": iteration,
                        },
                    )
                else:  # content → streamed answer tokens
                    yield HarnessEvent(
                        "token",
                        {
                            "text": item.text,
                            "iteration": iteration,
                        },
                    )
    except Exception as exc:
        logger.warning(
            "harness_stream_fallback",
            iteration=iteration,
            error=str(exc),
        )
        response = None

    if response is not None:
        yield response
        return

    # Streaming failed or produced no terminal response — fall
    # back to a single blocking call (may raise; the caller's
    # handler converts that into the partial-diagnosis path).
    yield await deps.llm_client.chat(
        messages=messages,
        tools=tool_schemas,
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )


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
        sid, parsed_summary, tool_names, cfg.locale,
    )

    iteration = 0
    total_tool_calls: List[str] = []

    logger.info(
        "harness_loop_start",
        session_id=sid,
        model=cfg.model,
        max_iterations=cfg.max_iterations,
    )

    start_payload = {
        "session_id": sid,
        "model": cfg.model,
        "max_iterations": cfg.max_iterations,
    }
    await emit_event(
        session_id, "session_start", start_payload,
    )
    yield HarnessEvent("session_start", start_payload)

    try:
        async with asyncio.timeout(cfg.timeout_seconds):
            while iteration < cfg.max_iterations:
                # ── 1. Call LLM (streaming) ──────────────────
                response: Optional[LLMResponse] = None
                try:
                    async for item in _stream_llm_turn(
                        deps, messages, tool_schemas, cfg,
                        iteration,
                    ):
                        if isinstance(item, LLMResponse):
                            response = item
                        else:
                            yield item
                    if response is None:
                        # Defensive: helper yielded no terminal
                        # response — fall back to a blocking call.
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
                        exc_info=exc,
                    )
                    safe_msg = _sanitize_llm_error(exc)
                    err_payload = {
                        "error_type": "llm_error",
                        "message": safe_msg,
                        "iteration": iteration,
                    }
                    await emit_event(
                        session_id, "error",
                        err_payload, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "error", err_payload,
                    )
                    partial_text = (
                        _extract_partial_diagnosis(messages)
                    )
                    done_payload = {
                        "diagnosis": partial_text,
                        "partial": True,
                        "iterations": iteration,
                        "tools_called": total_tool_calls,
                    }
                    await emit_event(
                        session_id, "diagnosis_done",
                        done_payload, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "done", done_payload,
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
                    done_payload = {
                        "diagnosis": diagnosis,
                        "partial": False,
                        "iterations": iteration + 1,
                        "tools_called": total_tool_calls,
                    }
                    await emit_event(
                        session_id, "diagnosis_done",
                        done_payload, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "done", done_payload,
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

                    tc_payload = {
                        "name": tc.name,
                        "input": args,
                        "iteration": iteration,
                        "tool_call_id": tc.id,
                    }
                    await emit_event(
                        session_id, "tool_call",
                        tc_payload, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "tool_call", tc_payload,
                    )

                    # Short-circuit on parse errors before
                    # injecting session context.
                    if "_parse_error" in args:
                        from app.harness.tool_registry import (
                            ToolResult,
                        )
                        result = ToolResult(
                            output=(
                                "Error: could not parse "
                                "tool arguments — "
                                + args["_parse_error"]
                            ),
                            duration_ms=0.0,
                            is_error=True,
                        )
                    else:
                        # Inject session_id so tools can
                        # access the session without the
                        # LLM passing it.
                        args["_session_id"] = sid
                        result = (
                            await deps.tool_registry.execute(
                                tc.name, args,
                            )
                        )
                    total_tool_calls.append(tc.name)

                    # Tier 1: truncate oversized tool results.
                    output = truncate_tool_result(
                        result.output,
                        cfg.max_tool_result_tokens,
                    )
                    if output is not result.output:
                        logger.info(
                            "tool_result_truncated",
                            tool=tc.name,
                            iteration=iteration,
                        )

                    # SSE payload uses text-only summary
                    # (no base64 images in event stream).
                    sse_output = _extract_text_for_sse(output)

                    tr_payload = {
                        "name": tc.name,
                        "output": sse_output,
                        "duration_ms": result.duration_ms,
                        "is_error": result.is_error,
                        "iteration": iteration,
                    }
                    await emit_event(
                        session_id, "tool_result",
                        tr_payload, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "tool_result", tr_payload,
                    )

                    messages.append(
                        _make_tool_message(
                            tc.id, output,
                        ),
                    )

                # Tier 2: auto-compact if approaching limit.
                messages, compact_info = maybe_compact(
                    messages, cfg.compact_threshold,
                )
                if compact_info:
                    await emit_event(
                        session_id, "context_compact",
                        compact_info, iteration=iteration,
                    )
                    yield HarnessEvent(
                        "context_compact", compact_info,
                    )

                iteration += 1

    except TimeoutError:
        logger.warning(
            "harness_loop_timeout",
            session_id=sid,
            iteration=iteration,
            timeout_seconds=cfg.timeout_seconds,
        )
        err_payload = {
            "error_type": "timeout",
            "message": (
                f"Agent loop timed out after "
                f"{cfg.timeout_seconds}s"
            ),
            "iteration": iteration,
        }
        await emit_event(
            session_id, "error",
            err_payload, iteration=iteration,
        )
        yield HarnessEvent("error", err_payload)
        partial_text = _extract_partial_diagnosis(messages)
        done_payload = {
            "diagnosis": partial_text,
            "partial": True,
            "iterations": iteration,
            "tools_called": total_tool_calls,
        }
        await emit_event(
            session_id, "diagnosis_done",
            done_payload, iteration=iteration,
        )
        yield HarnessEvent("done", done_payload)
        return

    # ── Max iterations reached ───────────────────────────────────
    logger.warning(
        "harness_loop_max_iterations",
        session_id=sid,
        max_iterations=cfg.max_iterations,
    )
    partial_text = _extract_partial_diagnosis(messages)
    done_payload = {
        "diagnosis": partial_text,
        "partial": True,
        "iterations": cfg.max_iterations,
        "tools_called": total_tool_calls,
    }
    await emit_event(
        session_id, "diagnosis_done",
        done_payload, iteration=cfg.max_iterations,
    )
    yield HarnessEvent("done", done_payload)

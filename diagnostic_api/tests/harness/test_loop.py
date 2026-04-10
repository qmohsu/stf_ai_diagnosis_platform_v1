"""Tests for the core agent loop (``app.harness.loop``).

Uses a ``MockLLMClient`` that replays pre-recorded responses so
every test is deterministic — no real LLM calls.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    HarnessEvent,
    LLMResponse,
    ToolCallInfo,
)
from app.harness.loop import (
    _build_initial_messages,
    _extract_diagnosis,
    _extract_partial_diagnosis,
    _parse_tool_arguments,
    _sanitize_llm_error,
    run_diagnosis_loop,
)
from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


# ── Fixtures ─────────────────────────────────────────────────────────


FAKE_SESSION_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

FAKE_PARSED_SUMMARY: Dict[str, Any] = {
    "vehicle_id": "V12345",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0300 (Random/Multiple Cylinder Misfire)",
    "pid_summary": "RPM: 780-4200, COOLANT_TEMP: 89-95",
    "anomaly_events": "RPM range_shift at 08:32",
    "diagnostic_clues": "STAT_001 Engine misfire pattern",
}


# ── Mock LLM Client ─────────────────────────────────────────────────


class MockLLMClient:
    """LLM client that replays a sequence of pre-recorded responses.

    Attributes:
        responses: Ordered list of ``LLMResponse`` to return.
        calls: Recorded call kwargs for assertions.
    """

    def __init__(
        self, responses: List[LLMResponse],
    ) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: List[Dict[str, Any]] = []

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Return the next pre-recorded response."""
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._index >= len(self._responses):
            raise RuntimeError(
                "MockLLMClient exhausted all responses"
            )
        resp = self._responses[self._index]
        self._index += 1
        return resp


class SlowMockLLMClient:
    """LLM client that sleeps to trigger timeout."""

    async def chat(self, **kwargs: Any) -> LLMResponse:
        """Sleep indefinitely until cancelled."""
        await asyncio.sleep(999)
        raise RuntimeError("unreachable")


class ErrorMockLLMClient:
    """LLM client that raises on the first call."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def chat(self, **kwargs: Any) -> LLMResponse:
        """Raise the configured error."""
        raise self._error


# ── Helper: build a registry with simple echo tools ──────────────────


def _echo_tool(name: str) -> ToolDefinition:
    """Create a simple tool that echoes its input as a string."""

    async def handler(
        input_data: Dict[str, Any],
    ) -> str:
        return f"{name} result: {input_data}"

    return ToolDefinition(
        name=name,
        description=f"Echo tool: {name}",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
        handler=handler,
    )


def _make_registry(
    *tool_names: str,
) -> ToolRegistry:
    """Build a registry with echo tools for testing."""
    registry = ToolRegistry()
    for name in tool_names:
        registry.register(_echo_tool(name))
    return registry


def _make_deps(
    client: Any,
    registry: ToolRegistry | None = None,
    **config_overrides: Any,
) -> HarnessDeps:
    """Build HarnessDeps with sensible test defaults."""
    if registry is None:
        registry = _make_registry(
            "get_session_context",
            "detect_anomalies",
            "search_manual",
        )
    config_kwargs: Dict[str, Any] = {
        "model": "test/mock-model",
        "max_iterations": 10,
        "timeout_seconds": 30.0,
    }
    config_kwargs.update(config_overrides)
    return HarnessDeps(
        llm_client=client,
        tool_registry=registry,
        config=HarnessConfig(**config_kwargs),
    )


async def _collect_events(
    gen: AsyncIterator[HarnessEvent],
) -> List[HarnessEvent]:
    """Drain an async generator into a list."""
    events: List[HarnessEvent] = []
    async for event in gen:
        events.append(event)
    return events


# ── Helper responses ─────────────────────────────────────────────────


def _tool_call_response(
    *calls: tuple[str, str, str],
) -> LLMResponse:
    """Build an LLMResponse with tool calls.

    Args:
        calls: Tuples of (id, name, arguments_json).
    """
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallInfo(id=c[0], name=c[1], arguments=c[2])
            for c in calls
        ],
        finish_reason="tool_calls",
    )


def _stop_response(content: str) -> LLMResponse:
    """Build an LLMResponse that ends the loop."""
    return LLMResponse(
        content=content,
        tool_calls=[],
        finish_reason="stop",
    )


# ── Tests: helper functions ──────────────────────────────────────────


class TestHelpers:
    """Tests for loop helper functions."""

    def test_parse_tool_arguments_valid(self) -> None:
        """Valid JSON object is parsed correctly."""
        result = _parse_tool_arguments(
            '{"session_id": "abc"}'
        )
        assert result == {"session_id": "abc"}

    def test_parse_tool_arguments_invalid_json(self) -> None:
        """Invalid JSON returns a dict with _parse_error."""
        result = _parse_tool_arguments("not json")
        assert "_parse_error" in result

    def test_parse_tool_arguments_non_object(self) -> None:
        """Valid JSON that is not an object returns error."""
        result = _parse_tool_arguments("[1, 2, 3]")
        assert "_parse_error" in result

    def test_extract_diagnosis_with_content(self) -> None:
        """Non-empty content is returned stripped."""
        assert _extract_diagnosis("  Diagnosis.  ") == (
            "Diagnosis."
        )

    def test_extract_diagnosis_empty(self) -> None:
        """Empty content returns fallback string."""
        result = _extract_diagnosis(None)
        assert "did not produce" in result

    def test_extract_partial_from_history(self) -> None:
        """Partial extraction finds last assistant content."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {
                "role": "assistant",
                "content": "first thought",
            },
            {"role": "tool", "content": "tool output"},
            {
                "role": "assistant",
                "content": "refined thought",
            },
        ]
        assert _extract_partial_diagnosis(messages) == (
            "refined thought"
        )

    def test_extract_partial_no_assistant(self) -> None:
        """No assistant messages returns fallback."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]
        result = _extract_partial_diagnosis(messages)
        assert "Max iterations" in result

    def test_build_initial_messages(self) -> None:
        """Initial messages contain system and user roles."""
        msgs = _build_initial_messages(
            str(FAKE_SESSION_ID),
            FAKE_PARSED_SUMMARY,
            ["detect_anomalies", "search_manual"],
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "V12345" in msgs[1]["content"]
        assert str(FAKE_SESSION_ID) in msgs[1]["content"]

    def test_sanitize_llm_error_short(self) -> None:
        """Short error messages are preserved with class name."""
        result = _sanitize_llm_error(
            ValueError("bad input"),
        )
        assert "ValueError" in result
        assert "bad input" in result

    def test_sanitize_llm_error_truncated(self) -> None:
        """Long error messages are truncated to 200 chars."""
        long_msg = "x" * 500
        result = _sanitize_llm_error(
            RuntimeError(long_msg),
        )
        assert len(result) < 300
        assert "..." in result


# ── Tests: agent loop ────────────────────────────────────────────────


class TestGoldenPath:
    """Golden-path tests: LLM calls tools then produces diagnosis."""

    @pytest.mark.asyncio
    async def test_three_tools_then_diagnosis(self) -> None:
        """LLM calls 3 tools then stops with a diagnosis.

        Verifies correct event sequence: 3x (tool_call, tool_result)
        then 1x done.
        """
        sid = '{"session_id": "aaa"}'
        client = MockLLMClient(
            [
                _tool_call_response(
                    ("tc1", "get_session_context", sid),
                ),
                _tool_call_response(
                    ("tc2", "detect_anomalies", sid),
                ),
                _tool_call_response(
                    ("tc3", "search_manual",
                     '{"query": "misfire"}'),
                ),
                _stop_response(
                    "Diagnosis: P0300 misfire due to "
                    "ignition coil failure."
                ),
            ]
        )

        deps = _make_deps(client)
        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        types = [e.event_type for e in events]
        assert types == [
            "tool_call", "tool_result",
            "tool_call", "tool_result",
            "tool_call", "tool_result",
            "done",
        ]

        done = events[-1]
        assert done.payload["partial"] is False
        assert "P0300" in done.payload["diagnosis"]
        assert done.payload["iterations"] == 4
        assert len(done.payload["tools_called"]) == 3

    @pytest.mark.asyncio
    async def test_immediate_diagnosis_no_tools(self) -> None:
        """LLM returns diagnosis on first call without tools.

        Verifies single done event.
        """
        client = MockLLMClient(
            [_stop_response("Simple diagnosis.")]
        )
        deps = _make_deps(client)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        assert len(events) == 1
        assert events[0].event_type == "done"
        assert events[0].payload["partial"] is False
        assert events[0].payload["iterations"] == 1

    @pytest.mark.asyncio
    async def test_multiple_tools_in_one_call(self) -> None:
        """LLM requests 2 tool calls in a single response.

        Verifies both are dispatched and results yielded.
        """
        client = MockLLMClient(
            [
                _tool_call_response(
                    ("tc1", "detect_anomalies",
                     '{"session_id": "x"}'),
                    ("tc2", "search_manual",
                     '{"query": "rpm"}'),
                ),
                _stop_response("Dual-tool diagnosis."),
            ]
        )
        deps = _make_deps(client)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        types = [e.event_type for e in events]
        assert types == [
            "tool_call", "tool_result",
            "tool_call", "tool_result",
            "done",
        ]
        assert events[-1].payload["tools_called"] == [
            "detect_anomalies",
            "search_manual",
        ]


class TestErrorHandling:
    """Tests for error conditions and recovery."""

    @pytest.mark.asyncio
    async def test_unknown_tool_error_then_recovery(
        self,
    ) -> None:
        """LLM calls unknown tool; gets error; self-corrects.

        Verifies the error result is returned to the LLM and the
        loop continues.
        """
        client = MockLLMClient(
            [
                _tool_call_response(
                    ("tc1", "nonexistent_tool",
                     '{"session_id": "x"}'),
                ),
                _stop_response(
                    "Recovered diagnosis after error."
                ),
            ]
        )
        deps = _make_deps(client)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        types = [e.event_type for e in events]
        assert types == [
            "tool_call", "tool_result", "done",
        ]

        tool_result = events[1]
        assert tool_result.payload["is_error"] is True
        assert (
            "unknown tool" in tool_result.payload["output"].lower()
        )

        done = events[-1]
        assert "Recovered" in done.payload["diagnosis"]

    @pytest.mark.asyncio
    async def test_tool_execution_error_continues(
        self,
    ) -> None:
        """Tool handler raises an exception; loop continues.

        The registry catches the exception and returns an error
        string.  The LLM receives it and can self-correct.
        """
        async def failing_handler(
            input_data: Dict[str, Any],
        ) -> str:
            raise RuntimeError("DB connection lost")

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="failing_tool",
                description="Always fails",
                input_schema={
                    "type": "object",
                    "properties": {},
                },
                handler=failing_handler,
            )
        )

        client = MockLLMClient(
            [
                _tool_call_response(
                    ("tc1", "failing_tool", "{}"),
                ),
                _stop_response("Diagnosis despite error."),
            ]
        )
        deps = _make_deps(client, registry=registry)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        tool_result = [
            e for e in events
            if e.event_type == "tool_result"
        ][0]
        assert tool_result.payload["is_error"] is True
        assert "RuntimeError" in tool_result.payload["output"]

        done = events[-1]
        assert done.event_type == "done"
        assert done.payload["partial"] is False

    @pytest.mark.asyncio
    async def test_malformed_tool_args_handled(self) -> None:
        """LLM sends invalid JSON as tool arguments.

        The parse error is forwarded as tool input and the
        registry returns a validation error.
        """
        client = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallInfo(
                            id="tc1",
                            name="get_session_context",
                            arguments="not valid json!!!",
                        ),
                    ],
                    finish_reason="tool_calls",
                ),
                _stop_response("Recovered from bad args."),
            ]
        )
        deps = _make_deps(client)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        tool_result = [
            e for e in events
            if e.event_type == "tool_result"
        ][0]
        assert tool_result.payload["is_error"] is True

    @pytest.mark.asyncio
    async def test_llm_error_yields_error_and_partial(
        self,
    ) -> None:
        """LLM client raises an exception on the first call.

        Verifies: error event yielded, then done with partial.
        """
        client = ErrorMockLLMClient(
            RuntimeError("API unreachable"),
        )
        deps = _make_deps(client)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        types = [e.event_type for e in events]
        assert types == ["error", "done"]
        assert events[0].payload["error_type"] == "llm_error"
        # Error message is sanitised (class name + truncated msg).
        msg = events[0].payload["message"]
        assert "RuntimeError" in msg
        assert "API unreachable" in msg
        assert events[1].payload["partial"] is True


class TestBudgetLimits:
    """Tests for iteration and timeout budgets."""

    @pytest.mark.asyncio
    async def test_max_iterations_partial_diagnosis(
        self,
    ) -> None:
        """LLM always calls tools and never stops voluntarily.

        Verifies: loop stops at max_iterations, yields done with
        partial=True.
        """
        responses = [
            _tool_call_response(
                (f"tc{i}", "detect_anomalies",
                 '{"session_id": "x"}'),
            )
            for i in range(5)
        ]
        client = MockLLMClient(responses)
        deps = _make_deps(client, max_iterations=3)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        done = events[-1]
        assert done.event_type == "done"
        assert done.payload["partial"] is True
        assert done.payload["iterations"] == 3

        tool_calls = [
            e for e in events
            if e.event_type == "tool_call"
        ]
        assert len(tool_calls) == 3

    @pytest.mark.asyncio
    async def test_timeout_yields_error_event(self) -> None:
        """Agent loop times out when LLM is too slow.

        Verifies: error event with type=timeout, then done with
        partial=True.
        """
        client = SlowMockLLMClient()
        deps = _make_deps(client, timeout_seconds=0.05)

        events = await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        types = [e.event_type for e in events]
        assert "error" in types
        error_event = [
            e for e in events if e.event_type == "error"
        ][0]
        assert error_event.payload["error_type"] == "timeout"

        done = events[-1]
        assert done.event_type == "done"
        assert done.payload["partial"] is True


class TestMessageHistory:
    """Tests that verify correct message construction."""

    @pytest.mark.asyncio
    async def test_tool_results_appended_to_messages(
        self,
    ) -> None:
        """Tool results are appended as 'tool' role messages.

        Verifies the LLM receives the full conversation history
        including tool results on subsequent calls.
        """
        client = MockLLMClient(
            [
                _tool_call_response(
                    ("tc1", "detect_anomalies",
                     '{"session_id": "x"}'),
                ),
                _stop_response("Final."),
            ]
        )
        deps = _make_deps(client)

        await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        second_call = client.calls[1]
        messages = second_call["messages"]

        roles = [m["role"] for m in messages]
        assert roles == [
            "system", "user", "assistant", "tool",
        ]

        tool_msg = messages[-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc1"

    @pytest.mark.asyncio
    async def test_model_passed_to_llm(self) -> None:
        """Config model is passed to every LLM call."""
        client = MockLLMClient(
            [_stop_response("Done.")]
        )
        deps = _make_deps(
            client, model="deepseek/deepseek-v3.2",
        )

        await _collect_events(
            run_diagnosis_loop(
                FAKE_SESSION_ID,
                FAKE_PARSED_SUMMARY,
                deps,
            )
        )

        assert client.calls[0]["model"] == (
            "deepseek/deepseek-v3.2"
        )

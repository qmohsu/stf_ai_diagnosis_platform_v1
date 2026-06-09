"""Tests for the streaming agent-loop path (``chat_stream``).

Covers three things added for live reasoning streaming
(HARNESS-22):

* ``OpenAILLMClient.chat_stream`` reassembly — splitting reasoning
  vs content deltas and accumulating streamed tool-call fragments
  by index into the terminal ``LLMResponse``.
* The loop emitting live ``reasoning`` / ``token`` events.
* Graceful fallback to the blocking ``chat()`` when streaming
  fails (e.g. a backend that won't stream with tools).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    HarnessEvent,
    LLMResponse,
    LLMStreamChunk,
    OpenAILLMClient,
    ToolCallInfo,
)
from app.harness.loop import run_diagnosis_loop
from app.harness.tool_registry import ToolDefinition, ToolRegistry


FAKE_SESSION_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
FAKE_PARSED_SUMMARY: Dict[str, Any] = {"vehicle_id": "V12345"}


@pytest.fixture(autouse=True)
def _mock_emit_event():
    """Patch emit_event to a no-op (persistence tested elsewhere)."""
    with patch(
        "app.harness.loop.emit_event", new_callable=AsyncMock,
    ):
        yield


# ── Fakes for the OpenAI streaming SDK surface ───────────────────────


def _delta(
    content: Any = None,
    reasoning: Any = None,
    tool_calls: Any = None,
    model_extra: Any = None,
) -> SimpleNamespace:
    """Build a fake ``choices[0].delta`` object."""
    return SimpleNamespace(
        content=content,
        reasoning=reasoning,
        reasoning_content=None,
        tool_calls=tool_calls,
        model_extra=model_extra or {},
    )


def _chunk(
    content: Any = None,
    reasoning: Any = None,
    tool_calls: Any = None,
    finish: Any = None,
    model_extra: Any = None,
) -> SimpleNamespace:
    """Build a fake streamed chat-completion chunk."""
    choice = SimpleNamespace(
        delta=_delta(content, reasoning, tool_calls, model_extra),
        finish_reason=finish,
    )
    return SimpleNamespace(choices=[choice])


def _tool_delta(
    index: int,
    *,
    id: Any = None,
    name: Any = None,
    args: Any = None,
) -> SimpleNamespace:
    """Build a fake streamed tool-call delta fragment."""
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=args),
    )


class _FakeStream:
    """Async-iterable over a fixed list of chunks."""

    def __init__(self, chunks: List[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for chunk in self._chunks:
                yield chunk

        return _gen()


class _FakeAsyncOpenAI:
    """Minimal stand-in exposing ``chat.completions.create``."""

    def __init__(self, chunks: List[Any]) -> None:
        async def _create(**kwargs: Any) -> _FakeStream:
            return _FakeStream(chunks)

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=_create),
        )


async def _drain_stream(client: OpenAILLMClient) -> List[Any]:
    """Collect everything ``chat_stream`` yields."""
    items: List[Any] = []
    async for item in client.chat_stream(
        messages=[],
        tools=[],
        model="m",
        temperature=0.0,
        max_tokens=10,
    ):
        items.append(item)
    return items


# ── chat_stream reassembly ───────────────────────────────────────────


class TestChatStreamReassembly:
    @pytest.mark.asyncio
    async def test_reasoning_and_content_split(self) -> None:
        """Reasoning and content deltas yield distinct chunk kinds."""
        client = OpenAILLMClient(
            _FakeAsyncOpenAI([
                _chunk(reasoning="Let me "),
                _chunk(reasoning="think."),
                _chunk(content="Final "),
                _chunk(content="answer.", finish="stop"),
            ])
        )
        items = await _drain_stream(client)

        reasoning = [
            i.text for i in items
            if isinstance(i, LLMStreamChunk) and i.kind == "reasoning"
        ]
        content = [
            i.text for i in items
            if isinstance(i, LLMStreamChunk) and i.kind == "content"
        ]
        terminal = items[-1]

        assert reasoning == ["Let me ", "think."]
        assert content == ["Final ", "answer."]
        assert isinstance(terminal, LLMResponse)
        assert terminal.content == "Final answer."
        assert terminal.tool_calls == []
        assert terminal.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_tool_call_delta_accumulation(self) -> None:
        """Streamed tool-call argument fragments concatenate by index."""
        client = OpenAILLMClient(
            _FakeAsyncOpenAI([
                _chunk(reasoning="Need stats."),
                _chunk(tool_calls=[
                    _tool_delta(
                        0, id="call_1",
                        name="get_signal_stats", args='{"sig',
                    ),
                ]),
                _chunk(tool_calls=[_tool_delta(0, args='nal":"rpm"}')]),
                _chunk(finish="tool_calls"),
            ])
        )
        items = await _drain_stream(client)
        terminal = items[-1]

        assert isinstance(terminal, LLMResponse)
        assert len(terminal.tool_calls) == 1
        tc = terminal.tool_calls[0]
        assert tc.id == "call_1"
        assert tc.name == "get_signal_stats"
        assert tc.arguments == '{"signal":"rpm"}'
        assert terminal.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_kept_in_index_order(self) -> None:
        """Two parallel tool calls are reassembled in index order."""
        client = OpenAILLMClient(
            _FakeAsyncOpenAI([
                _chunk(tool_calls=[
                    _tool_delta(0, id="a", name="t1", args="{}"),
                ]),
                _chunk(tool_calls=[
                    _tool_delta(1, id="b", name="t2", args="{}"),
                ]),
                _chunk(finish="tool_calls"),
            ])
        )
        items = await _drain_stream(client)
        terminal = items[-1]

        assert [tc.name for tc in terminal.tool_calls] == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_reasoning_via_model_extra(self) -> None:
        """Reasoning carried in ``model_extra`` is still surfaced."""
        client = OpenAILLMClient(
            _FakeAsyncOpenAI([
                _chunk(model_extra={"reasoning": "hidden thought"}),
                _chunk(content="ok", finish="stop"),
            ])
        )
        items = await _drain_stream(client)
        reasoning = [
            i.text for i in items
            if isinstance(i, LLMStreamChunk) and i.kind == "reasoning"
        ]
        assert reasoning == ["hidden thought"]


# ── Loop streaming behaviour ─────────────────────────────────────────


def _echo_tool(name: str) -> ToolDefinition:
    """Simple tool that echoes a fixed string."""

    async def handler(input_data: Dict[str, Any]) -> str:
        return f"{name}: ok"

    return ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _deps(client: Any) -> HarnessDeps:
    """Build deps with a one-tool registry and short budgets."""
    registry = ToolRegistry()
    registry.register(_echo_tool("list_signals"))
    return HarnessDeps(
        llm_client=client,
        tool_registry=registry,
        config=HarnessConfig(
            model="m", max_iterations=5, timeout_seconds=30.0,
        ),
    )


async def _collect(gen: AsyncIterator[HarnessEvent]) -> List[HarnessEvent]:
    """Drain the loop generator into a list."""
    events: List[HarnessEvent] = []
    async for event in gen:
        events.append(event)
    return events


class _StreamingClient:
    """Replays a scripted chunk sequence per iteration via chat_stream."""

    def __init__(self, scripts: List[List[Any]]) -> None:
        self._scripts = list(scripts)
        self._index = 0
        self.chat_calls = 0

    async def chat(self, **kwargs: Any) -> LLMResponse:
        """Blocking fallback — should not be hit when streaming works."""
        self.chat_calls += 1
        return LLMResponse(
            content="fallback", tool_calls=[], finish_reason="stop",
        )

    async def chat_stream(
        self, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield the next scripted chunk list."""
        script = self._scripts[self._index]
        self._index += 1
        for item in script:
            yield item


class TestLoopStreamingEvents:
    @pytest.mark.asyncio
    async def test_emits_reasoning_and_token_events(self) -> None:
        """Loop surfaces reasoning + answer tokens, no fallback."""
        scripts = [
            [
                LLMStreamChunk("reasoning", "Check signals."),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallInfo("c1", "list_signals", "{}"),
                    ],
                    finish_reason="tool_calls",
                ),
            ],
            [
                LLMStreamChunk("reasoning", "Done analyzing."),
                LLMStreamChunk("content", "Diagnosis: OK."),
                LLMResponse(
                    content="Diagnosis: OK.",
                    tool_calls=[],
                    finish_reason="stop",
                ),
            ],
        ]
        deps = _deps(_StreamingClient(scripts))
        events = await _collect(
            run_diagnosis_loop(
                FAKE_SESSION_ID, FAKE_PARSED_SUMMARY, deps,
            )
        )
        types = [e.event_type for e in events]

        assert "reasoning" in types
        assert "token" in types

        reasoning_evs = [e for e in events if e.event_type == "reasoning"]
        assert reasoning_evs[0].payload["text"] == "Check signals."
        assert reasoning_evs[0].payload["iteration"] == 0

        token_evs = [e for e in events if e.event_type == "token"]
        assert token_evs[0].payload["text"] == "Diagnosis: OK."

        done = events[-1]
        assert done.event_type == "done"
        assert done.payload["diagnosis"] == "Diagnosis: OK."
        assert done.payload["partial"] is False
        # Streaming path used end-to-end — no blocking fallback.
        assert deps.llm_client.chat_calls == 0


class TestLoopStreamingFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_chat_on_stream_error(self) -> None:
        """A chat_stream failure degrades to the blocking chat()."""

        class _BrokenStreamClient:
            def __init__(self) -> None:
                self.chat_calls = 0

            async def chat(self, **kwargs: Any) -> LLMResponse:
                self.chat_calls += 1
                return LLMResponse(
                    content="Fallback diagnosis.",
                    tool_calls=[],
                    finish_reason="stop",
                )

            async def chat_stream(
                self, **kwargs: Any,
            ) -> AsyncIterator[Any]:
                raise RuntimeError("streaming not supported")
                yield  # pragma: no cover — marks this an async gen

        client = _BrokenStreamClient()
        deps = _deps(client)
        events = await _collect(
            run_diagnosis_loop(
                FAKE_SESSION_ID, FAKE_PARSED_SUMMARY, deps,
            )
        )
        done = events[-1]

        assert done.event_type == "done"
        assert done.payload["diagnosis"] == "Fallback diagnosis."
        assert client.chat_calls == 1

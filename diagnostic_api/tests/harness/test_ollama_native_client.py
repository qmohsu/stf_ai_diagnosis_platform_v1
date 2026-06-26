"""Tests for ``OllamaNativeLLMClient`` and its translation helpers.

The native ``/api/chat`` client lets the manual-agent eval suppress
qwen3's reasoning channel (``think=False``) — the only mechanism that
works (HARNESS-23 / #144).  These tests cover the OpenAI<->Ollama
message / tool-call translation and the request payload, with a fake
``httpx.AsyncClient`` so no Ollama is needed.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from app.harness import deps as deps_mod
from app.harness.deps import (
    LLMResponse,
    OllamaNativeLLMClient,
    ToolCallInfo,
    _flatten_tool_content,
    _from_ollama_response,
    _to_ollama_messages,
)


# ── _flatten_tool_content ─────────────────────────────────────────


class TestFlattenToolContent:
    """Tool-result content is reduced to plain text."""

    def test_none_becomes_empty(self) -> None:
        """``None`` content → empty string."""
        assert _flatten_tool_content(None) == ""

    def test_string_passthrough(self) -> None:
        """A plain string is returned unchanged."""
        assert _flatten_tool_content("section text") == "section text"

    def test_multimodal_keeps_text_drops_images(self) -> None:
        """Text blocks are joined; image blocks are dropped."""
        blocks = [
            {"type": "text", "text": "prose"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "more"},
        ]
        assert _flatten_tool_content(blocks) == "prose\nmore"


# ── _to_ollama_messages ───────────────────────────────────────────


class TestToOllamaMessages:
    """OpenAI-shaped history → Ollama-native shape."""

    def test_system_and_user_passthrough(self) -> None:
        """Plain messages keep their role + string content."""
        out = _to_ollama_messages([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ])
        assert out == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]

    def test_assistant_tool_call_args_string_to_object(self) -> None:
        """OpenAI string ``arguments`` become a native object."""
        out = _to_ollama_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "tc_0",
                "type": "function",
                "function": {
                    "name": "read_manual_section",
                    "arguments": json.dumps(
                        {"manual_id": "M", "section": "s"},
                    ),
                },
            }],
        }])
        assert len(out) == 1
        msg = out[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == ""  # None coerced to ""
        call = msg["tool_calls"][0]
        # id / type are dropped; arguments is now a dict.
        assert call == {
            "function": {
                "name": "read_manual_section",
                "arguments": {"manual_id": "M", "section": "s"},
            },
        }

    def test_malformed_tool_args_become_empty_object(self) -> None:
        """Unparseable arguments degrade to ``{}`` rather than crash."""
        out = _to_ollama_messages([{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {"name": "x", "arguments": "{not json"},
            }],
        }])
        assert out[0]["tool_calls"][0]["function"]["arguments"] == {}

    def test_tool_message_drops_id_and_flattens(self) -> None:
        """Tool results lose ``tool_call_id``; list content flattens."""
        out = _to_ollama_messages([{
            "role": "tool",
            "tool_call_id": "tc_0",
            "content": [
                {"type": "text", "text": "a"},
                {"type": "image_url", "image_url": {"url": "x"}},
            ],
        }])
        assert out == [{"role": "tool", "content": "a"}]


# ── _from_ollama_response ─────────────────────────────────────────


class TestFromOllamaResponse:
    """Ollama ``/api/chat`` body → ``LLMResponse``."""

    def test_content_only(self) -> None:
        """A text answer maps to content + finish from done_reason."""
        resp = _from_ollama_response({
            "message": {"role": "assistant", "content": "the answer"},
            "done_reason": "stop",
        })
        assert resp.content == "the answer"
        assert resp.tool_calls == []
        assert resp.finish_reason == "stop"

    def test_tool_calls_object_args_to_json_string(self) -> None:
        """Native object ``arguments`` become a JSON string."""
        resp = _from_ollama_response({
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "get_manual_toc",
                        "arguments": {"manual_id": "M"},
                    },
                }],
            },
        })
        assert resp.finish_reason == "tool_calls"
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert isinstance(tc, ToolCallInfo)
        assert tc.name == "get_manual_toc"
        assert tc.id == "call_0"
        assert json.loads(tc.arguments) == {"manual_id": "M"}

    def test_empty_content_is_none(self) -> None:
        """Empty content normalises to ``None`` (no tool calls)."""
        resp = _from_ollama_response({"message": {"content": ""}})
        assert resp.content is None
        assert resp.finish_reason == "stop"


# ── OllamaNativeLLMClient.chat ────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response``."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Captures the POST so the test can assert on the payload."""

    last_url: str = ""
    last_json: Dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, json: Dict[str, Any]):  # noqa: A002
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_json = json
        return _FakeResponse({
            "message": {"role": "assistant", "content": "ok"},
            "done_reason": "stop",
        })


class TestOllamaNativeChat:
    """End-to-end ``chat()`` with a faked HTTP layer."""

    @pytest.mark.asyncio
    async def test_payload_sets_think_false_and_options(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Request disables thinking and maps token/temperature."""
        monkeypatch.setattr(
            deps_mod.httpx, "AsyncClient", _FakeAsyncClient,
        )
        client = OllamaNativeLLMClient("http://127.0.0.1:11434/")
        resp = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            model="qwen3.5:27b-q8_0",
            temperature=0.2,
            max_tokens=4096,
        )
        assert resp.content == "ok"
        sent = _FakeAsyncClient.last_json
        assert _FakeAsyncClient.last_url == (
            "http://127.0.0.1:11434/api/chat"
        )
        assert sent["think"] is False
        assert sent["stream"] is False
        assert sent["options"] == {
            "temperature": 0.2, "num_predict": 4096,
        }
        # Empty tools list is omitted entirely.
        assert "tools" not in sent

    @pytest.mark.asyncio
    async def test_tools_included_when_present(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-empty tool list is forwarded to Ollama."""
        monkeypatch.setattr(
            deps_mod.httpx, "AsyncClient", _FakeAsyncClient,
        )
        client = OllamaNativeLLMClient("http://127.0.0.1:11434")
        schema = {"type": "function", "function": {"name": "x"}}
        await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[schema],
            model="m",
            temperature=0.0,
            max_tokens=10,
        )
        assert _FakeAsyncClient.last_json["tools"] == [schema]

    def test_chat_stream_not_implemented(self) -> None:
        """Streaming is intentionally unsupported for this client."""
        client = OllamaNativeLLMClient("http://127.0.0.1:11434")
        with pytest.raises(NotImplementedError):
            client.chat_stream(
                messages=[], tools=[], model="m",
                temperature=0.0, max_tokens=10,
            )

    @pytest.mark.asyncio
    async def test_think_true_when_requested(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``think=True`` leaves the reasoning channel on."""
        monkeypatch.setattr(
            deps_mod.httpx, "AsyncClient", _FakeAsyncClient,
        )
        client = OllamaNativeLLMClient("http://127.0.0.1:11434", think=True)
        await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[], model="m", temperature=0.0, max_tokens=10,
        )
        assert _FakeAsyncClient.last_json["think"] is True

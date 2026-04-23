"""Unit tests for the manual-search sub-agent.

Uses a scripted ``LLMClient`` that replays pre-queued responses so
tests run without Ollama or OpenRouter access.  Covers: registry
restriction, final-JSON parsing variations, raw-section capture,
tool-trace assembly, max-iteration exit, error recovery, and
empty-final-content fallback.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

import pytest

from app.harness.deps import LLMResponse, ToolCallInfo
from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from app.harness_agents.manual_agent import (
    ManualAgentConfig,
    ManualAgentDeps,
    _extract_last_assistant_content,
    _extract_section_ref,
    _parse_final_json,
    _parse_tool_arguments,
    _sanitize_tool_input_for_trace,
    _strip_markdown_fence,
    create_manual_agent_registry,
    run_manual_agent,
)
from app.harness_agents.types import ManualAgentResult


# ── Scripted LLM client ───────────────────────────────────────────


class _ScriptedLLMClient:
    """Minimal ``LLMClient`` that replays pre-queued responses.

    Each enqueued response is either an ``LLMResponse`` (returned
    normally) or an ``Exception`` instance (raised).  Records every
    call's kwargs for later assertions.
    """

    def __init__(
        self,
        responses: List[Union[LLMResponse, Exception]],
    ) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError(
                "ScriptedLLMClient exhausted — test queued "
                "too few responses",
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _final_response(summary: str, citations: Any = None) -> LLMResponse:
    """Build an LLMResponse that ends the loop."""
    payload: Dict[str, Any] = {
        "summary": summary,
        "citations": citations if citations is not None else [],
    }
    return LLMResponse(
        content=json.dumps(payload),
        tool_calls=[],
        finish_reason="stop",
    )


def _tool_call_response(
    calls: List[Dict[str, Any]],
) -> LLMResponse:
    """Build an LLMResponse that requests tool calls."""
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallInfo(
                id=f"tc_{i}",
                name=c["name"],
                arguments=json.dumps(c.get("arguments", {})),
            )
            for i, c in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


# ── Mock tool registry ────────────────────────────────────────────


def _build_mock_registry(
    outputs: Dict[str, Any],
) -> ToolRegistry:
    """Build a tool registry with stub handlers returning canned output.

    Args:
        outputs: Map of tool_name -> canned output (str or
            content-block list).  Missing tools fall back to a
            default string.

    Returns:
        A ``ToolRegistry`` with the 4 manual tools registered
        using fake handlers.
    """
    registry = ToolRegistry()

    async def _handler_for(name: str, _input: Dict[str, Any]) -> Any:
        return outputs.get(name, f"stub output for {name}")

    def _make_def(name: str) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=f"mock {name}",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
            handler=lambda d, n=name: _handler_for(n, d),
            is_read_only=True,
        )

    for name in (
        "list_manuals",
        "get_manual_toc",
        "read_manual_section",
        "search_manual",
    ):
        registry.register(_make_def(name))
    return registry


def _make_deps(
    responses: List[Union[LLMResponse, Exception]],
    tool_outputs: Optional[Dict[str, Any]] = None,
    config: Optional[ManualAgentConfig] = None,
) -> tuple[ManualAgentDeps, _ScriptedLLMClient]:
    """Assemble deps + return the client for later assertions."""
    client = _ScriptedLLMClient(responses)
    registry = _build_mock_registry(tool_outputs or {})
    deps = ManualAgentDeps(
        llm_client=client,  # type: ignore[arg-type]
        tool_registry=registry,
        config=config or ManualAgentConfig(
            max_iterations=5, timeout_seconds=30.0,
        ),
    )
    return deps, client


# ── Registry builder ──────────────────────────────────────────────


class TestManualAgentRegistry:
    """Tests for ``create_manual_agent_registry``."""

    def test_registers_exactly_four_manual_tools(self) -> None:
        """Registry contains the 4 manual tools, nothing else."""
        registry = create_manual_agent_registry()
        assert set(registry.tool_names) == {
            "list_manuals",
            "get_manual_toc",
            "read_manual_section",
            "search_manual",
        }

    def test_read_obd_data_is_not_registered(self) -> None:
        """Restricted registry must NOT include read_obd_data."""
        registry = create_manual_agent_registry()
        assert "read_obd_data" not in registry.tool_names


# ── Parse helpers ─────────────────────────────────────────────────


class TestStripMarkdownFence:
    """Tests for ``_strip_markdown_fence``."""

    def test_strips_json_fence(self) -> None:
        """Common ```json\\n...\\n``` wrapper is unwrapped."""
        wrapped = '```json\n{"a": 1}\n```'
        assert _strip_markdown_fence(wrapped) == '{"a": 1}'

    def test_strips_bare_fence(self) -> None:
        """Bare ``` fence is also stripped."""
        wrapped = '```\n{"a": 1}\n```'
        assert _strip_markdown_fence(wrapped) == '{"a": 1}'

    def test_unfenced_content_is_returned_as_is(self) -> None:
        """Non-fenced content is just trimmed."""
        assert _strip_markdown_fence("  plain  ") == "plain"


class TestParseFinalJson:
    """Tests for ``_parse_final_json``."""

    def test_clean_json_parses(self) -> None:
        """Well-formed JSON yields summary + citations."""
        content = json.dumps({
            "summary": "Fault is X.",
            "citations": [
                {
                    "manual_id": "M",
                    "slug": "s",
                    "quote": "q",
                },
            ],
        })
        summary, citations = _parse_final_json(content)
        assert summary == "Fault is X."
        assert len(citations) == 1
        assert citations[0].manual_id == "M"
        assert citations[0].slug == "s"
        assert citations[0].quote == "q"

    def test_markdown_fenced_json_parses(self) -> None:
        """```json fenced response is still parsed."""
        raw = (
            '```json\n'
            '{"summary": "ok", "citations": []}\n'
            '```'
        )
        summary, citations = _parse_final_json(raw)
        assert summary == "ok"
        assert citations == []

    def test_json_with_prose_is_extracted(self) -> None:
        """Embedded JSON object is found via regex fallback."""
        raw = (
            "Here is my answer:\n\n"
            '{"summary": "extracted", "citations": []}\n\n'
            "Hope this helps!"
        )
        summary, _ = _parse_final_json(raw)
        assert summary == "extracted"

    def test_malformed_content_falls_back_to_raw_text(self) -> None:
        """Unparseable content becomes the summary verbatim."""
        raw = "this is just prose, no JSON here"
        summary, citations = _parse_final_json(raw)
        assert summary == raw
        assert citations == []

    def test_empty_content_produces_fallback_message(self) -> None:
        """None / empty content yields a clear fallback message."""
        summary, citations = _parse_final_json(None)
        assert "no final content" in summary.lower()
        assert citations == []
        summary2, _ = _parse_final_json("")
        assert "no final content" in summary2.lower()

    def test_invalid_citation_entries_are_skipped(self) -> None:
        """Non-dict citation entries are dropped silently."""
        raw = json.dumps({
            "summary": "ok",
            "citations": [
                "not a dict",
                {
                    "manual_id": "M",
                    "slug": "s",
                    "quote": "q",
                },
                42,
            ],
        })
        _, citations = _parse_final_json(raw)
        assert len(citations) == 1
        assert citations[0].manual_id == "M"

    def test_summary_is_truncated_at_cap(self) -> None:
        """Very long summaries are clipped to the safety cap."""
        raw = json.dumps({
            "summary": "X" * 20_000,
            "citations": [],
        })
        summary, _ = _parse_final_json(raw)
        assert len(summary) <= 4000


class TestParseToolArguments:
    """Tests for ``_parse_tool_arguments``."""

    def test_valid_json_object(self) -> None:
        """Valid JSON object parses to a dict."""
        assert _parse_tool_arguments('{"x": 1}') == {"x": 1}

    def test_empty_string_yields_empty_dict(self) -> None:
        """Empty string is treated as no arguments."""
        assert _parse_tool_arguments("") == {}

    def test_malformed_json_returns_parse_error(self) -> None:
        """Malformed JSON surfaces ``_parse_error`` key."""
        result = _parse_tool_arguments("{not valid")
        assert "_parse_error" in result

    def test_non_object_json_returns_parse_error(self) -> None:
        """Array / scalar JSON is rejected with parse error."""
        result = _parse_tool_arguments("[1,2,3]")
        assert "_parse_error" in result


class TestSanitizeToolInputForTrace:
    """Tests for ``_sanitize_tool_input_for_trace``."""

    def test_strips_underscore_prefixed_keys(self) -> None:
        """Internal keys like ``_session_id`` are removed."""
        result = _sanitize_tool_input_for_trace({
            "query": "q", "_session_id": "abc",
        })
        assert result == {"query": "q"}

    def test_truncates_long_strings(self) -> None:
        """String values over 500 chars are truncated."""
        result = _sanitize_tool_input_for_trace({
            "query": "X" * 1000,
        })
        assert len(result["query"]) <= 503  # 500 + "..."

    def test_preserves_short_values_unchanged(self) -> None:
        """Short values pass through verbatim."""
        result = _sanitize_tool_input_for_trace({
            "x": 42, "y": "short",
        })
        assert result == {"x": 42, "y": "short"}


class TestExtractSectionRef:
    """Tests for ``_extract_section_ref``."""

    def test_string_output(self) -> None:
        """Plain string output -> SectionRef with had_images=False."""
        ref = _extract_section_ref(
            {"manual_id": "M", "section": "s"},
            "full section text",
        )
        assert ref is not None
        assert ref.manual_id == "M"
        assert ref.slug == "s"
        assert ref.text == "full section text"
        assert ref.had_images is False

    def test_multimodal_output_detects_images(self) -> None:
        """Content-block list with image_url -> had_images=True."""
        ref = _extract_section_ref(
            {"manual_id": "M", "section": "s"},
            [
                {"type": "text", "text": "prose"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:..."},
                },
                {"type": "text", "text": "more prose"},
            ],
        )
        assert ref is not None
        assert ref.text == "prose\nmore prose"
        assert ref.had_images is True

    def test_missing_manual_id_returns_none(self) -> None:
        """Missing manual_id -> cannot identify section -> None."""
        ref = _extract_section_ref(
            {"section": "s"}, "text",
        )
        assert ref is None

    def test_missing_section_returns_none(self) -> None:
        """Missing section slug -> None."""
        ref = _extract_section_ref(
            {"manual_id": "M"}, "text",
        )
        assert ref is None


class TestExtractLastAssistantContent:
    """Tests for ``_extract_last_assistant_content`` fallback."""

    def test_returns_last_non_empty_assistant(self) -> None:
        """Last assistant content is returned when present."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        assert (
            _extract_last_assistant_content(messages)
            == "second"
        )

    def test_fallback_when_no_content(self) -> None:
        """No assistant content -> canned fallback string."""
        messages = [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": None},
        ]
        result = _extract_last_assistant_content(messages)
        assert "did not produce" in result.lower()


# ── Full loop integration ─────────────────────────────────────────


class TestRunManualAgentHappyPath:
    """End-to-end loop with scripted LLM + stub tools."""

    @pytest.mark.asyncio
    async def test_terminates_on_stop_finish_reason(self) -> None:
        """First response with finish=stop ends loop."""
        deps, client = _make_deps([
            _final_response("immediate answer"),
        ])
        result = await run_manual_agent("q", None, deps)
        assert isinstance(result, ManualAgentResult)
        assert result.stopped_reason == "complete"
        assert result.summary == "immediate answer"
        assert result.iterations == 1
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_tool_call_then_final_answer(self) -> None:
        """One tool call, then final JSON -> iterations=2."""
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([
                    {"name": "list_manuals"},
                ]),
                _final_response(
                    "found it",
                    [{
                        "manual_id": "MWS150A",
                        "slug": "3-2",
                        "quote": "q",
                    }],
                ),
            ],
            tool_outputs={
                "list_manuals": "one manual available",
            },
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.iterations == 2
        assert result.summary == "found it"
        assert len(result.citations) == 1
        assert len(result.tool_trace) == 1
        assert result.tool_trace[0].name == "list_manuals"

    @pytest.mark.asyncio
    async def test_read_manual_section_captures_raw_section(
        self,
    ) -> None:
        """read_manual_section results land in raw_sections."""
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([{
                    "name": "read_manual_section",
                    "arguments": {
                        "manual_id": "MWS150A",
                        "section": "3-2",
                    },
                }]),
                _final_response("done"),
            ],
            tool_outputs={
                "read_manual_section": (
                    "detailed section content"
                ),
            },
        )
        result = await run_manual_agent("q", None, deps)
        assert len(result.raw_sections) == 1
        assert result.raw_sections[0].manual_id == "MWS150A"
        assert result.raw_sections[0].slug == "3-2"
        assert (
            result.raw_sections[0].text
            == "detailed section content"
        )
        assert result.raw_sections[0].had_images is False

    @pytest.mark.asyncio
    async def test_multimodal_section_flags_images(self) -> None:
        """Multimodal output flips had_images=True."""
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([{
                    "name": "read_manual_section",
                    "arguments": {
                        "manual_id": "MWS150A",
                        "section": "3-2",
                    },
                }]),
                _final_response("done"),
            ],
            tool_outputs={
                "read_manual_section": [
                    {"type": "text", "text": "prose"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:..."},
                    },
                ],
            },
        )
        result = await run_manual_agent("q", None, deps)
        assert result.raw_sections[0].had_images is True

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_recorded_in_trace(
        self,
    ) -> None:
        """All tool calls appear in tool_trace in order."""
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([
                    {"name": "list_manuals"},
                    {
                        "name": "get_manual_toc",
                        "arguments": {"manual_id": "M"},
                    },
                ]),
                _final_response("done"),
            ],
            tool_outputs={
                "list_manuals": "ok",
                "get_manual_toc": "toc text",
            },
        )
        result = await run_manual_agent("q", None, deps)
        names = [tc.name for tc in result.tool_trace]
        assert names == ["list_manuals", "get_manual_toc"]


class TestRunManualAgentBudget:
    """Iteration and error budget behaviour."""

    @pytest.mark.asyncio
    async def test_max_iterations_exits_cleanly(self) -> None:
        """Hitting max_iterations yields stopped_reason='max_iterations'."""
        # Queue up more tool-call responses than max_iterations
        # so the loop never sees a "stop" finish.
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([{"name": "list_manuals"}])
                for _ in range(10)
            ],
            tool_outputs={"list_manuals": "ok"},
            config=ManualAgentConfig(
                max_iterations=3, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "max_iterations"
        assert result.iterations == 3
        # summary falls back to last assistant content.
        assert result.summary  # non-empty

    @pytest.mark.asyncio
    async def test_llm_error_sets_error_stopped_reason(
        self,
    ) -> None:
        """LLM exception sets stopped_reason='error' gracefully."""
        deps, _ = _make_deps([
            RuntimeError("API boom"),
        ])
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "error"
        # loop broke before any tool calls.
        assert len(result.tool_trace) == 0

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments_do_not_crash(
        self,
    ) -> None:
        """Malformed tool args produce an error trace entry
        but the loop continues to the next response."""
        # Manually construct a response with invalid JSON args.
        bad_call_response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallInfo(
                    id="tc_bad",
                    name="list_manuals",
                    arguments="{not valid json",
                ),
            ],
            finish_reason="tool_calls",
        )
        deps, _ = _make_deps(
            responses=[
                bad_call_response,
                _final_response("recovered"),
            ],
            tool_outputs={"list_manuals": "ok"},
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == "recovered"
        # trace recorded the broken call as is_error.
        assert len(result.tool_trace) == 1
        assert result.tool_trace[0].is_error is True

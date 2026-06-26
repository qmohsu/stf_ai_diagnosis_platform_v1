"""Unit tests for the manual-search sub-agent.

Uses a scripted ``LLMClient`` that replays pre-queued responses so
tests run without Ollama or OpenRouter access.  Covers: registry
restriction, final-JSON parsing variations, raw-section capture,
tool-trace assembly, max-iteration exit, error recovery, and
empty-final-content fallback.
"""

from __future__ import annotations

import copy
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
    _FORCED_DECLINE_SUMMARY,
    _MAX_SECTION_READS_BEFORE_FINAL,
    ManualAgentConfig,
    ManualAgentDeps,
    _extract_last_assistant_content,
    _extract_section_ref,
    _NO_THINK_DIRECTIVE,
    _force_not_found_finalize,
    _parse_final_json,
    _suppress_thinking_in_system,
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
        # Snapshot messages — the loop mutates the same list in place
        # (e.g. appending the /no_think directive), so storing the
        # reference would make every recorded call look identical.
        recorded = dict(kwargs)
        recorded["messages"] = copy.deepcopy(kwargs.get("messages"))
        self.calls.append(recorded)
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

    def test_registers_exactly_three_manual_tools(self) -> None:
        """Registry contains the 3 manual-fs navigation tools.

        ``search_manual`` was removed in HARNESS-15 to keep the
        agent's capabilities architecturally orthogonal to RAG —
        see ``create_manual_agent_registry`` docstring.
        """
        registry = create_manual_agent_registry()
        assert set(registry.tool_names) == {
            "list_manuals",
            "get_manual_toc",
            "read_manual_section",
        }

    def test_read_obd_data_is_not_registered(self) -> None:
        """Restricted registry must NOT include read_obd_data."""
        registry = create_manual_agent_registry()
        assert "read_obd_data" not in registry.tool_names

    def test_search_manual_is_not_registered(self) -> None:
        """``search_manual`` was deliberately removed (HARNESS-15)."""
        registry = create_manual_agent_registry()
        assert "search_manual" not in registry.tool_names


# ── Config defaults ──────────────────────────────────────────────


class TestManualAgentConfigDefaults:
    """Pin the default-config values that affect eval behaviour."""

    def test_default_max_iterations_is_12(self) -> None:
        """HARNESS-23 T1 (#143) raised the cap 8 → 12 after the
        first-round eval: 6/30 runs hit the old 8-iter cap mid-answer
        (``stopped_reason='max_iterations'``).  The cap and the wall
        timeout bind *different* entries, so both moved together.

        Tighten only when a re-baseline shows deterministic
        convergence below 12 cycles.
        """
        assert ManualAgentConfig().max_iterations == 12

    def test_default_timeout_is_240_seconds(self) -> None:
        """HARNESS-23 T1 (#143) raised the wall 120 → 240 s, mirroring
        the OBD agent's precedent.  At a stable ~10-24 s/iter
        (``qwen3.5:27b`` thinking mode) the old 120 s wall cut runs
        off at 5-7 iterations — 13/30 first-round runs timed out
        before converging.
        """
        assert ManualAgentConfig().timeout_seconds == 240.0

    def test_default_max_tokens_unchanged(self) -> None:
        """The first-round budget failures were iteration/wall-clock
        bound, not output-token bound (no run hit the per-call cap),
        so T1 left ``max_tokens`` at 12288.  Pinned so a sloppy edit
        doesn't silently change it alongside the two limits that
        did move.
        """
        assert ManualAgentConfig().max_tokens == 12_288


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
        """Hitting max_iterations yields stopped_reason='max_iterations'.

        Each queued response makes a *distinct, non-read* tool call
        (``get_manual_toc`` with a unique manual_id) so neither the
        read-count nor the repeat trigger of the no-progress backstop
        (HARNESS-23 T2) fires — this exercises the genuine
        iteration-cap exit, where the agent keeps navigating but
        never finalizes.
        """
        deps, _ = _make_deps(
            responses=[
                _tool_call_response([{
                    "name": "get_manual_toc",
                    "arguments": {"manual_id": f"M-{i}"},
                }])
                for i in range(10)
            ],
            tool_outputs={"get_manual_toc": "ok"},
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


class TestRunManualAgentForcedSynthesis:
    """No-progress backstop → forced synthesis turn (HARNESS-23 T2).

    The server smoke showed the live model spins by reading *distinct*
    sections while hunting absent info (the adversarial ``P9999``
    golden read 6 sections and rode the 240 s wall to
    ``answer_quality=0``).  Once it has read enough — or genuinely
    repeats a call — the loop withholds the tools and forces one
    synthesis turn so it must answer / decline from what it gathered.
    """

    @staticmethod
    def _read(section: str) -> LLMResponse:
        """A response that reads one distinct section."""
        return _tool_call_response([{
            "name": "read_manual_section",
            "arguments": {"manual_id": "M", "section": section},
        }])

    @pytest.mark.asyncio
    async def test_read_count_forces_tool_less_final_turn(
        self,
    ) -> None:
        """After N distinct reads, the next turn is forced tool-less.

        This is the fix the smoke demanded: distinct-section spinning
        (no byte-identical repeat) must still terminate in a clean
        ``complete`` instead of a wall-clock timeout.
        """
        reads = [
            self._read(f"s-{i}")
            for i in range(_MAX_SECTION_READS_BEFORE_FINAL)
        ]
        deps, client = _make_deps(
            responses=reads + [
                _final_response(
                    "Not found: P9999 is not in the table; the "
                    "manual defines P0117 and P0335.",
                ),
            ],
            tool_outputs={"read_manual_section": "nothing relevant"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("P9999?", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary.lower().startswith("not found")
        # The forced (terminal) turn withheld the tools.
        assert client.calls[-1]["tools"] == []
        # Terminated right after the read budget, not at iter cap 12.
        assert result.iterations == _MAX_SECTION_READS_BEFORE_FINAL + 1

    @pytest.mark.asyncio
    async def test_forced_turn_suppresses_thinking(self) -> None:
        """The forced turn appends /no_think to the system prompt.

        In thinking mode a slow synthesis call can blow the wall
        budget; disabling reasoning on this one turn keeps it fast.
        """
        reads = [
            self._read(f"s-{i}")
            for i in range(_MAX_SECTION_READS_BEFORE_FINAL)
        ]
        deps, client = _make_deps(
            responses=reads + [_final_response("Not found: absent.")],
            tool_outputs={"read_manual_section": "x"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        await run_manual_agent("q", None, deps)
        # The forced (final) turn's system message carries /no_think;
        # the earlier tool-calling turns do not.
        forced_system = client.calls[-1]["messages"][0]
        assert forced_system["role"] == "system"
        assert _NO_THINK_DIRECTIVE in forced_system["content"]
        first_system = client.calls[0]["messages"][0]
        assert _NO_THINK_DIRECTIVE not in first_system["content"]

    @pytest.mark.asyncio
    async def test_forced_turn_can_synthesize_a_real_answer(
        self,
    ) -> None:
        """The forced turn isn't hard-wired to decline.

        Withholding tools lets the model give the substantive answer
        the goldens expect, not a canned refusal.
        """
        reads = [
            self._read(f"s-{i}")
            for i in range(_MAX_SECTION_READS_BEFORE_FINAL)
        ]
        deps, _ = _make_deps(
            responses=reads + [
                _final_response("Torque spec is 23 N·m per §4.2."),
            ],
            tool_outputs={"read_manual_section": "section text"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("torque?", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == "Torque spec is 23 N·m per §4.2."

    @pytest.mark.asyncio
    async def test_repeated_call_forces_final(self) -> None:
        """A byte-identical repeat trips the backstop early."""
        deps, client = _make_deps(
            responses=[
                self._read("s"),  # novel
                self._read("s"),  # identical repeat → force_final
                _final_response("Not found: nothing here."),
            ],
            tool_outputs={"read_manual_section": "x"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert client.calls[-1]["tools"] == []

    @pytest.mark.asyncio
    async def test_forced_turn_empty_content_falls_back(self) -> None:
        """Empty forced-turn content → canned decline, still complete."""
        reads = [
            self._read(f"s-{i}")
            for i in range(_MAX_SECTION_READS_BEFORE_FINAL)
        ]
        empty = LLMResponse(
            content="", tool_calls=[], finish_reason="stop",
        )
        deps, _ = _make_deps(
            responses=reads + [empty],
            tool_outputs={"read_manual_section": "x"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == _FORCED_DECLINE_SUMMARY

    @pytest.mark.asyncio
    async def test_forced_turn_chat_error_falls_back(self) -> None:
        """If the forced synthesis call errors, degrade to a decline.

        A failure on the *forced* turn must still finalize cleanly
        (not surface stopped_reason='error').
        """
        reads = [
            self._read(f"s-{i}")
            for i in range(_MAX_SECTION_READS_BEFORE_FINAL)
        ]
        deps, _ = _make_deps(
            responses=reads + [RuntimeError("boom on forced turn")],
            tool_outputs={"read_manual_section": "x"},
            config=ManualAgentConfig(
                max_iterations=12, timeout_seconds=30.0,
            ),
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == _FORCED_DECLINE_SUMMARY

    @pytest.mark.asyncio
    async def test_under_budget_reads_do_not_force(self) -> None:
        """Fewer than N reads + a self-final → agent's answer stands."""
        deps, client = _make_deps(
            responses=[
                self._read("a"),
                self._read("b"),
                _final_response("Concrete answer from the manual."),
            ],
            tool_outputs={"read_manual_section": "section text"},
        )
        # Guard: this test assumes the read budget is > 2.
        assert _MAX_SECTION_READS_BEFORE_FINAL > 2
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == "Concrete answer from the manual."
        # No forced turn → every call saw the real tools.
        assert all(c["tools"] != [] for c in client.calls)

    @pytest.mark.asyncio
    async def test_agent_self_declines_early(self) -> None:
        """An LLM that declines on its own is taken verbatim."""
        deps, client = _make_deps(
            responses=[
                _tool_call_response([{"name": "list_manuals"}]),
                _final_response("Not found: no manual for this car."),
            ],
            tool_outputs={"list_manuals": "only an unrelated manual"},
        )
        result = await run_manual_agent("q", None, deps)
        assert result.stopped_reason == "complete"
        assert result.summary == "Not found: no manual for this car."
        assert len(client.calls) == 2


class TestForceNotFoundFinalize:
    """Unit tests for ``_force_not_found_finalize``."""

    def test_preserves_agent_decline(self) -> None:
        """Agent's own "Not found" message + cites are kept."""
        messages = [
            {"role": "assistant", "content": json.dumps({
                "summary": "Not found: code P9999 is not in the manual.",
                "citations": [],
            })},
        ]
        summary, citations = _force_not_found_finalize(messages, [])
        assert summary == (
            "Not found: code P9999 is not in the manual."
        )
        assert citations == []

    def test_falls_back_to_canned_decline(self) -> None:
        """Non-decline last message → canned "Not found" summary."""
        messages = [
            {"role": "assistant", "content": "let me read more"},
        ]
        summary, citations = _force_not_found_finalize(messages, [])
        assert summary == _FORCED_DECLINE_SUMMARY
        assert summary.lower().startswith("not found")
        assert citations == []

    def test_empty_history_falls_back(self) -> None:
        """No assistant content → canned decline, never crashes."""
        summary, _ = _force_not_found_finalize([], [])
        assert summary == _FORCED_DECLINE_SUMMARY


class TestSuppressThinkingInSystem:
    """Unit tests for ``_suppress_thinking_in_system``."""

    def test_appends_directive_to_system(self) -> None:
        """Directive is appended to the system message content."""
        messages = [
            {"role": "system", "content": "base prompt"},
            {"role": "user", "content": "q"},
        ]
        _suppress_thinking_in_system(messages)
        assert messages[0]["content"].endswith(_NO_THINK_DIRECTIVE)
        assert "base prompt" in messages[0]["content"]

    def test_idempotent(self) -> None:
        """A second call does not duplicate the directive."""
        messages = [{"role": "system", "content": "base"}]
        _suppress_thinking_in_system(messages)
        _suppress_thinking_in_system(messages)
        assert messages[0]["content"].count(_NO_THINK_DIRECTIVE) == 1

    def test_no_system_message_is_noop(self) -> None:
        """No system message → nothing fabricated, no crash."""
        messages = [{"role": "user", "content": "q"}]
        _suppress_thinking_in_system(messages)
        assert messages == [{"role": "user", "content": "q"}]

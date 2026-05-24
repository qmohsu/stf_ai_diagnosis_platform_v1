"""Unit tests for the OBD sub-agent (HARNESS-19).

Uses a scripted ``LLMClient`` that replays pre-queued responses so
tests run without Ollama or OpenRouter access.  Covers: registry
restriction (recursion guard), final-JSON parsing variations,
raw-data capture, tool-trace assembly, max-iteration exit, error
recovery, timeout fallback, and empty-final-content fallback.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Union

import pytest

from app.harness.deps import LLMResponse, ToolCallInfo
from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
)
from app.harness_agents.obd_agent import (
    OBDAgentConfig,
    OBDAgentDeps,
    _build_data_excerpt,
    _coerce_dtc_citations,
    _coerce_limitations,
    _coerce_signal_citations,
    _parse_final_json,
    _parse_tool_arguments,
    _strip_markdown_fence,
    create_obd_agent_registry,
    run_obd_agent,
)
from app.harness_agents.types import (
    OBDAgentResult,
    SignalCitation,
)


# ── Scripted LLM client ──────────────────────────────────────────


class _ScriptedLLMClient:
    """Minimal ``LLMClient`` that replays pre-queued responses."""

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


def _final_response(
    summary: str = "test summary",
    signal_citations: Optional[List[Dict[str, Any]]] = None,
    dtc_citations: Optional[List[Dict[str, Any]]] = None,
    limitations: Optional[List[str]] = None,
) -> LLMResponse:
    """Build an LLMResponse that ends the loop."""
    payload: Dict[str, Any] = {
        "summary": summary,
        "signal_citations": signal_citations or [],
        "dtc_citations": dtc_citations or [],
        "raw_data": [],
        "limitations": limitations or [],
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


def _build_mock_registry(
    outputs: Optional[Dict[str, Any]] = None,
) -> ToolRegistry:
    """Build a registry with stub handlers returning canned text."""
    outputs = outputs or {}
    registry = ToolRegistry()

    async def _handler(name: str, _input: Dict[str, Any]) -> Any:
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
            handler=lambda d, n=name: _handler(n, d),
            is_read_only=True,
        )

    for name in (
        "list_signals",
        "read_window",
        "get_signal_stats",
        "find_events",
        "list_dtcs",
        "lookup_dtc",
    ):
        registry.register(_make_def(name))
    return registry


def _make_deps(
    responses: List[Union[LLMResponse, Exception]],
    tool_outputs: Optional[Dict[str, Any]] = None,
    config: Optional[OBDAgentConfig] = None,
) -> tuple[OBDAgentDeps, _ScriptedLLMClient]:
    """Build OBDAgentDeps + return client for assertion."""
    client = _ScriptedLLMClient(responses)
    registry = _build_mock_registry(tool_outputs)
    deps = OBDAgentDeps(
        llm_client=client,  # type: ignore[arg-type]
        tool_registry=registry,
        config=config or OBDAgentConfig(
            max_iterations=5,
            timeout_seconds=30.0,
        ),
    )
    return deps, client


# ── Registry isolation ───────────────────────────────────────────


class TestOBDAgentRegistry:
    """Tests for ``create_obd_agent_registry``."""

    def test_registers_exactly_six_obd_primitives(self) -> None:
        registry = create_obd_agent_registry()
        assert set(registry.tool_names) == {
            "list_signals",
            "read_window",
            "get_signal_stats",
            "find_events",
            "list_dtcs",
            "lookup_dtc",
        }

    def test_does_not_include_delegation_tools(self) -> None:
        """Recursion guard: sub-agent can't delegate to itself."""
        registry = create_obd_agent_registry()
        assert "delegate_to_obd_agent" not in registry.tool_names
        assert "delegate_to_manual_agent" not in registry.tool_names

    def test_does_not_include_manual_tools(self) -> None:
        """OBD sub-agent has no manual access by design."""
        registry = create_obd_agent_registry()
        for manual_tool in (
            "list_manuals",
            "get_manual_toc",
            "read_manual_section",
            "search_manual",
        ):
            assert manual_tool not in registry.tool_names


# ── Config defaults ──────────────────────────────────────────────


class TestOBDAgentConfigDefaults:
    """Pin the default-config values that affect eval behaviour."""

    def test_default_timeout_is_240_seconds(self) -> None:
        """HARNESS-21 [2a/4] bumped 120 → 240 after observing
        53s / 62s / 120s+ variance on the same question against
        qwen3.5:27b-q8_0.  Hidden chain-of-thought dominates wall
        clock; 240s gives comfortable headroom.

        Tighten only when (a) we move to a non-thinking model,
        (b) baseline shows deterministic convergence under N
        seconds, or (c) we enable Ollama's ``"think": false``
        via extra_body.
        """
        assert OBDAgentConfig().timeout_seconds == 240.0

    def test_default_max_iterations_is_8(self) -> None:
        """Iteration cap is independent of timeout — pinning so
        a sloppy edit to one doesn't accidentally change the
        other."""
        assert OBDAgentConfig().max_iterations == 8


# ── Final JSON parsing ───────────────────────────────────────────


class TestParseFinalJSON:
    """Tests for ``_parse_final_json``."""

    def test_parses_well_formed_payload(self) -> None:
        content = json.dumps({
            "summary": "RPM ranged 0-3906",
            "signal_citations": [
                {
                    "signal": "RPM",
                    "value": 3906,
                    "stat": "max",
                    "units": "rpm",
                },
            ],
            "dtc_citations": [
                {
                    "code": "87F11043000000000000CB",
                    "status": "stored",
                    "ecu": "K-Line",
                },
            ],
            "raw_data": [],
            "limitations": ["Yamaha hex DTC not decodable"],
        })
        summary, sig_cits, dtc_cits, _, lims = _parse_final_json(
            content,
        )
        assert summary == "RPM ranged 0-3906"
        assert len(sig_cits) == 1
        assert sig_cits[0].signal == "RPM"
        assert sig_cits[0].value == pytest.approx(3906)
        assert len(dtc_cits) == 1
        assert dtc_cits[0].code == "87F11043000000000000CB"
        assert dtc_cits[0].status == "stored"
        assert lims == ["Yamaha hex DTC not decodable"]

    def test_handles_markdown_fence(self) -> None:
        """LLMs sometimes wrap output in ```json — must strip."""
        content = "```json\n" + json.dumps({
            "summary": "test",
            "signal_citations": [],
            "dtc_citations": [],
            "raw_data": [],
            "limitations": [],
        }) + "\n```"
        summary, *_ = _parse_final_json(content)
        assert summary == "test"

    def test_handles_leading_prose_then_json(self) -> None:
        """Extracts the first {...} block when JSON is embedded."""
        content = (
            "Here is my answer:\n"
            + json.dumps({
                "summary": "embedded",
                "signal_citations": [],
                "dtc_citations": [],
                "raw_data": [],
                "limitations": [],
            })
            + "\nLet me know if you need more."
        )
        summary, *_ = _parse_final_json(content)
        assert summary == "embedded"

    def test_falls_back_to_raw_content_on_parse_failure(
        self,
    ) -> None:
        """Garbage content becomes the summary, empty citations."""
        summary, sigs, dtcs, _, lims = _parse_final_json(
            "totally not json",
        )
        assert "totally not json" in summary
        assert sigs == []
        assert dtcs == []
        assert lims == []

    def test_empty_content_returns_placeholder(self) -> None:
        summary, *_ = _parse_final_json(None)
        assert "no final content" in summary.lower()

    def test_strip_markdown_fence_handles_no_fence(self) -> None:
        assert _strip_markdown_fence("hello") == "hello"

    def test_strip_markdown_fence_handles_json_fence(self) -> None:
        assert _strip_markdown_fence("```json\nfoo\n```") == "foo"


class TestCoerceCitations:
    """Defensive coercion of LLM-supplied citation arrays."""

    def test_signal_citation_drops_entries_missing_signal(self) -> None:
        out = _coerce_signal_citations([
            {"signal": "RPM", "value": 1.0},
            {"value": 2.0},  # missing signal — drop
        ])
        assert len(out) == 1
        assert out[0].signal == "RPM"

    def test_dtc_citation_requires_valid_status(self) -> None:
        out = _coerce_dtc_citations([
            {"code": "P0117", "status": "stored"},
            {"code": "P0118", "status": "WHATEVER"},  # drop
        ])
        assert len(out) == 1
        assert out[0].status == "stored"

    def test_limitations_coerces_strings(self) -> None:
        out = _coerce_limitations(["a", "b", 42])
        assert out == ["a", "b", "42"]

    def test_limitations_handles_single_string(self) -> None:
        out = _coerce_limitations("only one")
        assert out == ["only one"]


# ── Data excerpt capture ─────────────────────────────────────────


class TestBuildDataExcerpt:
    """Tests for ``_build_data_excerpt``."""

    def test_skips_non_excerpt_tools(self) -> None:
        """list_signals and lookup_dtc don't produce excerpts."""
        assert _build_data_excerpt(
            "list_signals", "anything",
        ) is None
        assert _build_data_excerpt(
            "lookup_dtc", "anything",
        ) is None

    def test_captures_stats_excerpt(self) -> None:
        ex = _build_data_excerpt(
            "get_signal_stats",
            "Signal stats: mean=1820 rpm",
        )
        assert ex is not None
        assert ex.kind == "stats"
        assert "mean=1820" in ex.payload["text"]

    def test_captures_events_excerpt(self) -> None:
        ex = _build_data_excerpt(
            "find_events",
            "Event #1 at 11:21:30",
        )
        assert ex is not None
        assert ex.kind == "events"

    def test_captures_window_excerpt(self) -> None:
        ex = _build_data_excerpt(
            "read_window",
            "Timestamp\tRPM\n...",
        )
        assert ex is not None
        assert ex.kind == "window"

    def test_captures_dtcs_excerpt(self) -> None:
        ex = _build_data_excerpt(
            "list_dtcs",
            "DTC list: ...",
        )
        assert ex is not None
        assert ex.kind == "dtcs"


# ── End-to-end loop ──────────────────────────────────────────────


SESSION_ID = "11111111-2222-3333-4444-555555555555"


class TestRunOBDAgentEndToEnd:
    """End-to-end ReAct loop tests with scripted LLM."""

    @pytest.mark.asyncio
    async def test_completes_when_llm_returns_final_json(
        self,
    ) -> None:
        deps, _ = _make_deps([
            _final_response(summary="all good"),
        ])
        result = await run_obd_agent(
            "investigate", SESSION_ID, deps,
        )
        assert isinstance(result, OBDAgentResult)
        assert result.summary == "all good"
        assert result.stopped_reason == "complete"
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_dispatches_tool_call_and_returns(
        self,
    ) -> None:
        """LLM asks for a tool, then returns final answer."""
        deps, client = _make_deps(
            [
                _tool_call_response([
                    {"name": "list_signals", "arguments": {}},
                ]),
                _final_response(
                    summary="found signals",
                    signal_citations=[
                        {
                            "signal": "RPM",
                            "stat": "max",
                            "value": 3906,
                            "units": "rpm",
                        },
                    ],
                ),
            ],
            tool_outputs={
                "list_signals": "RPM, COOLANT_TEMP, ...",
            },
        )
        result = await run_obd_agent(
            "what signals exist?", SESSION_ID, deps,
        )
        assert result.stopped_reason == "complete"
        assert len(result.tool_trace) == 1
        assert result.tool_trace[0].name == "list_signals"
        assert len(result.signal_citations) == 1

    @pytest.mark.asyncio
    async def test_captures_raw_data_excerpts_from_tool_outputs(
        self,
    ) -> None:
        """get_signal_stats output becomes a DataExcerpt."""
        deps, _ = _make_deps(
            [
                _tool_call_response([
                    {
                        "name": "get_signal_stats",
                        "arguments": {"signals": ["RPM"]},
                    },
                ]),
                _final_response(summary="done"),
            ],
            tool_outputs={
                "get_signal_stats": "RPM: mean=1820 rpm",
            },
        )
        result = await run_obd_agent(
            "summarize RPM", SESSION_ID, deps,
        )
        assert any(
            e.kind == "stats" for e in result.raw_data
        )

    @pytest.mark.asyncio
    async def test_session_id_injected_into_tool_calls(
        self,
    ) -> None:
        """Sub-agent loop must inject _session_id like the main loop."""
        seen_args: List[Dict[str, Any]] = []

        async def capture(input_data: Dict[str, Any]) -> str:
            seen_args.append(dict(input_data))
            return "captured"

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="list_signals",
            description="mock",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
            handler=capture,
            is_read_only=True,
        ))

        client = _ScriptedLLMClient([
            _tool_call_response([
                {"name": "list_signals", "arguments": {}},
            ]),
            _final_response(),
        ])
        deps = OBDAgentDeps(
            llm_client=client,  # type: ignore[arg-type]
            tool_registry=registry,
            config=OBDAgentConfig(
                max_iterations=3, timeout_seconds=10.0,
            ),
        )
        await run_obd_agent("test", SESSION_ID, deps)

        assert seen_args
        assert seen_args[0].get("_session_id") == SESSION_ID

    @pytest.mark.asyncio
    async def test_max_iterations_records_partial_result(
        self,
    ) -> None:
        """Iteration cap reached → graceful partial result."""
        # Two tool-call responses, no final response — loop hits
        # max_iterations=2.
        deps, _ = _make_deps(
            [
                _tool_call_response([
                    {"name": "list_signals", "arguments": {}},
                ]),
                _tool_call_response([
                    {"name": "list_signals", "arguments": {}},
                ]),
            ],
            config=OBDAgentConfig(
                max_iterations=2, timeout_seconds=10.0,
            ),
        )
        result = await run_obd_agent(
            "spin forever", SESSION_ID, deps,
        )
        assert result.stopped_reason == "max_iterations"
        # Limitations should mention the cap.
        assert any(
            "iteration" in lim.lower()
            for lim in result.limitations
        )

    @pytest.mark.asyncio
    async def test_llm_error_records_error_stopped_reason(
        self,
    ) -> None:
        deps, _ = _make_deps([RuntimeError("LLM exploded")])
        result = await run_obd_agent(
            "test", SESSION_ID, deps,
        )
        assert result.stopped_reason == "error"
        # Should still produce a usable summary.
        assert result.summary

    @pytest.mark.asyncio
    async def test_tool_argument_parse_error_surfaces_to_llm(
        self,
    ) -> None:
        """Malformed tool args don't crash — return error to LLM."""
        registry = _build_mock_registry()
        client = _ScriptedLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallInfo(
                        id="tc_bad",
                        name="list_signals",
                        arguments="not json",
                    ),
                ],
                finish_reason="tool_calls",
            ),
            _final_response(summary="recovered"),
        ])
        deps = OBDAgentDeps(
            llm_client=client,  # type: ignore[arg-type]
            tool_registry=registry,
            config=OBDAgentConfig(
                max_iterations=3, timeout_seconds=10.0,
            ),
        )
        result = await run_obd_agent(
            "test", SESSION_ID, deps,
        )
        # Should complete despite the parse failure.
        assert result.stopped_reason == "complete"
        # Tool trace must record the failed call as an error.
        assert any(t.is_error for t in result.tool_trace)


class TestParseToolArguments:
    """Edge cases for the JSON-args parser."""

    def test_valid_json_object(self) -> None:
        assert _parse_tool_arguments('{"a": 1}') == {"a": 1}

    def test_empty_string_returns_empty_dict(self) -> None:
        assert _parse_tool_arguments("") == {}

    def test_non_object_returns_parse_error(self) -> None:
        out = _parse_tool_arguments("[1, 2, 3]")
        assert "_parse_error" in out

    def test_invalid_json_returns_parse_error(self) -> None:
        out = _parse_tool_arguments("not json")
        assert "_parse_error" in out

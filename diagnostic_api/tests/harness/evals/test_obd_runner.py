"""Unit tests for the OBD eval runner + adapter (HARNESS-21).

Covers two surfaces:

1. ``_obd_result_to_system_run`` + ``_serialize_output_text`` —
   structural adapter from ``OBDAgentResult`` to the unified
   ``SystemRunResult`` shape.  No LLM involvement.

2. ``_build_default_deps`` — environment-driven client/config
   selection.  Tests monkeypatch ``OBD_EVAL_AGENT_MODEL`` and
   ``settings`` to assert the right wiring happens without
   actually making network calls.

A small scripted-LLM happy-path test for ``run_obd_agent_unified``
proves the timing + adaptation pipeline composes end-to-end.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.harness.deps import LLMResponse, ToolCallInfo
from app.harness_agents.obd_agent import (
    OBDAgentConfig,
    OBDAgentDeps,
    create_obd_agent_registry,
)
from app.harness_agents.types import (
    DataExcerpt,
    DTCCitation,
    OBDAgentResult,
    SignalCitation,
    ToolCallTrace,
)
from tests.harness.evals import obd_runner
from tests.harness.evals.obd_runner import (
    _build_default_deps,
    _format_dtc_citation,
    _format_signal_citation,
    _obd_result_to_system_run,
    _reset_cache_for_testing,
    _serialize_output_text,
    run_obd_agent_unified,
)


# ── Scripted LLM client (mirrors test_obd_agent.py) ──────────────


class _ScriptedLLMClient:
    """Minimal LLMClient replaying pre-queued responses."""

    def __init__(self, responses: List[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError(
                "ScriptedLLMClient exhausted — test queued too "
                "few responses",
            )
        return self._responses.pop(0)


def _final_response(
    summary: str = "Test summary.",
    signal_citations: Optional[List[Dict[str, Any]]] = None,
    dtc_citations: Optional[List[Dict[str, Any]]] = None,
    limitations: Optional[List[str]] = None,
) -> LLMResponse:
    """Build a final-JSON LLM response (no tool calls)."""
    return LLMResponse(
        content=json.dumps({
            "summary": summary,
            "signal_citations": signal_citations or [],
            "dtc_citations": dtc_citations or [],
            "raw_data": [],
            "limitations": limitations or [],
        }),
        tool_calls=[],
        finish_reason="stop",
    )


# ── _format_* helpers ────────────────────────────────────────────


class TestFormatSignalCitation:
    """Verify the human-readable line shape."""

    def test_signal_only(self):
        c = SignalCitation(signal="RPM")
        assert _format_signal_citation(c) == "RPM"

    def test_signal_with_stat(self):
        c = SignalCitation(signal="RPM", stat="p95")
        assert _format_signal_citation(c) == "RPM (p95)"

    def test_signal_with_value_and_units(self):
        c = SignalCitation(
            signal="RPM", stat="p95", value=2941.0, units="rpm",
        )
        assert _format_signal_citation(c) == "RPM (p95) = 2941.0 rpm"

    def test_signal_with_time_range(self):
        c = SignalCitation(
            signal="COOLANT_TEMP",
            stat="max",
            value=84.0,
            units="°C",
            time_range=(
                "2026-05-08T11:23:12", "2026-05-08T11:24:01",
            ),
        )
        line = _format_signal_citation(c)
        assert "COOLANT_TEMP" in line
        assert "max" in line
        assert "84.0" in line
        assert "2026-05-08T11:23:12" in line


class TestFormatDtcCitation:
    """Verify the DTC line shape."""

    def test_dtc_with_ecu(self):
        c = DTCCitation(
            code="87F11043", status="stored", ecu="K-Line",
        )
        assert _format_dtc_citation(c) == "87F11043 (stored, K-Line)"

    def test_dtc_without_ecu(self):
        c = DTCCitation(code="P0117", status="pending")
        assert _format_dtc_citation(c) == "P0117 (pending)"


# ── _serialize_output_text ───────────────────────────────────────


class TestSerializeOutputText:
    """End-to-end formatting on ``OBDAgentResult`` shapes."""

    def test_summary_only(self):
        """Result with only a summary → just the summary."""
        result = OBDAgentResult(summary="Engine is healthy.")
        text = _serialize_output_text(result)
        assert text == "Engine is healthy."
        assert "---" not in text  # No block headers.

    def test_summary_plus_signals(self):
        result = OBDAgentResult(
            summary="Peak RPM was 3906.",
            signal_citations=[
                SignalCitation(
                    signal="RPM", stat="max", value=3906.0,
                ),
            ],
        )
        text = _serialize_output_text(result)
        assert "Peak RPM was 3906." in text
        assert "--- Signal citations (1) ---" in text
        assert "RPM (max) = 3906.0" in text

    def test_full_blocks(self):
        """Summary + signals + DTCs + limitations."""
        result = OBDAgentResult(
            summary="See evidence below.",
            signal_citations=[
                SignalCitation(signal="RPM", stat="max", value=3906.0),
                SignalCitation(signal="SPEED", stat="mean", value=6.2),
            ],
            dtc_citations=[
                DTCCitation(
                    code="87F11043", status="stored", ecu="K-Line",
                ),
            ],
            limitations=["No Yamaha hex decoder available."],
        )
        text = _serialize_output_text(result)
        assert "--- Signal citations (2) ---" in text
        assert "--- DTC citations (1) ---" in text
        assert "--- Limitations ---" in text
        assert "- No Yamaha hex decoder available." in text


# ── _obd_result_to_system_run ────────────────────────────────────


class TestObdResultToSystemRun:
    """Mapping from ``OBDAgentResult`` to ``SystemRunResult``."""

    def _sample_result(self) -> OBDAgentResult:
        return OBDAgentResult(
            summary="The engine ran cleanly.",
            signal_citations=[
                SignalCitation(
                    signal="RPM", stat="max", value=3906.0,
                ),
            ],
            dtc_citations=[
                DTCCitation(
                    code="87F11043", status="stored", ecu="K-Line",
                ),
            ],
            raw_data=[
                DataExcerpt(
                    kind="stats", payload={"text": "..."},
                ),
            ],
            limitations=[],
            tool_trace=[
                ToolCallTrace(
                    name="get_signal_stats",
                    input={"signal": "RPM"},
                    latency_ms=123.4,
                    is_error=False,
                ),
            ],
            iterations=2,
            stopped_reason="complete",
        )

    def test_system_label_is_obd_agent(self):
        run = _obd_result_to_system_run(
            question="Peak RPM?",
            result=self._sample_result(),
            latency_ms_wall=456.0,
        )
        assert run.system_label == "obd_agent"

    def test_slug_fields_always_empty(self):
        """OBD has no slug concept — slug fields stay [] so the
        manual-lane slug metrics short-circuit cleanly in the
        dispatcher."""
        run = _obd_result_to_system_run(
            question="Peak RPM?",
            result=self._sample_result(),
            latency_ms_wall=456.0,
        )
        assert run.claim_slugs == []
        assert run.read_slugs == []
        assert run.retrieved_chunk_metadata == []

    def test_obd_citations_passed_through(self):
        run = _obd_result_to_system_run(
            question="Peak RPM?",
            result=self._sample_result(),
            latency_ms_wall=456.0,
        )
        assert len(run.obd_signal_citations) == 1
        assert run.obd_signal_citations[0].signal == "RPM"
        assert run.obd_signal_citations[0].value == 3906.0
        assert len(run.obd_dtc_citations) == 1
        assert run.obd_dtc_citations[0].code == "87F11043"

    def test_tool_trace_and_diagnostics_pass_through(self):
        run = _obd_result_to_system_run(
            question="Peak RPM?",
            result=self._sample_result(),
            latency_ms_wall=456.0,
        )
        assert len(run.tool_trace) == 1
        assert run.iterations == 2
        assert run.stopped_reason == "complete"

    def test_wall_latency_captured(self):
        run = _obd_result_to_system_run(
            question="q",
            result=self._sample_result(),
            latency_ms_wall=789.0,
        )
        assert run.latency_ms_wall == pytest.approx(789.0)

    def test_llm_latency_proxy_sums_tool_calls(self):
        """``latency_ms_llm`` is the sum of ``tool_trace[].latency_ms``."""
        run = _obd_result_to_system_run(
            question="q",
            result=self._sample_result(),
            latency_ms_wall=789.0,
        )
        # Single tool call with 123.4ms latency in the fixture.
        assert run.latency_ms_llm == pytest.approx(123.4)

    def test_output_text_contains_summary_and_citations(self):
        """Smoke: the serialized output_text bundles everything
        the judge needs."""
        run = _obd_result_to_system_run(
            question="q",
            result=self._sample_result(),
            latency_ms_wall=1.0,
        )
        assert "The engine ran cleanly." in run.output_text
        assert "--- Signal citations (1) ---" in run.output_text
        assert "--- DTC citations (1) ---" in run.output_text


# ── _build_default_deps ──────────────────────────────────────────


class TestBuildDefaultDeps:
    """Env-driven Ollama vs OpenRouter client selection.

    Each test resets the module cache so cached deps from a prior
    test don't leak.
    """

    def setup_method(self):
        _reset_cache_for_testing()

    def test_ollama_default_when_env_unset(self, monkeypatch):
        """No env var → Ollama path, default model."""
        monkeypatch.delenv("OBD_EVAL_AGENT_MODEL", raising=False)
        deps = _build_default_deps()
        # Default model comes from OBDAgentConfig() defaults.
        assert deps.config.model == OBDAgentConfig().model
        # Registry has exactly the 6 OBD tools.
        assert len(deps.tool_registry.schemas) == 6

    def test_ollama_path_with_plain_model_tag(self, monkeypatch):
        """Plain Ollama tag (no slash) → Ollama path with that
        model name."""
        monkeypatch.setenv("OBD_EVAL_AGENT_MODEL", "qwen2.5:7b")
        deps = _build_default_deps()
        assert deps.config.model == "qwen2.5:7b"

    def test_openrouter_path_when_slash_in_env(self, monkeypatch):
        """OpenRouter-style identifier (contains slash) → uses
        premium client base URL."""
        monkeypatch.setenv(
            "OBD_EVAL_AGENT_MODEL", "z-ai/glm-5.1",
        )
        # Provide a fake API key so the build doesn't raise.
        monkeypatch.setattr(
            obd_runner.settings, "premium_llm_api_key", "fake-key",
        )
        deps = _build_default_deps()
        assert deps.config.model == "z-ai/glm-5.1"

    def test_openrouter_raises_without_api_key(self, monkeypatch):
        """OpenRouter path requires premium_llm_api_key."""
        monkeypatch.setenv(
            "OBD_EVAL_AGENT_MODEL", "z-ai/glm-5.1",
        )
        monkeypatch.setattr(
            obd_runner.settings, "premium_llm_api_key", "",
        )
        with pytest.raises(RuntimeError) as exc:
            _build_default_deps()
        assert "PREMIUM_LLM_API_KEY" in str(exc.value)

    def test_cache_reset_helper(self, monkeypatch):
        """``_reset_cache_for_testing`` lets sequential tests with
        different env vars build distinct deps."""
        monkeypatch.delenv("OBD_EVAL_AGENT_MODEL", raising=False)
        first = obd_runner._get_default_deps()
        _reset_cache_for_testing()
        monkeypatch.setenv("OBD_EVAL_AGENT_MODEL", "ollama-test")
        second = obd_runner._get_default_deps()
        assert first is not second
        assert second.config.model == "ollama-test"


# ── End-to-end: run_obd_agent_unified ────────────────────────────


class TestRunObdAgentUnified:
    """Happy-path end-to-end with a scripted LLM."""

    @pytest.mark.asyncio
    async def test_scripted_run_produces_system_run_result(self):
        """The runner times the call, invokes the agent, adapts
        the result, returns a fully-populated ``SystemRunResult``."""
        scripted = _ScriptedLLMClient([
            _final_response(
                summary="Peak RPM was 3906.",
                signal_citations=[
                    {"signal": "RPM", "stat": "max", "value": 3906.0},
                ],
            ),
        ])
        deps = OBDAgentDeps(
            llm_client=scripted,
            tool_registry=create_obd_agent_registry(),
            config=OBDAgentConfig(),
        )
        run = await run_obd_agent_unified(
            inquiry="What was the peak engine RPM?",
            session_id="00000000-0000-0000-0000-000000000000",
            deps=deps,
        )
        assert run.system_label == "obd_agent"
        assert "Peak RPM was 3906." in run.output_text
        assert len(run.obd_signal_citations) == 1
        assert run.obd_signal_citations[0].signal == "RPM"
        assert run.latency_ms_wall > 0
        assert run.stopped_reason == "complete"

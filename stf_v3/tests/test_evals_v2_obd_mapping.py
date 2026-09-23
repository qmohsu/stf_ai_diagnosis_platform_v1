# Copied from diagnostic_api/tests/harness/evals/test_obd_runner.py @ 9c9e7a9
# (PROD-10): the result-mapping tests only (the V2 deps / Ollama client
# tests have no V3 counterpart -- V3 runs the production sub-agent).
"""V2 OBD result → ``SystemRunResult`` mapping tests, run against the V3 copy.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import pytest

from stf_v3.evals.lanes import (
    _format_dtc_citation,
    _format_signal_citation,
    _obd_result_to_system_run,
    _serialize_output_text,
)
from stf_v3.evals.schemas import (
    DTCCitation,
    OBDAgentResult,
    SignalCitation,
    ToolCallTrace,
)
from stf_v3.diagnosis.agent.types import DataExcerpt


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



"""Unit tests for the LLM-as-judge wrapper.

Uses a fake ``AsyncOpenAI``-like client so tests run without
network access or API keys.  Covers: happy path, JSON parse
retry, schema validation retry, double-failure fallback, prompt
construction, and the ``--run-eval`` gate's mocked-judge fixture.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.harness.evals.judge import (
    _JUDGE_MODEL,
    _JUDGE_TEMPERATURE,
    _parse_grade,
    judge_result,
)
from tests.harness.evals.judge_prompts import (
    JUDGE_SYSTEM_PROMPT,
    build_user_prompt,
)
from tests.harness.evals.schemas import (
    Citation,
    GoldenCitation,
    GoldenEntry,
    Grade,
    ManualAgentResult,
    SectionRef,
    ToolCallTrace,
)


# ── Fixtures ──────────────────────────────────────────────────────


def _sample_entry(
    *,
    must_contain: Optional[List[str]] = None,
) -> GoldenEntry:
    """Build a minimal valid golden entry for tests."""
    return GoldenEntry(
        id="mws150a-dtc-p0171-test",
        category="dtc",
        difficulty="easy",
        question="MWS-150-A shows DTC P0171. What does this mean?",
        obd_context="Vehicle: MWS-150-A. DTCs: P0171.",
        golden_summary=(
            "P0171 indicates a system-too-lean condition on "
            "bank 1.  The MWS-150-A manual directs the "
            "technician to inspect the intake manifold for "
            "vacuum leaks and measure fuel pressure at the "
            "rail."
        ),
        golden_citations=[
            GoldenCitation(
                manual_id="MWS150A_Service_Manual",
                slug="3-2-fuel-system-troubleshooting",
                quote="P0171 lean condition",
            ),
        ],
        expected_tool_trace=[
            "get_manual_toc", "read_manual_section",
        ],
        must_contain=must_contain or ["P0171", "fuel"],
        must_not_contain=["P0300"],
        requires_image=False,
        notes="unit-test fixture",
    )


def _sample_result() -> ManualAgentResult:
    """Build a minimal valid agent result for tests."""
    return ManualAgentResult(
        summary=(
            "DTC P0171 on the MWS-150-A indicates a "
            "system-too-lean fault.  Inspect the fuel system "
            "per the manual."
        ),
        citations=[
            Citation(
                manual_id="MWS150A_Service_Manual",
                slug="3-2-fuel-system-troubleshooting",
                quote="P0171 lean condition",
            ),
        ],
        raw_sections=[
            SectionRef(
                manual_id="MWS150A_Service_Manual",
                slug="3-2-fuel-system-troubleshooting",
                text=(
                    "P0171 lean condition.  Check intake "
                    "manifold for vacuum leaks.  Measure fuel "
                    "pressure at rail."
                ),
                had_images=False,
            ),
        ],
        tool_trace=[
            ToolCallTrace(
                name="get_manual_toc",
                input={"manual_id": "MWS150A_Service_Manual"},
                latency_ms=12.0,
            ),
            ToolCallTrace(
                name="read_manual_section",
                input={
                    "manual_id": "MWS150A_Service_Manual",
                    "section": "3-2-fuel-system-troubleshooting",
                },
                latency_ms=18.5,
            ),
        ],
        iterations=2,
        total_tokens=1234,
        stopped_reason="complete",
    )


class _FakeClient:
    """Minimal async stand-in for ``AsyncOpenAI``.

    Returns responses from a scripted queue.  A response can be
    a raw string (wrapped into a choice) or an ``Exception`` to
    raise.  Records every call for later assertions.
    """

    def __init__(
        self,
        responses: List[Union[str, Exception]],
    ) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []
        # Nested namespace mirrors the real SDK shape:
        # client.chat.completions.create(...)
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = AsyncMock(
            side_effect=self._respond,
        )

    async def _respond(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError(
                "FakeClient exhausted — test queued "
                "too few responses",
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        # Build a minimal completion-like object.
        msg = MagicMock()
        msg.content = nxt
        choice = MagicMock()
        choice.message = msg
        completion = MagicMock()
        completion.choices = [choice]
        return completion


def _ok_json() -> str:
    """Return a valid judge-response JSON payload."""
    return json.dumps({
        "section_match": 1,
        "fact_recall": 1.0,
        "hallucination": 0,
        "citation_present": 1,
        "trajectory_ok": 1,
        "overall": 1.0,
        "reasoning": (
            "Agent cited the golden slug and must_contain "
            "terms appear in the summary.  Trajectory "
            "matched expected tools."
        ),
    })


# ── Prompt builder tests ──────────────────────────────────────────


class TestBuildUserPrompt:
    """Tests for ``build_user_prompt`` — pure function."""

    def test_includes_core_fields(self) -> None:
        """Prompt contains question, golden summary, and agent output."""
        entry = _sample_entry()
        result = _sample_result()
        prompt = build_user_prompt(entry, result)
        assert entry.question in prompt
        assert entry.golden_summary in prompt
        assert result.summary in prompt

    def test_includes_golden_citations(self) -> None:
        """Golden citation slugs are rendered in the prompt."""
        entry = _sample_entry()
        result = _sample_result()
        prompt = build_user_prompt(entry, result)
        assert "3-2-fuel-system-troubleshooting" in prompt
        assert "MWS150A_Service_Manual" in prompt

    def test_truncates_long_raw_sections(self) -> None:
        """Raw section text is capped at _MAX_SECTION_CHARS."""
        entry = _sample_entry()
        result = _sample_result()
        result.raw_sections[0].text = "X" * 10_000
        prompt = build_user_prompt(entry, result)
        assert "[truncated" in prompt
        # Full 10K chars should not be present verbatim.
        assert "X" * 10_000 not in prompt

    def test_handles_empty_citations(self) -> None:
        """Empty citation lists render as '(none)' placeholder."""
        entry = _sample_entry()
        entry.golden_citations = []
        result = _sample_result()
        result.citations = []
        prompt = build_user_prompt(entry, result)
        assert "(none)" in prompt

    def test_includes_must_contain_guidance(self) -> None:
        """must_contain and must_not_contain appear in prompt."""
        entry = _sample_entry(must_contain=["P0171", "fuel"])
        result = _sample_result()
        prompt = build_user_prompt(entry, result)
        assert "P0171" in prompt
        assert "P0300" in prompt  # from must_not_contain

    def test_renders_tool_trace_order_and_counts(self) -> None:
        """Tool trace shows both order and per-tool counts."""
        entry = _sample_entry()
        result = _sample_result()
        prompt = build_user_prompt(entry, result)
        assert "get_manual_toc" in prompt
        assert "read_manual_section" in prompt
        assert "order:" in prompt
        assert "counts:" in prompt


# ── Parse helper tests ────────────────────────────────────────────


class TestParseGrade:
    """Tests for ``_parse_grade``."""

    def test_valid_json_returns_grade(self) -> None:
        """Well-formed JSON parses to a Grade."""
        grade = _parse_grade(_ok_json())
        assert isinstance(grade, Grade)
        assert grade.overall == 1.0

    def test_invalid_json_raises_value_error(self) -> None:
        """Malformed JSON raises ValueError."""
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_grade("not json {")

    def test_schema_violation_raises_value_error(self) -> None:
        """Missing required fields raise ValueError."""
        bad = json.dumps({"section_match": 1})  # missing fields
        with pytest.raises(ValueError, match="failed schema"):
            _parse_grade(bad)

    def test_out_of_range_value_raises(self) -> None:
        """Values outside [0, 1] fail Pydantic validation."""
        bad = json.dumps({
            "section_match": 1,
            "fact_recall": 1.5,  # > 1.0
            "hallucination": 0,
            "citation_present": 1,
            "trajectory_ok": 1,
            "overall": 1.0,
            "reasoning": "x",
        })
        with pytest.raises(ValueError, match="failed schema"):
            _parse_grade(bad)


# ── judge_result happy path ───────────────────────────────────────


class TestJudgeResultHappyPath:
    """End-to-end judge flow with a valid first response."""

    @pytest.mark.asyncio
    async def test_single_call_returns_grade(self) -> None:
        """One judge call, valid JSON, returns Grade."""
        client = _FakeClient([_ok_json()])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 1.0
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_call_uses_pinned_model_and_temperature(
        self,
    ) -> None:
        """Judge request uses GLM 5.1 at temp 0 with JSON mode."""
        client = _FakeClient([_ok_json()])
        await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        kwargs = client.calls[0]
        assert kwargs["model"] == _JUDGE_MODEL
        assert kwargs["temperature"] == _JUDGE_TEMPERATURE
        assert kwargs["response_format"] == {
            "type": "json_object",
        }

    @pytest.mark.asyncio
    async def test_system_and_user_messages_sent(self) -> None:
        """System prompt + user prompt are both present."""
        client = _FakeClient([_ok_json()])
        await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        messages = client.calls[0]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == JUDGE_SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        # User prompt should contain at least the question.
        assert "P0171" in messages[1]["content"]


# ── Retry behavior ────────────────────────────────────────────────


class TestJudgeRetry:
    """Retry + fallback paths."""

    @pytest.mark.asyncio
    async def test_parse_retry_succeeds(self) -> None:
        """Malformed first response, corrected on retry."""
        client = _FakeClient([
            "this is not json at all",
            _ok_json(),
        ])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 1.0
        # Retry sends 4-message history (sys + user +
        # assistant + corrective user).
        assert len(client.calls) == 2
        retry_messages = client.calls[1]["messages"]
        assert len(retry_messages) == 4
        assert retry_messages[-1]["role"] == "user"
        assert "previous response" in retry_messages[-1][
            "content"
        ].lower()

    @pytest.mark.asyncio
    async def test_double_parse_failure_returns_fallback(
        self,
    ) -> None:
        """Two malformed responses -> zero-score fallback."""
        client = _FakeClient([
            "not json",
            "still not json",
        ])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 0.0
        assert grade.section_match == 0
        assert grade.reasoning.startswith("[judge failure]")
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_schema_failure_retries(self) -> None:
        """Schema-invalid first response triggers retry."""
        bad = json.dumps({"section_match": 1})  # missing fields
        client = _FakeClient([bad, _ok_json()])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 1.0
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_api_error_first_try_retries(self) -> None:
        """Transient API error on first call -> retry once."""
        client = _FakeClient([
            RuntimeError("transient 502"),
            _ok_json(),
        ])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 1.0
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_api_error_both_tries_returns_fallback(
        self,
    ) -> None:
        """Two API failures -> zero-score fallback."""
        client = _FakeClient([
            RuntimeError("transient 502"),
            RuntimeError("still 502"),
        ])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 0.0
        assert grade.reasoning.startswith("[judge failure]")
        assert "api error" in grade.reasoning

    @pytest.mark.asyncio
    async def test_api_error_on_retry_call_returns_fallback(
        self,
    ) -> None:
        """Parse-retry that itself errors -> fallback."""
        client = _FakeClient([
            "not json",  # trigger parse retry
            RuntimeError("retry 502"),  # retry call fails
        ])
        grade = await judge_result(
            _sample_entry(), _sample_result(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.overall == 0.0
        assert grade.reasoning.startswith("[judge failure]")


# ── Edge cases ────────────────────────────────────────────────────


class TestJudgeEdgeCases:
    """Assorted edge cases that shouldn't crash the judge."""

    @pytest.mark.asyncio
    async def test_empty_citations_result_still_graded(
        self,
    ) -> None:
        """Result with no citations still produces a grade."""
        client = _FakeClient([_ok_json()])
        result = _sample_result()
        result.citations = []
        grade = await judge_result(
            _sample_entry(), result,
            client=client,  # type: ignore[arg-type]
        )
        assert isinstance(grade, Grade)

    @pytest.mark.asyncio
    async def test_empty_raw_sections_result_still_graded(
        self,
    ) -> None:
        """Result with no raw sections still produces a grade."""
        client = _FakeClient([_ok_json()])
        result = _sample_result()
        result.raw_sections = []
        grade = await judge_result(
            _sample_entry(), result,
            client=client,  # type: ignore[arg-type]
        )
        assert isinstance(grade, Grade)

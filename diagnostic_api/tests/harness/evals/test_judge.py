"""Unit tests for the LLM-as-judge wrapper.

Uses a fake ``AsyncOpenAI``-like client so tests run without
network access or API keys.  Covers: prompt construction
(including the #146 ANSWERABILITY block that credits a correct
adversarial decline), payload parsing, retry / fallback paths of
``rate_quality_and_pitfalls``, and end-to-end ``grade_run``
plumbing for both a correct decline and a fabricated answer on a
no-evidence entry.

Rewritten for the HARNESS-15 (#74) judge API — the previous
version of this file targeted the retired ``judge_result`` /
``_parse_grade`` interface and blocked collection with an
ImportError (noted in ``test_judge_obd.py``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Union
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.harness.evals.judge import (
    _JUDGE_MODEL,
    _JUDGE_TEMPERATURE,
    _parse_judge_payload,
    grade_run,
    rate_quality_and_pitfalls,
)
from tests.harness.evals.judge_prompts import (
    JUDGE_SYSTEM_PROMPT,
    _is_image_required_entry,
    _is_no_evidence_entry,
    build_user_prompt,
    classify_pitfall_directive,
)
from tests.harness.evals.runner import (
    _agent_result_to_system_run,
    _extract_surfaced_images,
)
from tests.harness.evals.schemas import (
    Citation,
    GoldenCitation,
    GoldenEntry,
    Grade,
    ManualAgentResult,
    SectionRef,
    SurfacedImage,
    SystemRunResult,
)


# ── Fixtures ──────────────────────────────────────────────────────


def _sample_entry() -> GoldenEntry:
    """Build a minimal valid manual-lane golden entry."""
    return GoldenEntry(
        id="mws150a-dtc-p0171-test",
        category="dtc",
        question_type="lookup",
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
        expected_recall_slugs=["3-2-fuel-system-troubleshooting"],
        expected_tool_trace=[
            "get_manual_toc", "read_manual_section",
        ],
        must_contain=["P0171", "fuel"],
        pitfall_directives=[
            "The output must not assert this is DTC P0300 — that "
            "is a misfire code, unrelated to lean-condition P0171.",
        ],
        notes="unit-test fixture",
    )


def _adversarial_entry() -> GoldenEntry:
    """Manual-lane adversarial golden — a decline is correct.

    Mirrors the locked ``adversarial-*`` entries: false premise
    (chain drive on a CVT scooter), empty ``expected_recall_slugs``,
    fabrication-focused pitfall directives.
    """
    return GoldenEntry(
        id="mws150a-adversarial-test",
        category="adversarial",
        question_type="adversarial",
        difficulty="hard",
        question=(
            "What is the chain-adjustment procedure for the "
            "MWS-150-A?"
        ),
        golden_summary=(
            "The premise of this question is incorrect.  The "
            "MWS-150-A does NOT use a chain drive — it uses a "
            "V-belt continuously variable transmission (CVT).  "
            "The manual documents V-belt inspection and "
            "replacement instead."
        ),
        pitfall_directives=[
            "The output must not invent a chain-adjustment "
            "specification — the MWS-150-A has no chain drive.",
        ],
        notes="unit-test fixture (#146)",
    )


def _sample_run(output_text: str = "") -> SystemRunResult:
    """Build a minimal agent-lane ``SystemRunResult``."""
    return SystemRunResult(
        system_label="manual_agent",
        question="MWS-150-A shows DTC P0171. What does this mean?",
        output_text=output_text or (
            "DTC P0171 on the MWS-150-A indicates a "
            "system-too-lean fault.  Inspect the fuel system "
            "per the manual.  P0171 lean condition."
        ),
        claim_slugs=["3-2-fuel-system-troubleshooting"],
        read_slugs=["3-2-fuel-system-troubleshooting"],
        iterations=2,
    )


def _decline_run() -> SystemRunResult:
    """A correct explicit decline on the adversarial entry."""
    return SystemRunResult(
        system_label="manual_agent",
        question=(
            "What is the chain-adjustment procedure for the "
            "MWS-150-A?"
        ),
        output_text=(
            "Not found: the MWS-150-A has no chain drive — it "
            "uses a V-belt CVT.  The manual documents V-belt "
            "inspection and replacement, not chain adjustment."
        ),
        iterations=3,
    )


def _fabrication_run() -> SystemRunResult:
    """A fabricated answer on the adversarial entry."""
    return SystemRunResult(
        system_label="manual_agent",
        question=(
            "What is the chain-adjustment procedure for the "
            "MWS-150-A?"
        ),
        output_text=(
            "Adjust the drive chain to 25-35 mm of slack at the "
            "midpoint and lubricate every 500 km per the manual."
        ),
        claim_slugs=["3-9-drive-chain"],
        read_slugs=["3-9-drive-chain"],
        iterations=3,
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


def _judge_json(
    answer_quality: float = 1.0,
    reasoning: str = "Matches the golden.",
    violations: Union[List[Dict[str, Any]], None] = None,
) -> str:
    """Serialize a valid judge-response payload."""
    return json.dumps({
        "answer_quality": answer_quality,
        "reasoning": reasoning,
        "pitfall_violations": violations or [],
    })


# ── _is_no_evidence_entry ─────────────────────────────────────────


class TestIsNoEvidenceEntry:
    """Signal detection for decline-expected entries (#146)."""

    def test_manual_adversarial_is_no_evidence(self) -> None:
        """Manual-lane adversarial question_type is the signal."""
        assert _is_no_evidence_entry(_adversarial_entry()) is True

    def test_normal_lookup_is_not_no_evidence(self) -> None:
        """A plain lookup entry expects a real answer."""
        assert bool(_is_no_evidence_entry(_sample_entry())) is False

    def test_obd_adversarial_is_no_evidence(self) -> None:
        """OBD-lane adversarial question_type is the signal."""
        entry = _adversarial_entry().model_copy(
            update={
                "question_type": "adversarial_obd",
                "category": "symptom",
            },
        )
        assert _is_no_evidence_entry(entry) is True

    def test_expected_no_evidence_flag_is_signal(self) -> None:
        """``expected_no_evidence=True`` marks a decline entry
        even for a non-adversarial question_type (dtc_decode)."""
        entry = _adversarial_entry().model_copy(
            update={
                "question_type": "dtc_decode",
                "category": "dtc",
                "expected_no_evidence": True,
            },
        )
        assert _is_no_evidence_entry(entry) is True


# ── Prompt builder ────────────────────────────────────────────────


class TestBuildUserPrompt:
    """Tests for ``build_user_prompt`` — pure function."""

    def test_includes_core_fields(self) -> None:
        """Prompt contains question, golden summary, and output."""
        entry = _sample_entry()
        run = _sample_run()
        prompt = build_user_prompt(entry, run)
        assert entry.question in prompt
        assert entry.golden_summary in prompt
        assert run.output_text in prompt

    def test_includes_pitfall_directives(self) -> None:
        """Pitfall directives are rendered for the judge."""
        prompt = build_user_prompt(_sample_entry(), _sample_run())
        assert "P0300" in prompt

    def test_truncates_long_output(self) -> None:
        """Output text is capped at _MAX_OUTPUT_CHARS."""
        run = _sample_run(output_text="X" * 10_000)
        prompt = build_user_prompt(_sample_entry(), run)
        assert "[truncated" in prompt
        assert "X" * 10_000 not in prompt

    def test_normal_entry_gets_normal_answerability(self) -> None:
        """A normal entry is marked answerable — refusal scores 0."""
        prompt = build_user_prompt(_sample_entry(), _sample_run())
        assert "## ANSWERABILITY" in prompt
        assert "Normal entry" in prompt
        assert "NO-EVIDENCE / FALSE-PREMISE" not in prompt

    def test_adversarial_entry_gets_no_evidence_marker(self) -> None:
        """An adversarial entry is explicitly marked NO-EVIDENCE
        so the judge credits a correct decline (#146)."""
        prompt = build_user_prompt(
            _adversarial_entry(), _decline_run(),
        )
        assert "## ANSWERABILITY" in prompt
        assert "NO-EVIDENCE / FALSE-PREMISE" in prompt
        assert "Normal entry" not in prompt

    def test_system_prompt_defines_decline_rubric(self) -> None:
        """The system prompt spells out the graded decline path."""
        assert "ANSWERABILITY" in JUDGE_SYSTEM_PROMPT
        assert "QUALITY OF THE DECLINE" in JUDGE_SYSTEM_PROMPT
        # Refusing an answerable question is still a 0.
        assert "NORMAL entry" in JUDGE_SYSTEM_PROMPT


# ── Payload parsing ───────────────────────────────────────────────


class TestParseJudgePayload:
    """Tests for ``_parse_judge_payload``."""

    def test_valid_json_parses(self) -> None:
        """Well-formed JSON parses into a payload."""
        payload = _parse_judge_payload(_judge_json(0.9))
        assert payload.answer_quality == 0.9
        assert payload.pitfall_violation_count == 0

    def test_markdown_fence_is_stripped(self) -> None:
        """A fenced JSON block still parses."""
        raw = f"```json\n{_judge_json(0.8)}\n```"
        payload = _parse_judge_payload(raw)
        assert payload.answer_quality == 0.8

    def test_invalid_json_raises(self) -> None:
        """Malformed JSON raises ValueError."""
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_judge_payload("not json {")

    def test_missing_answer_quality_raises(self) -> None:
        """answer_quality is mandatory."""
        with pytest.raises(ValueError, match="answer_quality"):
            _parse_judge_payload(json.dumps({"reasoning": "x"}))

    def test_out_of_range_answer_quality_raises(self) -> None:
        """Values outside [0, 1] are rejected."""
        with pytest.raises(ValueError, match="out of"):
            _parse_judge_payload(_judge_json(1.5))

    def test_violations_counted(self) -> None:
        """Violated directives are tallied; details preserved."""
        payload = _parse_judge_payload(_judge_json(
            0.2,
            violations=[
                {"directive": "d1", "violated": True,
                 "reasoning": "asserts it"},
                {"directive": "d2", "violated": False,
                 "reasoning": "compliant"},
            ],
        ))
        assert payload.pitfall_violation_count == 1
        assert len(payload.pitfall_violation_details) == 2


# ── rate_quality_and_pitfalls: happy path + retries ───────────────


class TestRateQualityHappyPath:
    """Judge call flow with a valid first response."""

    @pytest.mark.asyncio
    async def test_single_call_returns_rating(self) -> None:
        """One judge call, valid JSON, returns the rating tuple."""
        client = _FakeClient([_judge_json(0.9)])
        aq, reasoning, count, details = (
            await rate_quality_and_pitfalls(
                _sample_entry(), _sample_run(),
                client=client,  # type: ignore[arg-type]
            )
        )
        assert aq == 0.9
        assert count == 0
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_call_uses_pinned_model_and_temperature(
        self,
    ) -> None:
        """Judge request uses the pinned model at temp 0, JSON
        mode, and sends system + user messages."""
        client = _FakeClient([_judge_json()])
        await rate_quality_and_pitfalls(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        kwargs = client.calls[0]
        assert kwargs["model"] == _JUDGE_MODEL
        assert kwargs["temperature"] == _JUDGE_TEMPERATURE
        assert kwargs["response_format"] == {"type": "json_object"}
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == JUDGE_SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        assert "P0171" in messages[1]["content"]


class TestRateQualityRetry:
    """Retry + fallback paths."""

    @pytest.mark.asyncio
    async def test_parse_retry_succeeds(self) -> None:
        """Malformed first response, corrected on retry."""
        client = _FakeClient([
            "this is not json at all",
            _judge_json(0.7),
        ])
        aq, _, _, _ = await rate_quality_and_pitfalls(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert aq == 0.7
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
        """Two malformed responses → zero-score fallback."""
        client = _FakeClient(["not json", "still not json"])
        aq, reasoning, count, details = (
            await rate_quality_and_pitfalls(
                _sample_entry(), _sample_run(),
                client=client,  # type: ignore[arg-type]
            )
        )
        assert aq == 0.0
        assert reasoning.startswith("[judge failure]")
        assert count == 0
        assert details == []

    @pytest.mark.asyncio
    async def test_api_error_first_try_retries(self) -> None:
        """Transient API error on first call → retry once."""
        client = _FakeClient([
            RuntimeError("transient 502"),
            _judge_json(0.6),
        ])
        aq, _, _, _ = await rate_quality_and_pitfalls(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert aq == 0.6
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_api_error_both_tries_returns_fallback(
        self,
    ) -> None:
        """Two API failures → zero-score fallback."""
        client = _FakeClient([
            RuntimeError("transient 502"),
            RuntimeError("still 502"),
        ])
        aq, reasoning, _, _ = await rate_quality_and_pitfalls(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert aq == 0.0
        assert reasoning.startswith("[judge failure]")
        assert "api error" in reasoning

    @pytest.mark.asyncio
    async def test_api_error_on_retry_call_returns_fallback(
        self,
    ) -> None:
        """Parse-retry that itself errors → fallback."""
        client = _FakeClient([
            "not json",  # trigger parse retry
            RuntimeError("retry 502"),  # retry call fails
        ])
        aq, reasoning, _, _ = await rate_quality_and_pitfalls(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert aq == 0.0
        assert reasoning.startswith("[judge failure]")


# ── grade_run: correct decline vs fabrication (#146) ─────────────


class TestGradeRunAdversarialDecline:
    """End-to-end: a correct decline on a no-evidence entry earns
    a high answer_quality; a fabrication stays low.  The judge is
    mocked — these tests pin the PLUMBING (the marker reaches the
    judge, the rating flows into the Grade unclamped) so a
    rubric-following judge can produce the intended scores."""

    @pytest.mark.asyncio
    async def test_judge_sees_no_evidence_marker(self) -> None:
        """grade_run sends the ANSWERABILITY marker for an
        adversarial entry."""
        client = _FakeClient([_judge_json(1.0)])
        await grade_run(
            _adversarial_entry(), _decline_run(),
            client=client,  # type: ignore[arg-type]
        )
        user_prompt = client.calls[0]["messages"][1]["content"]
        assert "NO-EVIDENCE / FALSE-PREMISE" in user_prompt

    @pytest.mark.asyncio
    async def test_correct_decline_scores_high(self) -> None:
        """Correct decline + rubric-following judge → high
        answer_quality and a strong overall (no structural zero)."""
        client = _FakeClient([_judge_json(
            1.0,
            reasoning=(
                "Correctly identifies the false premise: no "
                "chain drive, V-belt CVT instead."
            ),
        )])
        grade = await grade_run(
            _adversarial_entry(), _decline_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert isinstance(grade, Grade)
        assert grade.answer_quality == 1.0
        assert grade.hallucination_penalty == 1.0
        # Adversarial entry + silent citations + clean judge →
        # every dimension is at its ceiling; the overall must
        # reflect that instead of bottoming out.
        assert grade.overall > 0.9

    @pytest.mark.asyncio
    async def test_fabrication_scores_low(self) -> None:
        """Fabricated answer + rubric-following judge → low
        answer_quality, pitfall violation, depressed overall."""
        client = _FakeClient([_judge_json(
            0.1,
            reasoning="Invents a chain spec for a CVT scooter.",
            violations=[{
                "directive": (
                    "The output must not invent a "
                    "chain-adjustment specification"
                ),
                "violated": True,
                "reasoning": "Output gives 25-35 mm slack spec.",
            }],
        )])
        grade = await grade_run(
            _adversarial_entry(), _fabrication_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.answer_quality == 0.1
        assert grade.hallucination_penalty == pytest.approx(0.7)
        # claim_precision is N/A on adversarial entries (#192);
        # the fabricated citation is punished by citation_quality
        # (0.3) plus the judge's low answer_quality and the
        # pitfall violation, not by a 0/1 precision polarity.
        assert grade.claim_precision is None
        assert grade.citation_quality == pytest.approx(0.3)
        assert grade.overall < grade.answer_quality + 0.65

    @pytest.mark.asyncio
    async def test_decline_beats_fabrication_overall(self) -> None:
        """The rubric orders the two outcomes correctly."""
        decline_client = _FakeClient([_judge_json(1.0)])
        fab_client = _FakeClient([_judge_json(
            0.1,
            violations=[{
                "directive": "no invented chain spec",
                "violated": True,
                "reasoning": "invented",
            }],
        )])
        decline_grade = await grade_run(
            _adversarial_entry(), _decline_run(),
            client=decline_client,  # type: ignore[arg-type]
        )
        fab_grade = await grade_run(
            _adversarial_entry(), _fabrication_run(),
            client=fab_client,  # type: ignore[arg-type]
        )
        assert decline_grade.overall > fab_grade.overall


# ── Omission vs assertion directives (#147) ──────────────────────


class TestClassifyPitfallDirective:
    """Phrasing-based directive classification (#147)."""

    def test_must_not_omit_is_omission(self) -> None:
        """The dominant authoring pattern."""
        assert classify_pitfall_directive(
            "The output must not omit the thermostat-stuck-closed "
            "cause — it's a common root cause of overheating."
        ) == "omission"

    def test_positive_requirement_is_omission(self) -> None:
        """cross-001 style: 'must reference X' demands presence."""
        assert classify_pitfall_directive(
            "The output must reference the manual's 12,000 km "
            "figure as the documented interval."
        ) == "omission"

    def test_must_not_assert_is_assertion(self) -> None:
        """Forbidding a claim is assertion-type."""
        assert classify_pitfall_directive(
            "The output must not assert this is DTC P0300."
        ) == "assertion"

    def test_must_not_invent_is_assertion(self) -> None:
        """Forbidding fabrication is assertion-type."""
        assert classify_pitfall_directive(
            "The output must not invent a chain-adjustment "
            "specification."
        ) == "assertion"

    def test_keyword_mention_does_not_flip_type(self) -> None:
        """'...the missing chain spec' inside an assertion
        directive must NOT classify as omission — only the demand
        phrasing counts (real adversarial-001 directive)."""
        assert classify_pitfall_directive(
            "The output must not return brake-system content as "
            "a substitute for the missing chain spec."
        ) == "assertion"

    def test_empty_defaults_to_assertion(self) -> None:
        """Conservative default: pre-#147 behaviour."""
        assert classify_pitfall_directive("") == "assertion"


def _mixed_directives_entry() -> GoldenEntry:
    """Entry with one assertion + one omission directive."""
    entry = _sample_entry()
    entry.pitfall_directives = [
        "The output must not assert this is DTC P0300 — that "
        "is a misfire code, unrelated to lean-condition P0171.",
        "The output must not omit the fuel-pressure check — it "
        "is the manual's first-line lean-condition test.",
    ]
    return entry


def _violations_payload(
    assert_violated: bool, omit_violated: bool,
) -> str:
    """Judge payload marking the two mixed directives."""
    return _judge_json(
        0.5,
        violations=[
            {"directive": "must not assert P0300",
             "violated": assert_violated,
             "reasoning": "x"},
            {"directive": "must not omit fuel-pressure check",
             "violated": omit_violated,
             "reasoning": "y"},
        ],
    )


class TestOmissionDirectiveSplit:
    """Omission violations no longer reduce hallucination_penalty
    (#147); assertion violations still do."""

    def test_prompt_tags_directive_types(self) -> None:
        """The rendered directive list carries type tags."""
        prompt = build_user_prompt(
            _mixed_directives_entry(), _sample_run(),
        )
        assert "[assertion] The output must not assert" in prompt
        assert "[omission] The output must not omit" in prompt

    def test_system_prompt_defines_omission_rule(self) -> None:
        """Judge instructions cover the omission verdict rule."""
        assert "[omission]" in JUDGE_SYSTEM_PROMPT
        assert "not mentioning IS the" in JUDGE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_omission_only_violation_no_penalty(self) -> None:
        """Violated omission directive → penalty stays 1.0."""
        client = _FakeClient([_violations_payload(False, True)])
        grade = await grade_run(
            _mixed_directives_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.hallucination_penalty == pytest.approx(1.0)
        # ...but it is still surfaced in the report reasoning.
        assert "Omission flags" in grade.reasoning
        assert "Pitfall violations" not in grade.reasoning

    @pytest.mark.asyncio
    async def test_assertion_violation_still_penalised(self) -> None:
        """Violated assertion directive → penalty 0.7."""
        client = _FakeClient([_violations_payload(True, False)])
        grade = await grade_run(
            _mixed_directives_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.hallucination_penalty == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_mixed_violations_count_assertion_only(
        self,
    ) -> None:
        """Both violated → one countable violation (0.7, not
        0.4), and both surfaced in reasoning."""
        client = _FakeClient([_violations_payload(True, True)])
        grade = await grade_run(
            _mixed_directives_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.hallucination_penalty == pytest.approx(0.7)
        assert "Pitfall violations (1" in grade.reasoning
        assert "Omission flags (1" in grade.reasoning

    @pytest.mark.asyncio
    async def test_omission_still_hits_fact_recall(self) -> None:
        """The omitted fact still costs recall: the entry's
        must_contain term missing from output → fact_recall < 1
        while the penalty stays clean."""
        entry = _mixed_directives_entry()
        entry.must_contain = ["P0171", "fuel pressure"]
        run = _sample_run(
            output_text="DTC P0171 indicates a lean condition.",
        )
        client = _FakeClient([_violations_payload(False, True)])
        grade = await grade_run(
            entry, run,
            client=client,  # type: ignore[arg-type]
        )
        assert grade.fact_recall == pytest.approx(0.5)
        assert grade.hallucination_penalty == pytest.approx(1.0)


# ── Surfaced image evidence (#193) ────────────────────────────────


def _image_entry() -> GoldenEntry:
    """Image-required golden entry fixture (#193)."""
    entry = _sample_entry()
    return entry.model_copy(update={
        "id": "mws150a-image-test",
        "category": "image",
        "question_type": "image-required",
        "requires_image": True,
        "question": (
            "Where is the balancer weight positioned during "
            "installation?"
        ),
        "golden_summary": (
            "The balancer weight must sit as shown in the "
            "installation figure: aligned with the crankshaft "
            "punch mark."
        ),
    })


def _image_agent_result() -> ManualAgentResult:
    """Agent result with one cited image section and one plain
    navigation section (#193 fixture).

    The cited section mimics ``build_multimodal_section`` output
    text: the ``![...](...)`` ref of the LOADED image is stripped
    while its ``*Vision description: ...*`` paragraph survives,
    plus one residual markdown ref for an image that failed to
    load."""
    cited_text = (
        "Install the balancer as shown.\n\n"
        "*Vision description: Balancer weight aligned with the\n"
        "crankshaft punch mark, timing marks facing outward.*\n\n"
        "![unloaded figure](images/manual/p099-9.png)\n\n"
        "Torque the retaining bolt to 12 N-m."
    )
    return ManualAgentResult(
        summary=(
            "Align the balancer weight with the crankshaft "
            "punch mark as shown in the installation figure."
        ),
        citations=[Citation(
            manual_id="MWS150A_Service_Manual",
            slug="5-3-balancer-installation",
            quote="Install the balancer as shown.",
        )],
        raw_sections=[
            SectionRef(
                manual_id="MWS150A_Service_Manual",
                slug="1-2-table-of-contents",
                text="TOC text, no images.",
                had_images=False,
            ),
            SectionRef(
                manual_id="MWS150A_Service_Manual",
                slug="5-3-balancer-installation",
                text=cited_text,
                had_images=True,
            ),
        ],
        iterations=3,
    )


class TestExtractSurfacedImages:
    """Adapter-side image-evidence extraction (#193)."""

    def test_image_section_is_surfaced(self) -> None:
        """A section with image blocks + vision text yields one
        SurfacedImage with the vision description captured."""
        result = _image_agent_result()
        surfaced = _extract_surfaced_images(
            result, ["5-3-balancer-installation"],
        )
        assert len(surfaced) == 1
        img = surfaced[0]
        assert img.slug == "5-3-balancer-installation"
        assert img.manual_id == "MWS150A_Service_Manual"
        assert img.cited is True
        # Lower-bound count: max(vision descs, residual refs,
        # had_images floor) — here max(1, 1, 1) = 1.
        assert img.image_count == 1
        assert len(img.vision_descriptions) == 1
        assert "crankshaft punch mark" in (
            img.vision_descriptions[0]
        )

    def test_vision_description_whitespace_normalised(self) -> None:
        """Multi-line vision paragraphs collapse to one line."""
        surfaced = _extract_surfaced_images(
            _image_agent_result(),
            ["5-3-balancer-installation"],
        )
        assert "\n" not in surfaced[0].vision_descriptions[0]

    def test_plain_section_is_not_surfaced(self) -> None:
        """Text-only sections contribute no image evidence."""
        surfaced = _extract_surfaced_images(
            _image_agent_result(), [],
        )
        slugs = [s.slug for s in surfaced]
        assert "1-2-table-of-contents" not in slugs

    def test_uncited_image_section_flagged_read_only(self) -> None:
        """cited=False when the slug is not in claim_slugs."""
        surfaced = _extract_surfaced_images(
            _image_agent_result(), [],
        )
        assert surfaced[0].cited is False

    def test_had_images_without_markers_floors_at_one(self) -> None:
        """had_images=True with no vision text or refs still
        counts as one figure."""
        result = ManualAgentResult(
            summary="s",
            raw_sections=[SectionRef(
                manual_id="m",
                slug="wiring",
                text="Diagram text only.",
                had_images=True,
            )],
        )
        surfaced = _extract_surfaced_images(result, [])
        assert surfaced[0].image_count == 1
        assert surfaced[0].vision_descriptions == []

    def test_adapter_populates_surfaced_images(self) -> None:
        """The full adapter carries image evidence into
        SystemRunResult.surfaced_images."""
        run = _agent_result_to_system_run(
            "Where is the balancer weight positioned?",
            _image_agent_result(),
            latency_ms_wall=100.0,
        )
        assert len(run.surfaced_images) == 1
        assert run.surfaced_images[0].cited is True


class TestSurfacedFiguresPrompt:
    """Judge prompt rendering of image evidence (#193)."""

    def _image_run(self) -> SystemRunResult:
        run = _sample_run()
        run.surfaced_images = [SurfacedImage(
            slug="5-3-balancer-installation",
            manual_id="MWS150A_Service_Manual",
            cited=True,
            image_count=2,
            vision_descriptions=[
                "Balancer weight aligned with the crankshaft "
                "punch mark.",
            ],
        )]
        return run

    def test_image_required_entry_detected(self) -> None:
        """question_type OR requires_image marks the entry."""
        assert _is_image_required_entry(_image_entry()) is True
        assert _is_image_required_entry(_sample_entry()) is False
        flagged = _sample_entry().model_copy(
            update={"requires_image": True},
        )
        assert _is_image_required_entry(flagged) is True

    def test_prompt_renders_surfaced_figures_block(self) -> None:
        """Figure slug, count, cited flag, and vision text all
        reach the judge."""
        prompt = build_user_prompt(
            _image_entry(), self._image_run(),
        )
        assert "SURFACED FIGURES" in prompt
        assert "slug=5-3-balancer-installation" in prompt
        assert "figures=2" in prompt
        assert "[CITED]" in prompt
        assert "crankshaft punch mark" in prompt

    def test_image_required_entry_gets_marker(self) -> None:
        """IMAGE REQUIREMENT block flags image-required entries."""
        prompt = build_user_prompt(
            _image_entry(), self._image_run(),
        )
        assert "## IMAGE REQUIREMENT" in prompt
        assert "IMAGE-REQUIRED entry" in prompt

    def test_normal_entry_gets_no_image_marker(self) -> None:
        """Normal entries are marked text-sufficient."""
        prompt = build_user_prompt(_sample_entry(), _sample_run())
        assert "## IMAGE REQUIREMENT" in prompt
        assert "IMAGE-REQUIRED entry" not in prompt

    def test_empty_evidence_renders_explicit_marker(self) -> None:
        """No surfaced images → explicit '(none captured)' so the
        judge knows the figure was not delivered."""
        prompt = build_user_prompt(_image_entry(), _sample_run())
        assert "none captured" in prompt

    def test_system_prompt_defines_figure_credit_rule(self) -> None:
        """The rubric covers full credit for surfaced figures and
        low scores for fabricated figure content."""
        assert "SURFACED FIGURES" in JUDGE_SYSTEM_PROMPT
        assert "IMAGE-REQUIRED" in JUDGE_SYSTEM_PROMPT
        assert "pixel-level" in JUDGE_SYSTEM_PROMPT
        assert "Fabricated figure content" in JUDGE_SYSTEM_PROMPT


class TestOldReportCompatibility:
    """Pre-#193 report JSON must keep loading (#193)."""

    def test_result_without_surfaced_images_loads(self) -> None:
        """A WP1-era result dict (no surfaced_images key) parses
        with the field defaulting to empty."""
        old_record = {
            "system_label": "manual_agent",
            "question": "Where is the balancer weight?",
            "output_text": "As shown in the figure.",
            "claim_slugs": ["5-3-balancer-installation"],
            "read_slugs": ["5-3-balancer-installation"],
            "retrieved_chunk_metadata": [],
            "latency_ms_wall": 1.0,
            "latency_ms_llm": 1.0,
            "cost_usd": 0.0,
            "tool_trace": [],
            "stopped_reason": "complete",
            "iterations": 2,
            "obd_signal_citations": [],
            "obd_dtc_citations": [],
        }
        run = SystemRunResult.model_validate(old_record)
        assert run.surfaced_images == []
        # And the old shape still renders a judge prompt.
        prompt = build_user_prompt(_sample_entry(), run)
        assert "none captured" in prompt


# ── Edge cases ────────────────────────────────────────────────────


class TestGradeRunEdgeCases:
    """Assorted edge cases that shouldn't crash grading."""

    @pytest.mark.asyncio
    async def test_empty_claims_still_graded(self) -> None:
        """A run with no citations still produces a Grade."""
        client = _FakeClient([_judge_json(0.5)])
        run = _sample_run()
        run.claim_slugs = []
        run.read_slugs = []
        grade = await grade_run(
            _sample_entry(), run,
            client=client,  # type: ignore[arg-type]
        )
        assert isinstance(grade, Grade)

    @pytest.mark.asyncio
    async def test_judge_failure_still_produces_grade(self) -> None:
        """Judge infrastructure failure degrades gracefully:
        answer_quality 0, hallucination_penalty stays neutral."""
        client = _FakeClient([
            RuntimeError("502"), RuntimeError("502"),
        ])
        grade = await grade_run(
            _sample_entry(), _sample_run(),
            client=client,  # type: ignore[arg-type]
        )
        assert grade.answer_quality == 0.0
        assert grade.hallucination_penalty == 1.0
        assert "[judge failure]" in grade.reasoning

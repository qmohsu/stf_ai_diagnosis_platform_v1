"""Judge OBD-lane sanity tests (HARNESS-21).

The judge module (``judge.py``) is content-agnostic — its grading
dimensions (``answer_quality`` + ``pitfall_violations``) work the
same way on an OBD-lane ``SystemRunResult`` as on a manual-lane
one.  This file documents and pins that property:

- ``judge_prompts.build_user_prompt`` doesn't crash on an OBD
  entry (whose ``claim_slugs`` / ``read_slugs`` are empty and
  whose ``golden_citations`` may also be empty).
- ``grade_run`` returns a fully-populated ``Grade`` including the
  new ``value_accuracy`` dimension.
- The polarity flip via ``expected_no_evidence`` is reflected
  through the OBD metrics path into the final ``Grade``.

These tests use a fake ``AsyncOpenAI``-shaped client so they don't
require network access or an API key.  Lives in its own file
(rather than extending ``test_judge.py``) because the existing
``test_judge.py`` has pre-existing stale imports tracked
separately and would block collection.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.harness.evals.judge import grade_run
from tests.harness.evals.judge_prompts import build_user_prompt
from tests.harness.evals.schemas import (
    DTCCitation,
    ExpectedDTC,
    ExpectedSignalCitation,
    GoldenEntry,
    SignalCitation,
    SystemRunResult,
)


# ── Helpers ───────────────────────────────────────────────────────


def _fake_judge_client(payload: dict) -> Any:
    """Construct a stand-in for ``AsyncOpenAI`` returning ``payload``.

    Mirrors the shape ``judge._call_judge`` expects:
    ``client.chat.completions.create(...)`` is an async callable
    returning an object with ``.choices[0].message.content``.

    Args:
        payload: The dict the fake judge "returns" as JSON.

    Returns:
        Mock client that returns the JSON-encoded payload.
    """
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=completion)
    return client


def _obd_signal_stats_entry() -> GoldenEntry:
    """Sample OBD ``signal_statistics`` golden."""
    return GoldenEntry(
        id="yamaha-stats-001",
        category="component",
        question_type="signal_statistics",
        difficulty="easy",
        question="What was the peak engine RPM during this trip?",
        golden_summary="Engine RPM peaks at 3906 rpm.",
        expected_signal_citations=[
            ExpectedSignalCitation(
                signal="RPM", stat="max", value=3906.0,
            ),
        ],
        must_contain=["RPM", "3906"],
        pitfall_directives=[
            "The output must not report an RPM maximum greater "
            "than 4000.",
        ],
    )


def _obd_signal_stats_run() -> SystemRunResult:
    """Sample ``SystemRunResult`` matching the entry above."""
    return SystemRunResult(
        system_label="obd_agent",
        question="What was the peak engine RPM during this trip?",
        output_text=(
            "Engine RPM peaks at 3906 rpm.\n\n"
            "--- Signal citations (1) ---\n"
            "RPM (max) = 3906.0"
        ),
        obd_signal_citations=[
            SignalCitation(signal="RPM", stat="max", value=3906.0),
        ],
    )


def _ok_judge_payload() -> dict:
    """A "judge says perfect" payload."""
    return {
        "answer_quality": 1.0,
        "reasoning": "Answer matches the golden completely.",
        "pitfall_violations": [
            {
                "directive": "The output must not report an RPM "
                             "maximum greater than 4000.",
                "violated": False,
                "reasoning": "Output cites 3906, well under 4000.",
            },
        ],
    }


# ── build_user_prompt sanity ──────────────────────────────────────


class TestBuildUserPromptObd:
    """The prompt builder doesn't crash on OBD-lane inputs."""

    def test_obd_entry_does_not_crash(self):
        """Empty ``claim_slugs`` / ``read_slugs`` / ``golden_citations``
        should format cleanly to "(none)" — no AttributeError, no
        IndexError."""
        prompt = build_user_prompt(
            _obd_signal_stats_entry(),
            _obd_signal_stats_run(),
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # Sanity: the slug blocks render as "(none)".
        assert "(none)" in prompt
        # The pitfall directive shows up.
        assert "RPM maximum greater than 4000" in prompt

    def test_obd_entry_renders_question_and_summary(self):
        """The judge sees the question + golden summary verbatim."""
        prompt = build_user_prompt(
            _obd_signal_stats_entry(),
            _obd_signal_stats_run(),
        )
        assert "peak engine RPM during this trip" in prompt
        assert "Engine RPM peaks at 3906 rpm." in prompt

    def test_obd_entry_renders_system_label(self):
        """``system_label="obd_agent"`` appears in the rendered
        prompt for judge transparency."""
        prompt = build_user_prompt(
            _obd_signal_stats_entry(),
            _obd_signal_stats_run(),
        )
        assert "obd_agent" in prompt

    def test_obd_entry_renders_output_text_with_citations(self):
        """The structured citation block from the OBD adapter
        flows into the judge's view of the output."""
        prompt = build_user_prompt(
            _obd_signal_stats_entry(),
            _obd_signal_stats_run(),
        )
        assert "--- Signal citations" in prompt
        assert "RPM (max) = 3906.0" in prompt


# ── grade_run on OBD entries ──────────────────────────────────────


class TestGradeRunObdLane:
    """End-to-end: deterministic OBD metrics + fake judge call →
    fully populated ``Grade``."""

    @pytest.mark.asyncio
    async def test_full_grade_returns_all_dimensions(self):
        """``Grade`` has all 11 fields including ``value_accuracy``."""
        client = _fake_judge_client(_ok_judge_payload())
        grade = await grade_run(
            _obd_signal_stats_entry(),
            _obd_signal_stats_run(),
            client=client,
        )
        # New OBD-specific dim is present.
        assert hasattr(grade, "value_accuracy")
        assert grade.value_accuracy == pytest.approx(1.0)
        # Slots populated by the OBD lane.
        assert grade.section_recall == pytest.approx(1.0)
        # signal_recall = 1.0
        assert grade.claim_precision == pytest.approx(1.0)
        # signal_precision = 1.0
        assert grade.citation_quality == pytest.approx(1.0)
        # dtc_accuracy = 1.0 (vacuous; no expected DTCs)
        # Judge-provided fields.
        assert grade.answer_quality == pytest.approx(1.0)
        assert grade.hallucination_penalty == pytest.approx(1.0)
        # Overall is the weighted sum; perfect input → 1.0.
        assert grade.overall == pytest.approx(1.0, abs=0.05)

    @pytest.mark.asyncio
    async def test_adversarial_obd_polarity_flip(self):
        """``expected_no_evidence=True`` + agent cited nothing →
        OBD metrics score 1.0; overall stays high."""
        entry = GoldenEntry(
            id="yamaha-adv-001",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is the engine misfiring?",
            golden_summary="No evidence of misfire.",
            expected_no_evidence=True,
            must_contain=["no evidence"],
            pitfall_directives=[
                "The output must not assert misfire.",
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is the engine misfiring?",
            output_text=(
                "No evidence of misfire in the captured log."
            ),
        )
        client = _fake_judge_client({
            "answer_quality": 1.0,
            "reasoning": "Correctly refused to fabricate.",
            "pitfall_violations": [
                {
                    "directive": "The output must not assert "
                                 "misfire.",
                    "violated": False,
                    "reasoning": "Output denies misfire.",
                },
            ],
        })
        grade = await grade_run(entry, run, client=client)
        assert grade.section_recall == pytest.approx(1.0)
        # Polarity flip — citing nothing IS the right answer.
        assert grade.claim_precision == pytest.approx(1.0)
        assert grade.citation_quality == pytest.approx(1.0)
        assert grade.value_accuracy == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_adversarial_obd_violation(self):
        """``expected_no_evidence=True`` + agent cited a signal →
        OBD metrics drop; overall reflects the failure."""
        entry = GoldenEntry(
            id="yamaha-adv-002",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is the engine misfiring?",
            golden_summary="No evidence of misfire.",
            expected_no_evidence=True,
            pitfall_directives=[
                "The output must not assert misfire.",
            ],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="Is the engine misfiring?",
            output_text=(
                "Possible misfire indicated by RPM dip.\n\n"
                "--- Signal citations (1) ---\n"
                "RPM = 1500.0"
            ),
            obd_signal_citations=[
                SignalCitation(signal="RPM", value=1500.0),
            ],
        )
        # Judge sees the violation too.
        client = _fake_judge_client({
            "answer_quality": 0.1,
            "reasoning": "Fabricated a misfire claim.",
            "pitfall_violations": [
                {
                    "directive": "The output must not assert "
                                 "misfire.",
                    "violated": True,
                    "reasoning": "Output asserts misfire.",
                },
            ],
        })
        grade = await grade_run(entry, run, client=client)
        # OBD signal-recall polarity flip — agent cited when it
        # shouldn't have.
        assert grade.section_recall == pytest.approx(0.0)
        assert grade.claim_precision == pytest.approx(0.0)
        # Judge caught the pitfall violation.
        assert grade.hallucination_penalty < 1.0
        # Overall should be well below 1.0.
        assert grade.overall < 0.5

    @pytest.mark.asyncio
    async def test_dtc_enumeration_grade(self):
        """``dtc_enumeration`` entry: DTC accuracy drives
        citation_quality slot."""
        entry = GoldenEntry(
            id="yamaha-dtcs-001",
            category="dtc",
            question_type="dtc_enumeration",
            difficulty="easy",
            question="What DTCs are stored?",
            golden_summary="Two stored Yamaha-hex codes.",
            expected_dtcs=[
                ExpectedDTC(code="87F11043", status="stored"),
                ExpectedDTC(code="44F2305A", status="stored"),
            ],
            must_contain=["stored"],
        )
        run = SystemRunResult(
            system_label="obd_agent",
            question="What DTCs are stored?",
            output_text=(
                "Two stored DTCs.\n\n"
                "--- DTC citations (2) ---\n"
                "87F11043 (stored, K-Line)\n"
                "44F2305A (stored, K-Line)"
            ),
            obd_dtc_citations=[
                DTCCitation(
                    code="87F11043", status="stored", ecu="K-Line",
                ),
                DTCCitation(
                    code="44F2305A", status="stored", ecu="K-Line",
                ),
            ],
        )
        client = _fake_judge_client(_ok_judge_payload())
        grade = await grade_run(entry, run, client=client)
        # citation_quality holds dtc_accuracy = 1.0.
        assert grade.citation_quality == pytest.approx(1.0)
        # No signal expectations → signal dims vacuously 1.0.
        assert grade.section_recall == pytest.approx(1.0)
        assert grade.claim_precision == pytest.approx(1.0)
        assert grade.value_accuracy == pytest.approx(1.0)

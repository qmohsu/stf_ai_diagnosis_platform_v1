"""Round-trip tests for the eval-schema additions in HARNESS-21.

Covers only the additive surface (``ExpectedSignalCitation``,
``ExpectedDTC``, the three new optional fields on ``GoldenEntry``,
the two new optional fields on ``SystemRunResult``, widened
``SystemLabel`` and ``GoldenQuestionType`` literals).  The existing
manual-lane shapes are still exercised end-to-end by
``test_judge.py`` and ``test_manual_agent_eval.py`` — this module
only guards the parts the older tests don't touch.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.harness.evals.schemas import (
    OBD_QUESTION_TYPES,
    DTCCitation,
    ExpectedDTC,
    ExpectedSignalCitation,
    GoldenCitation,
    GoldenEntry,
    Grade,
    SignalCitation,
    SystemRunResult,
)


# ── ExpectedSignalCitation ────────────────────────────────────────


class TestExpectedSignalCitation:
    """Round-trip + default-value coverage."""

    def test_minimal_construction(self):
        """Only ``signal`` is required; everything else defaults."""
        cite = ExpectedSignalCitation(signal="RPM")
        assert cite.signal == "RPM"
        assert cite.stat is None
        assert cite.value is None
        assert cite.value_tolerance_rel == 0.05
        assert cite.time_range is None

    def test_full_construction(self):
        """All optional fields accept the documented shapes."""
        cite = ExpectedSignalCitation(
            signal="COOLANT_TEMP",
            stat="max",
            value=84.0,
            value_tolerance_rel=0.10,
            time_range=("2026-05-08T11:20:39", "2026-05-08T11:24:55"),
        )
        assert cite.stat == "max"
        assert cite.value == pytest.approx(84.0)
        assert cite.value_tolerance_rel == pytest.approx(0.10)
        assert cite.time_range == (
            "2026-05-08T11:20:39",
            "2026-05-08T11:24:55",
        )

    def test_json_round_trip(self):
        """``model_dump_json`` + ``model_validate_json`` is lossless."""
        original = ExpectedSignalCitation(
            signal="SPEED",
            stat="mean",
            value=6.2,
        )
        encoded = original.model_dump_json()
        decoded = ExpectedSignalCitation.model_validate_json(encoded)
        assert decoded == original


# ── ExpectedDTC ───────────────────────────────────────────────────


class TestExpectedDTC:
    """Round-trip + default-value coverage."""

    def test_minimal_construction(self):
        """Only ``code`` is required; ``status`` defaults to ``None``."""
        dtc = ExpectedDTC(code="P0117")
        assert dtc.code == "P0117"
        assert dtc.status is None

    def test_status_constrained_to_literal(self):
        """``status`` accepts only ``stored``/``pending`` when set."""
        ExpectedDTC(code="P0117", status="stored")
        ExpectedDTC(code="P0117", status="pending")
        with pytest.raises(ValidationError):
            ExpectedDTC(code="P0117", status="latent")  # type: ignore[arg-type]


# ── GoldenEntry additive fields ───────────────────────────────────


class TestGoldenEntryObdFields:
    """The three OBD-lane fields are optional and default correctly."""

    def test_manual_entry_construction_unchanged(self):
        """Existing manual-lane authoring still works.

        Regression guard against schema additions accidentally
        making a previously-valid manual entry invalid.
        """
        entry = GoldenEntry(
            id="manual-001",
            category="dtc",
            question_type="lookup",
            difficulty="easy",
            question="What does P0117 mean?",
            golden_summary="P0117 indicates engine coolant "
                           "temperature circuit low voltage.",
            golden_citations=[
                GoldenCitation(
                    manual_id="MWS150A_Service_Manual",
                    slug="dtc-codes",
                    quote="P0117 — engine coolant temperature circuit low",
                ),
            ],
        )
        assert entry.expected_signal_citations == []
        assert entry.expected_dtcs == []
        assert entry.expected_no_evidence is False

    def test_obd_entry_construction(self):
        """OBD-lane entries can populate the new fields and skip
        the manual-side ``golden_citations`` thanks to its default."""
        entry = GoldenEntry(
            id="yamaha-stats-001",
            category="component",
            question_type="signal_statistics",
            difficulty="easy",
            question="What was the peak engine RPM?",
            golden_summary="Engine RPM peaks at 3906 rpm during "
                           "the trip.",
            expected_signal_citations=[
                ExpectedSignalCitation(
                    signal="RPM", stat="max", value=3906.0,
                ),
            ],
            must_contain=["RPM", "3906"],
        )
        assert entry.golden_citations == []
        assert len(entry.expected_signal_citations) == 1
        assert entry.expected_signal_citations[0].value == 3906.0

    def test_adversarial_obd_entry_with_no_evidence_flag(self):
        """``expected_no_evidence=True`` round-trips."""
        entry = GoldenEntry(
            id="yamaha-adversarial-001",
            category="symptom",
            question_type="adversarial_obd",
            difficulty="hard",
            question="Is the engine misfiring?",
            golden_summary="No evidence of misfire in the log.",
            expected_no_evidence=True,
            pitfall_directives=[
                "The output must not assert misfire.",
            ],
        )
        assert entry.expected_no_evidence is True
        assert entry.expected_signal_citations == []
        assert entry.expected_dtcs == []

    def test_obd_entry_with_dtcs(self):
        """``expected_dtcs`` is populated case-insensitively at use
        time — at the schema level we just round-trip whatever's
        authored."""
        entry = GoldenEntry(
            id="yamaha-dtcs-001",
            category="dtc",
            question_type="dtc_enumeration",
            difficulty="easy",
            question="What DTCs are stored on this bike?",
            golden_summary="Two stored DTCs from the K-Line ECU.",
            expected_dtcs=[
                ExpectedDTC(
                    code="87F11043000000000000CB",
                    status="stored",
                ),
                ExpectedDTC(
                    code="44F2305A000000000000AB",
                    status="stored",
                ),
            ],
            must_contain=["stored"],
        )
        assert len(entry.expected_dtcs) == 2
        assert entry.expected_dtcs[0].status == "stored"

    def test_obd_question_types_in_literal(self):
        """Every value in ``OBD_QUESTION_TYPES`` is accepted by
        ``GoldenEntry.question_type``."""
        for qt in OBD_QUESTION_TYPES:
            entry = GoldenEntry(
                id=f"qt-{qt}",
                category="symptom",
                question_type=qt,  # type: ignore[arg-type]
                difficulty="easy",
                question="placeholder",
                golden_summary="placeholder",
            )
            assert entry.question_type == qt

    def test_rejects_unknown_question_type(self):
        """Literal narrowing still catches typos."""
        with pytest.raises(ValidationError):
            GoldenEntry(
                id="bad-qt",
                category="symptom",
                question_type="not-a-real-type",  # type: ignore[arg-type]
                difficulty="easy",
                question="placeholder",
                golden_summary="placeholder",
            )


# ── SystemRunResult additive fields ───────────────────────────────


class TestSystemRunResultObdFields:
    """Two new optional fields default to empty lists."""

    def test_manual_run_construction_unchanged(self):
        """Existing manual-lane callers don't supply OBD fields."""
        run = SystemRunResult(
            system_label="manual_agent",
            question="What does P0117 mean?",
            output_text="P0117 — engine coolant temperature low.",
        )
        assert run.obd_signal_citations == []
        assert run.obd_dtc_citations == []

    def test_obd_run_construction(self):
        """OBD adapter populates the new fields directly."""
        run = SystemRunResult(
            system_label="obd_agent",
            question="What was the peak RPM?",
            output_text="Peak RPM was 3906.",
            obd_signal_citations=[
                SignalCitation(
                    signal="RPM", stat="max", value=3906.0,
                ),
            ],
            obd_dtc_citations=[
                DTCCitation(
                    code="87F11043000000000000CB",
                    status="stored",
                    ecu="K-Line",
                ),
            ],
        )
        assert run.system_label == "obd_agent"
        assert len(run.obd_signal_citations) == 1
        assert run.obd_signal_citations[0].value == 3906.0
        assert run.obd_dtc_citations[0].ecu == "K-Line"

    def test_system_label_accepts_obd_agent(self):
        """Widened ``SystemLabel`` literal accepts the new value."""
        run = SystemRunResult(
            system_label="obd_agent",
            question="q",
            output_text="o",
        )
        assert run.system_label == "obd_agent"


# ── Grade unchanged in commit 1 ──────────────────────────────────


def test_grade_round_trip_unchanged():
    """``Grade`` hasn't gained a field yet — commit 3 adds
    ``value_accuracy``.  This test exists so the commit-3 diff is
    visible (a single new field assertion lands here)."""
    grade = Grade(
        section_recall=0.8,
        claim_precision=0.6,
        exploration_cost=0.2,
        fact_recall=0.9,
        fact_density=0.5,
        hallucination_penalty=1.0,
        citation_quality=1.0,
        answer_quality=0.85,
        overall=0.78,
        reasoning="Sample reasoning.",
    )
    decoded = Grade.model_validate_json(grade.model_dump_json())
    assert decoded == grade

"""PROD-10 T-1: the V3 eval measures with V2's ruler (FM-1 / FM-41 / FM-57).

The fixture holds archived V2 eval records (5 manual goldens, one per
question type, and 3 OBD goldens) plus, for each, the sub-agent result
reconstructed from the archived run (V2's own mapping reproduces the
archived record byte-for-byte from it) and the deterministic metrics
V2's scorer computes today.  The V3 copies must give exactly the same.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import pytest

from stf_v3.evals.lanes import _agent_result_to_system_run, _obd_result_to_system_run
from stf_v3.evals.metrics import compute_deterministic_metrics
from stf_v3.evals.schemas import GoldenEntry, Grade, ManualAgentResult, OBDAgentResult, SystemRunResult

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "evals" / "v2_equivalence.json"
DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
DET = ("section_recall", "claim_precision", "exploration_cost", "fact_recall", "fact_density",
       "citation_quality", "trajectory_efficiency", "value_accuracy")


def _ids(recs: List[Dict[str, Any]]) -> List[str]:
    return [r["entry"]["id"][-24:] for r in recs]


def test_fixture_covers_every_manual_question_type_and_obd() -> None:
    """The fixture spans all five manual question types and the OBD lane."""
    assert sorted(r["entry"]["question_type"] for r in DATA["manual"]) == [
        "adversarial", "cross-section", "image-required", "lookup", "procedural"]
    assert len(DATA["obd"]) == 3


@pytest.mark.parametrize("rec", DATA["manual"], ids=_ids(DATA["manual"]))
def test_manual_mapping_reproduces_the_archived_v2_run(rec: Dict[str, Any]) -> None:
    """Summary + cited sections, claim / read slugs and surfaced figures:
    byte-identical to what V2 wrote (FM-1 / FM-41)."""
    archived = rec["archived_result"]
    agent = ManualAgentResult.model_validate(rec["agent_result"])
    mapped = _agent_result_to_system_run(archived["question"], agent, archived["latency_ms_wall"])
    assert mapped.output_text == archived["output_text"]
    assert mapped.claim_slugs == archived["claim_slugs"]
    assert mapped.read_slugs == archived["read_slugs"]
    assert [s.model_dump(mode="json") for s in mapped.surfaced_images] == archived["surfaced_images"]


@pytest.mark.parametrize("rec", DATA["obd"], ids=_ids(DATA["obd"]))
def test_obd_mapping_reproduces_the_archived_v2_run(rec: Dict[str, Any]) -> None:
    """Summary + signal / DTC citations + limitations: byte-identical."""
    archived = rec["archived_result"]
    agent = OBDAgentResult.model_validate(rec["agent_result"])
    mapped = _obd_result_to_system_run(archived["question"], agent, archived["latency_ms_wall"])
    assert mapped.output_text == archived["output_text"]
    assert [c.model_dump(mode="json") for c in mapped.obd_signal_citations] == archived["obd_signal_citations"]
    assert [c.model_dump(mode="json") for c in mapped.obd_dtc_citations] == archived["obd_dtc_citations"]


def test_the_scorer_counts_real_tokens() -> None:
    """The comparison below only means something with the real cl100k_base:
    without it the scorer silently falls back to len/4 and V2 and V3 would
    agree on wrong numbers (FM-43 — happened while writing this test)."""
    from stf_v3.evals import metrics

    assert metrics._count_tokens("測試 test") == 5, "cl100k_base unavailable: set TIKTOKEN_CACHE_DIR"


@pytest.mark.parametrize("rec", DATA["manual"] + DATA["obd"], ids=_ids(DATA["manual"] + DATA["obd"]))
def test_deterministic_metrics_equal_v2(rec: Dict[str, Any]) -> None:
    """The V3 scorer copy computes exactly V2's numbers on the same input
    (needs the real cl100k_base tokenizer: fact_density counts tokens)."""
    entry = GoldenEntry.model_validate(rec["entry"])
    run = SystemRunResult.model_validate(rec["archived_result"])
    det = compute_deterministic_metrics(entry, run)
    for dim in DET:
        assert getattr(det, dim) == pytest.approx(rec["expected_det"][dim], abs=1e-12), dim


def test_archived_v2_scorecard_records_load_with_v3_schemas() -> None:
    """V2 report JSON (entry / result / grade) validates with the V3 copies (FM-57)."""
    for rec in DATA["manual"] + DATA["obd"]:
        GoldenEntry.model_validate(rec["entry"])
        SystemRunResult.model_validate(rec["archived_result"])
        Grade.model_validate(rec["archived_grade"])

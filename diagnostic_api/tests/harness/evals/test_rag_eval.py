"""Parametrized entry point for the RAG eval lane (HARNESS-20 phase 3).

Mirror of ``test_manual_agent_eval.py`` for the RAG retriever.
Same locked-tier source (`golden/v2/locked/mws150a.jsonl`), same
LLM judge (`grade_run` via z-ai/glm-5.1), same `Grade` envelope.

Lets the eval suite produce an **agent-vs-RAG** comparison
without changing the manual-agent suite — both files write into
the same session-scoped ``eval_report`` fixture, so a single
pytest invocation grades both lanes against the same 30 goldens
and the resulting JSON report carries both sets of grades.

Why a separate file (instead of parametrising the existing file
over an extra `system` axis): the agent lane has its own
mock-agent CLI flag + fixture; the RAG lane needs neither (it
takes no `deps` parameter, just calls `retrieve_context`
directly). Forking the file keeps each lane's parametrize axis
focused on `entry` only and keeps mock plumbing per-lane.

Run both lanes against the locked corpus::

    pytest --run-eval \\
        tests/harness/evals/test_manual_agent_eval.py \\
        tests/harness/evals/test_rag_eval.py

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from tests.harness.evals.conftest import (
    EvalReport,
    load_golden,
)
from tests.harness.evals.judge import grade_run
from tests.harness.evals.rag_runner import run_rag
from tests.harness.evals.schemas import GoldenEntry


# Minimum overall score the RAG lane must achieve.  Re-pinned from
# the HARNESS-23 v2 re-baseline (#155, run 2026-07-12, after the
# phase-1/2 fixes): rag mean 0.239, stdev 0.122 over the 30 locked
# goldens → mean − 1·stdev = 0.117, floored to 0.1.  Lower than the
# v1 pin (0.2) because #153 removed RAG's free (1 − exploration_cost)
# credit and #148 removed the vacuous adversarial section_recall
# floor — the lane lost structural free credit, not capability.
# Deliberately lower than the agent lane (0.4): single-shot top-5
# concatenation has no synthesis step (answer_quality ~0.06).
# Revisit if the RAG lane ever grows a synthesis step.  v1 and v2
# numbers are NOT comparable.  See
# docs/harness_14_phase6_baseline.md.
_PASS_THRESHOLD = 0.1


# Load goldens at import time so pytest parametrization shows one
# test id per entry.  Same empty-tier safety net as the agent
# lane: a single skipped placeholder when no entries have been
# promoted yet, instead of a parametrize-with-empty-list
# collection crash.
_LOCKED_ENTRIES = load_golden("v2/locked/mws150a.jsonl")

_NO_LOCKED_REASON = (
    "No entries in golden/v2/locked/mws150a.jsonl yet.  Promote "
    "candidates via `python -m scripts.promote_golden "
    "--entry-id <id> --reviewer <name> --reason <why>` "
    "(HARNESS-20)."
)
_PARAM_ENTRIES = (
    _LOCKED_ENTRIES
    if _LOCKED_ENTRIES
    else [
        pytest.param(
            None,
            id="no-locked-entries",
            marks=pytest.mark.skip(reason=_NO_LOCKED_REASON),
        ),
    ]
)


# RAG-side knobs.  ``top_k=5`` matches the production endpoint
# default.
#
# ``vehicle_model="TRICITY155"``: HARNESS-23 found the corpus had
# drifted since the issue was written — the goldens' Yamaha manual
# ("MWS150-A 中文SERVICE MANUAL.pdf") is stored under vehicle_model
# ``TRICITY155``, and a second manual (``Corolla E11``, Toyota) was
# ingested into the same pgvector table.  The old ``"MWS150-A"``
# label matched zero rows.
#
# ``_RAG_EXACT_SCAN``: with two manuals sharing the HNSW index, a
# hard single-manual filter is starved to zero rows — HNSW selects
# the approximate nearest neighbours first (all from the larger
# English Corolla manual for cross-language queries) and only then
# applies the filter, so nothing survives even at the max
# ef_search=1000.  The exact sequential-scan path makes the filter
# faithful again so the RAG lane actually retrieves Yamaha content.
# See ``rag_runner._sync_exact_vector_query`` for the full rationale.
#
# Bumping the corpus to N>2 manuals would lift this to a parametrize
# axis (recall@k per manual scope).
_RAG_TOP_K = 5
_RAG_VEHICLE_MODEL = "TRICITY155"
_RAG_EXACT_SCAN = True


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    _PARAM_ENTRIES,
    ids=lambda e: e.id if _LOCKED_ENTRIES else None,
)
async def test_rag(
    entry: GoldenEntry,
    eval_report: EvalReport,
    judge_client: Optional[Any],
) -> None:
    """Run RAG retrieval and grade it against the golden entry.

    Args:
        entry: One ``GoldenEntry`` from
            ``golden/v2/locked/mws150a.jsonl``.
        eval_report: Session-scoped report accumulator.  Shared
            with the agent lane so a single pytest invocation
            produces one combined report covering both systems.
        judge_client: ``None`` for the real GLM 5.1 judge, or a
            mock client when ``--mock-judge`` is passed.
    """
    run = await run_rag(
        question=entry.question,
        top_k=_RAG_TOP_K,
        vehicle_model=_RAG_VEHICLE_MODEL,
        exact=_RAG_EXACT_SCAN,
    )
    grade = await grade_run(entry, run, client=judge_client)
    eval_report.record(entry, run, grade)  # type: ignore[arg-type]

    assert grade.overall >= _PASS_THRESHOLD, (
        f"[{entry.id} / rag] overall={grade.overall:.2f} "
        f"below threshold {_PASS_THRESHOLD}: "
        f"{grade.reasoning}"
    )

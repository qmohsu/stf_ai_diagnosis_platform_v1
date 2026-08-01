"""Parametrized entry point for the RAG eval lane (HARNESS-20 phase 3).

Mirror of ``test_manual_agent_eval.py`` for the RAG retriever.
Same locked-tier source (`golden/v2/locked/mws150a.jsonl`), same
LLM judge (`grade_run` via z-ai/glm-5.1), same `Grade` envelope.

Since HARNESS-31 (#225) execution is pipelined: the session-scoped
``pipeline_results`` fixture runs BOTH lanes' selected goldens up
front (manual lane first, mirroring collection order) with judge
calls overlapping subsequent runs; this file's tests are thin
assertions over the pre-computed outcomes.  The RAG-side knobs
(top_k, vehicle_model, exact scan) moved to ``lanes.py`` where the
orchestrator builds the run callables.

Lets the eval suite produce an **agent-vs-RAG** comparison
without changing the manual-agent suite — both files write into
the same session-scoped ``eval_report`` fixture, so a single
pytest invocation grades both lanes against the same 30 goldens
and the resulting JSON report carries both sets of grades.

Run both lanes against the locked corpus::

    pytest --run-eval \\
        tests/harness/evals/test_manual_agent_eval.py \\
        tests/harness/evals/test_rag_eval.py

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from tests.harness.evals.conftest import (
    EvalReport,
    load_golden,
)
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
#
# #234: deliberately NOT track-selected (unlike the manual lane).
# RAG retrieves pgvector chunks whose metadata carries legacy
# heading slugs regardless of the manual tools' index track, so
# grading it against the makeup overlay's node-id anchors would
# structurally zero its ``section_recall``.  This lane stays on
# the legacy-slug file — comparable with its own history
# (HARNESS-23 re-baseline 0.239).
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


@pytest.mark.eval
@pytest.mark.parametrize(
    "entry",
    _PARAM_ENTRIES,
    ids=lambda e: e.id if _LOCKED_ENTRIES else None,
)
def test_rag(
    entry: GoldenEntry,
    eval_report: EvalReport,
    pipeline_results: Dict[Any, Any],
) -> None:
    """Assert the pipelined RAG outcome for one golden.

    Args:
        entry: One ``GoldenEntry`` from
            ``golden/v2/locked/mws150a.jsonl``.
        eval_report: Session-scoped report accumulator.  Shared
            with the agent lane so a single pytest invocation
            produces one combined report covering both systems.
        pipeline_results: Session-scoped mapping of
            ``(system_label, entry_id)`` to pre-computed
            ``PipelineOutcome`` (HARNESS-31).
    """
    outcome = pipeline_results.get(("rag", entry.id))
    assert outcome is not None, (
        f"[{entry.id} / rag] missing from pipeline results — the "
        f"orchestrator did not schedule this item (conftest "
        f"item-selection bug?)"
    )
    if outcome.error is not None:
        raise outcome.error
    eval_report.record(
        entry, outcome.run, outcome.grade,  # type: ignore[arg-type]
    )

    assert outcome.grade.overall >= _PASS_THRESHOLD, (
        f"[{entry.id} / rag] overall={outcome.grade.overall:.2f} "
        f"below threshold {_PASS_THRESHOLD}: "
        f"{outcome.grade.reasoning}"
    )

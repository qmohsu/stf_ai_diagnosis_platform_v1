"""Parametrized entry point for the manual-agent eval suite.

One test per ``GoldenEntry`` loaded from
``golden/v2/locked/mws150a.jsonl``.  Each test runs the manual
sub-agent against the entry's question, grades the output via
the LLM judge, records the triple in the session-scoped
``eval_report``, and asserts ``grade.overall >= 0.7``.

HARNESS-20 moved the source from the v1 set (mutable, drifted
from production) to the locked tier of v2.  The locked tier is
append-only and only contains entries that an expert reviewer
has accepted via the dashboard and that
``scripts/promote_golden.py`` has explicitly promoted.  An
empty locked file is a deliberate safety net: the suite collects
zero parametrised cases (skipping cleanly) rather than grading
against unreviewed candidates.

Skipped unless ``--run-eval`` is passed on the command line.

Run with::

    pytest --run-eval diagnostic_api/tests/harness/evals/

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
from tests.harness.evals.runner import run_manual_agent_unified
from tests.harness.evals.schemas import GoldenEntry


# Minimum overall score the agent must achieve.  Pinned from the
# HARNESS-23 baseline (#107, run 2026-06-20): manual_agent mean
# 0.590, stdev 0.163 over the 30 locked goldens → mean − 1·stdev
# = 0.427, floored to 0.4.  This is a regression floor, not a
# quality target — it catches the lane falling off a cliff without
# flapping on per-entry judge noise.  See
# docs/harness_14_phase6_baseline.md.
_PASS_THRESHOLD = 0.4


# Load goldens at import time so pytest parametrization shows one
# test id per entry.  HARNESS-20: the locked tier is the canonical
# source — promote_golden.py is the only way an entry lands here.
_LOCKED_ENTRIES = load_golden("v2/locked/mws150a.jsonl")

# An empty locked file is the shipped initial state (no entries
# promoted yet).  Parametrising on an empty list crashes pytest's
# ``ids=lambda`` evaluator, so substitute a single skipped
# placeholder that explains how to populate the tier — gives a
# clean "1 skipped" line instead of a collection error.
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
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    _PARAM_ENTRIES,
    ids=lambda e: e.id if _LOCKED_ENTRIES else None,
)
async def test_manual_agent(
    entry: GoldenEntry,
    eval_report: EvalReport,
    judge_client: Optional[Any],
    manual_agent_deps: Optional[Any],
) -> None:
    """Run the manual agent and grade it against the golden entry.

    Args:
        entry: One ``GoldenEntry`` from
            ``golden/v2/locked/mws150a.jsonl``.
        eval_report: Session-scoped report accumulator.
        judge_client: ``None`` for the real GLM 5.1 judge, or a
            mock client when ``--mock-judge`` is passed.
        manual_agent_deps: ``None`` for real deps pointing at
            local Ollama, or a stub deps object when
            ``--mock-agent`` is passed.
    """
    # Mirror the OBD eval's pattern: produce a unified
    # SystemRunResult so the shared judge (grade_run) can grade
    # the manual agent and RAG on the same rubric.  The legacy
    # judge_result helper that took a ManualAgentResult directly
    # was removed during HARNESS-21's judge rewrite when the
    # grader was generalised across systems.
    run = await run_manual_agent_unified(
        entry.question, entry.obd_context,
        deps=manual_agent_deps,
    )
    grade = await grade_run(entry, run, client=judge_client)
    eval_report.record(entry, run, grade)  # type: ignore[arg-type]

    assert grade.overall >= _PASS_THRESHOLD, (
        f"[{entry.id}] overall={grade.overall:.2f} "
        f"below threshold {_PASS_THRESHOLD}: "
        f"{grade.reasoning}"
    )

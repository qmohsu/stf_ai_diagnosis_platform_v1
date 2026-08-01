"""Parametrized entry point for the manual-agent eval suite.

One test per ``GoldenEntry`` loaded from
``golden/v2/locked/mws150a.jsonl``.  Since HARNESS-31 (#225) the
actual execution is pipelined: the session-scoped
``pipeline_results`` fixture (see ``conftest.py`` +
``orchestrator.py``) runs the manual sub-agent for every selected
golden — serially by default, preserving single-stream GPU access
— while judge calls overlap subsequent agent runs.  Each
parametrised test here just looks up its entry's outcome, records
it in the session report, and asserts the pass threshold.  Test
ids, ``-k`` filtering, report format, and thresholds are
unchanged from the pre-pipeline suite.

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

from typing import Any, Dict

import pytest

from tests.harness.evals.conftest import (
    EvalReport,
    load_golden,
    resolve_manual_golden_path,
)
from tests.harness.evals.schemas import GoldenEntry


# Minimum overall score the agent must achieve.  Re-pinned from
# the HARNESS-23 v2 re-baseline (#155, run 2026-07-12, after the
# phase-1/2 fixes): manual_agent mean 0.670, stdev 0.176 over the
# 30 locked goldens → mean − 1·stdev = 0.494, floored to 0.4
# (numerically unchanged from the v1 pin — the mean rose but so
# did the spread).  This is a regression floor, not a quality
# target — it catches the lane falling off a cliff without
# flapping on per-entry judge noise.  v1 and v2 numbers are NOT
# comparable (rubric/weights changed in #148/#153).  See
# docs/harness_14_phase6_baseline.md.
_PASS_THRESHOLD = 0.4


# Load goldens at import time so pytest parametrization shows one
# test id per entry.  HARNESS-20: the locked tier is the canonical
# source — promote_golden.py is the only way an entry lands here.
# #234: the file is track-selected — the index track (production
# default since the HARNESS-30 cutover) grades against the makeup
# overlay's node-id anchors; MANUAL_INDEX_TRACK=off grades against
# the legacy-slug file (the A/B lane switch).
_LOCKED_ENTRIES = load_golden(resolve_manual_golden_path())

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
@pytest.mark.parametrize(
    "entry",
    _PARAM_ENTRIES,
    ids=lambda e: e.id if _LOCKED_ENTRIES else None,
)
def test_manual_agent(
    entry: GoldenEntry,
    eval_report: EvalReport,
    pipeline_results: Dict[Any, Any],
) -> None:
    """Assert the pipelined manual-agent outcome for one golden.

    Args:
        entry: One ``GoldenEntry`` from
            ``golden/v2/locked/mws150a.jsonl``.
        eval_report: Session-scoped report accumulator.
        pipeline_results: Session-scoped mapping of
            ``(system_label, entry_id)`` to pre-computed
            ``PipelineOutcome`` (HARNESS-31).
    """
    outcome = pipeline_results.get(("manual_agent", entry.id))
    assert outcome is not None, (
        f"[{entry.id}] missing from pipeline results — the "
        f"orchestrator did not schedule this item (conftest "
        f"item-selection bug?)"
    )
    if outcome.error is not None:
        raise outcome.error
    eval_report.record(
        entry, outcome.run, outcome.grade,  # type: ignore[arg-type]
    )

    assert outcome.grade.overall >= _PASS_THRESHOLD, (
        f"[{entry.id}] overall={outcome.grade.overall:.2f} "
        f"below threshold {_PASS_THRESHOLD}: "
        f"{outcome.grade.reasoning}"
    )

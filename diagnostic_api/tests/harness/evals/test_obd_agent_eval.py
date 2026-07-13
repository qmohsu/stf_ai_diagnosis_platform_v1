"""End-to-end eval entry point for the OBD sub-agent (HARNESS-21).

Parametrized over the goldens in
``golden/v1/yamaha_road_test.jsonl``.  Each test:

1. Invokes ``run_obd_agent_unified`` against the Yamaha fixture
   via the (real or mocked) OBD-agent deps.
2. Grades the result with ``grade_run`` against the golden using
   the (real or mocked) judge client.
3. Records the (entry, run, grade) triple to the session-scoped
   ``eval_report``.
4. Asserts ``grade.overall >= _PASS_THRESHOLD``.

Run modes
---------

**Plumbing verification (zero external dependencies)** — no
Ollama, no OpenRouter, no Postgres::

    pytest -m eval --run-eval --mock-agent --mock-judge \\
        tests/harness/evals/test_obd_agent_eval.py

Under ``--mock-agent``, a canned LLM client returns the same
RPM/DTC-focused response regardless of question.  This was
designed in PR [1/3] to satisfy the 3 dummy goldens that mirrored
the canned response.  PR [2a/4] added 12 more diverse real
goldens (SPEED, coolant, event_finding, dtc_decode, adversarial,
etc.) which the canned response cannot answer correctly — so
under mocks you should expect roughly **3 entries pass, 12 fail
with non-fabrication-related score drops**.  That's the right
behaviour: the mocked path verifies the pipeline doesn't crash,
not that scores are meaningful.

**Real-LLM run (PolyU server, inside the diagnostic-api
container)**::

    podman exec stf-diagnostic-api pytest -m eval --run-eval \\
        tests/harness/evals/test_obd_agent_eval.py --tb=short

The ``yamaha_session_id`` fixture idempotently bootstraps a
Postgres ``OBDAnalysisSession`` row + materialises the fixture
into ``settings.obd_log_storage_path``.  Requires Postgres
reachable AND Ollama serving the model in
``OBDAgentConfig().model``.  Pass-or-fail at ``_PASS_THRESHOLD``
is meaningful here; the resulting ``reports/eval_{ts}.json`` is
the input to PR [3/4]'s baseline scorecard.

**Ceiling run (OpenRouter model for comparison)**::

    OBD_EVAL_AGENT_MODEL=z-ai/glm-5.1 \\
        podman exec stf-diagnostic-api pytest -m eval \\
        --run-eval tests/harness/evals/test_obd_agent_eval.py

Default ``pytest`` runs skip this module entirely via the
``eval`` marker registered in ``tests/conftest.py``.

PR [3/4] will raise ``_PASS_THRESHOLD`` from 0.6 to whatever the
baseline supports + tune ``obd_agent_prompts.py`` based on
failure analysis.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import os
from typing import Any, Optional

import pytest

from tests.harness.evals.conftest import (
    EvalReport,
    load_golden,
)
from tests.harness.evals.judge import grade_run
from tests.harness.evals.obd_runner import run_obd_agent_unified
from tests.harness.evals.schemas import GoldenEntry


# ── Module-level config ──────────────────────────────────────────


_GOLDEN_FILE = os.getenv(
    "OBD_EVAL_GOLDEN_FILE",
    "v2/locked/yamaha_road_test.jsonl",
)
"""HARNESS-25 (#117) measurement switch.  The DEFAULT stays the
locked tier (empty until the workshop expert accepts candidates
at ``/goldens/obd`` — the HARNESS-20 safety net is intact).  An
explicit ``OBD_EVAL_GOLDEN_FILE=v2/yamaha_road_test.jsonl``
opts a run into the CANDIDATE tier so the baseline scorecard can
be established while expert review is pending; any report from
such a run must be labelled candidate-tier."""


_LOCKED_ENTRIES = load_golden(_GOLDEN_FILE)
"""HARNESS-21 [3/4]: reader migrated from
``v1/yamaha_road_test.jsonl`` to
``v2/locked/yamaha_road_test.jsonl`` to align with the manual
lane's HARNESS-20 safety-net policy.  The locked file is
populated by ``scripts/promote_golden.py --lane=obd`` after a
workshop expert reviews the candidate at ``/goldens/obd`` and
marks it accept-with-5★ via the UI.

Until the first OBD promotion happens, ``v2/locked/
yamaha_road_test.jsonl`` is empty.  PR [2a/4]'s pre-migration
baseline lives in ``docs/harness_21_phase5_baseline.md`` and
stands as the HARNESS-21 reference until enough OBD candidates
clear expert review to support a follow-up run from the locked
tier.

Loading at import keeps pytest IDs stable (each entry's ``id``
becomes the test ID) and surfaces JSONL schema issues during
collection rather than mid-run.
"""


# An empty locked file is the shipped initial state (no OBD
# entries promoted yet).  Parametrising on an empty list crashes
# pytest's ``ids=lambda`` evaluator, so substitute a single
# skipped placeholder explaining how to populate the tier —
# gives a clean "1 skipped" line instead of a collection error.
# Mirrors the pattern used in ``test_manual_agent_eval.py``.
_NO_LOCKED_REASON = (
    "No entries in golden/v2/locked/yamaha_road_test.jsonl yet.  "
    "Promote OBD candidates via `python -m scripts.promote_golden "
    "--lane=obd --entry-id <id> --reviewer <name> --reason <why>` "
    "after the workshop expert accepts them at /goldens/obd "
    "(HARNESS-21 [3/4])."
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


_PASS_THRESHOLD = 0.6
"""Minimum ``Grade.overall`` for an entry to pass.

Starts at 0.6 (lower than the manual lane's 0.7) because the OBD
lane has no baseline yet.  PR [3/3] re-pins this based on the
local-Qwen + GLM-5.1 baseline numbers.
"""


# ── Test ─────────────────────────────────────────────────────────


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    _PARAM_ENTRIES,
    ids=lambda e: e.id if _LOCKED_ENTRIES else None,
)
async def test_obd_agent(
    entry: GoldenEntry,
    eval_report: EvalReport,
    judge_client: Optional[Any],
    obd_agent_deps: Optional[Any],
    yamaha_session_id: str,
) -> None:
    """Run the OBD sub-agent against one golden and grade it.

    Args:
        entry: One ``GoldenEntry`` from the v1 Yamaha goldens.
        eval_report: Session-scoped report accumulator.
        judge_client: Real or mocked judge client.
        obd_agent_deps: Real or mocked OBD agent deps.
        yamaha_session_id: Stable session UUID for the fixture.
    """
    run = await run_obd_agent_unified(
        inquiry=entry.question,
        session_id=yamaha_session_id,
        deps=obd_agent_deps,
    )
    grade = await grade_run(entry, run, client=judge_client)
    eval_report.record(entry, run, grade)  # type: ignore[arg-type]
    assert grade.overall >= _PASS_THRESHOLD, (
        f"{entry.id} scored {grade.overall:.3f} < "
        f"{_PASS_THRESHOLD} — {grade.reasoning}"
    )

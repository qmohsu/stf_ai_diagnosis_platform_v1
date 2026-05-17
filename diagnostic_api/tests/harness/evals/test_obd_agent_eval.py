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

PR [1/3] verification command (zero external dependencies — no
Ollama, no OpenRouter, no DB)::

    pytest -m eval --run-eval --mock-agent --mock-judge \\
        tests/harness/evals/test_obd_agent_eval.py

Real-LLM runs are deferred to PR [2/3] (the goldens here are
"dummies" that mirror what the mock OBD agent returns).  PR [3/3]
will raise ``_PASS_THRESHOLD`` from 0.6 to whatever the baseline
supports.

Default ``pytest`` runs skip this module entirely via the
``eval`` marker registered in ``tests/conftest.py``.

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
from tests.harness.evals.obd_runner import run_obd_agent_unified
from tests.harness.evals.schemas import GoldenEntry


# ── Module-level config ──────────────────────────────────────────


_V1_YAMAHA_ENTRIES = load_golden("v1/yamaha_road_test.jsonl")
"""Parametrize ID list, resolved at import time.

Loading at import keeps pytest IDs stable (each entry's ``id``
becomes the test ID) and surfaces JSONL schema issues during
collection rather than mid-run.
"""


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
    "entry", _V1_YAMAHA_ENTRIES, ids=lambda e: e.id,
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

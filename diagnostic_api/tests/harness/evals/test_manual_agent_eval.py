"""Parametrized entry point for the manual-agent eval suite.

One test per ``GoldenEntry`` loaded from ``golden/v1/mws150a.jsonl``.
Each test runs the manual sub-agent against the entry's question,
grades the output via the LLM judge, records the triple in the
session-scoped ``eval_report``, and asserts ``grade.overall >=
0.7``.

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
from tests.harness.evals.judge import judge_result
from tests.harness.evals.runner import run_manual_agent
from tests.harness.evals.schemas import GoldenEntry


# Minimum overall score the agent must achieve.  Revisit in Phase
# 5 after a real baseline run; start lenient so plumbing tests
# pass with stub-perfect output.
_PASS_THRESHOLD = 0.7


# Load goldens at import time so pytest parametrization shows one
# test id per entry.  Errors surface as collection failures.
_V1_ENTRIES = load_golden("v1/mws150a.jsonl")


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    _V1_ENTRIES,
    ids=lambda e: e.id,
)
async def test_manual_agent(
    entry: GoldenEntry,
    eval_report: EvalReport,
    judge_client: Optional[Any],
    manual_agent_deps: Optional[Any],
) -> None:
    """Run the manual agent and grade it against the golden entry.

    Args:
        entry: One ``GoldenEntry`` from ``golden/v1/mws150a.jsonl``.
        eval_report: Session-scoped report accumulator.
        judge_client: ``None`` for the real GLM 5.1 judge, or a
            mock client when ``--mock-judge`` is passed.
        manual_agent_deps: ``None`` for real deps pointing at
            local Ollama, or a stub deps object when
            ``--mock-agent`` is passed.
    """
    result = await run_manual_agent(
        entry.question, entry.obd_context,
        deps=manual_agent_deps,
    )
    grade = await judge_result(entry, result, client=judge_client)
    eval_report.record(entry, result, grade)

    assert grade.overall >= _PASS_THRESHOLD, (
        f"[{entry.id}] overall={grade.overall:.2f} "
        f"below threshold {_PASS_THRESHOLD}: "
        f"{grade.reasoning}"
    )

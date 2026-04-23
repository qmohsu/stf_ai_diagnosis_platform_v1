"""LLM-as-judge wrapper — Phase 1 stub.

Stable entry point for the eval harness::

    grade = await judge_result(entry, agent_result)

In Phase 1, this returns a fixed passing ``Grade`` so the
parametrized pytest suite can verify the full report pipeline
without making any LLM calls.  In Phase 2 the implementation
will call ``z-ai/glm-5.1`` via OpenRouter with temperature 0
and ``response_format={"type": "json_object"}``; Pydantic will
validate the JSON and retry once on parse failure per the
project-wide error-handling rule.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import structlog

from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    ManualAgentResult,
)

logger = structlog.get_logger(__name__)


async def judge_result(
    entry: GoldenEntry,
    result: ManualAgentResult,
) -> Grade:
    """Grade an agent result against its golden entry.

    Phase 1 stub: returns a perfect score so the eval harness
    plumbing (pytest parametrization, report writer) can be
    verified without LLM cost.  In Phase 2, this will call the
    GLM 5.1 judge with a structured rubric prompt and parse
    the JSON response into a ``Grade``.

    Args:
        entry: The golden entry for this question.
        result: The agent's output for this question.

    Returns:
        A ``Grade`` summarising the judge's rubric scores.
    """
    logger.info(
        "judge_stub_invoked",
        entry_id=entry.id,
        category=entry.category,
        agent_iterations=result.iterations,
    )
    return Grade(
        section_match=1,
        fact_recall=1.0,
        hallucination=0,
        citation_present=1,
        trajectory_ok=1,
        overall=1.0,
        reasoning=(
            "Phase 1 stub — returns a passing grade regardless "
            "of agent output.  Replace with real GLM 5.1 judge "
            "call in Phase 2."
        ),
    )

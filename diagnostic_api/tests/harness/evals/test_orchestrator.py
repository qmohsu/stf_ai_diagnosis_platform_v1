"""Unit tests for the pipelined eval orchestrator (HARNESS-31).

Pure-asyncio tests over ``orchestrator.execute`` with stub run
callables and a stub grade function — no LLM, no DB, no app-stack
imports (the orchestrator deliberately imports neither the agent
runner nor ``lanes.py``).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from tests.harness.evals.orchestrator import (
    PipelineOutcome,
    WorkItem,
    execute,
    run_pipeline,
)
from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    SystemRunResult,
)


def _entry(entry_id: str) -> GoldenEntry:
    """Build a minimal valid golden entry for orchestration tests."""
    return GoldenEntry(
        id=entry_id,
        category="dtc",
        question_type="lookup",
        difficulty="easy",
        question=f"question for {entry_id}",
        golden_summary="reference answer",
    )


def _run_result(label: str, question: str) -> SystemRunResult:
    """Build a minimal ``SystemRunResult`` for a stub run."""
    return SystemRunResult(
        system_label=label,  # type: ignore[arg-type]
        question=question,
        output_text=f"answer to {question}",
    )


def _grade(overall: float = 0.9) -> Grade:
    """Build a minimal valid ``Grade``."""
    return Grade(
        exploration_cost=0.0,
        fact_recall=1.0,
        fact_density=1.0,
        hallucination_penalty=0.0,
        citation_quality=1.0,
        answer_quality=overall,
        overall=overall,
        reasoning="[stub grade]",
    )


def _items(
    count: int,
    label: str = "manual_agent",
    run_delay: float = 0.0,
) -> List[WorkItem]:
    """Build ``count`` stub work items with optional run delay."""

    def _make(entry_id: str) -> WorkItem:
        async def _run() -> SystemRunResult:
            if run_delay:
                await asyncio.sleep(run_delay)
            return _run_result(label, f"question for {entry_id}")

        return WorkItem(
            system_label=label,
            entry=_entry(entry_id),
            run_fn=_run,
        )

    return [_make(f"g-{i:03d}") for i in range(count)]


async def _stub_grade_fn(entry, run, client=None):  # noqa: ANN001
    """Stub grader returning a fixed passing grade."""
    return _grade()


@pytest.mark.asyncio
async def test_execute_returns_outcome_per_item() -> None:
    """Every scheduled item gets a keyed, graded outcome."""
    items = _items(3)
    results = await execute(items, grade_fn=_stub_grade_fn)

    assert set(results) == {
        ("manual_agent", "g-000"),
        ("manual_agent", "g-001"),
        ("manual_agent", "g-002"),
    }
    for outcome in results.values():
        assert outcome.error is None
        assert outcome.run is not None
        assert outcome.grade is not None
        assert outcome.grade.overall == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_execute_keys_disambiguate_lanes() -> None:
    """Same entry id under two lanes yields two distinct keys."""
    manual = _items(1, label="manual_agent")
    rag = _items(1, label="rag")
    results = await execute(
        manual + rag, grade_fn=_stub_grade_fn,
    )

    assert ("manual_agent", "g-000") in results
    assert ("rag", "g-000") in results
    assert (
        results[("manual_agent", "g-000")].run.system_label
        == "manual_agent"
    )
    assert results[("rag", "g-000")].run.system_label == "rag"


@pytest.mark.asyncio
async def test_run_concurrency_is_respected() -> None:
    """At most ``run_concurrency`` run phases execute at once."""
    in_flight = 0
    max_in_flight = 0

    def _make(entry_id: str) -> WorkItem:
        async def _run() -> SystemRunResult:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return _run_result("manual_agent", entry_id)

        return WorkItem(
            system_label="manual_agent",
            entry=_entry(entry_id),
            run_fn=_run,
        )

    items = [_make(f"g-{i:03d}") for i in range(6)]
    await execute(
        items, run_concurrency=2, grade_fn=_stub_grade_fn,
    )
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_judge_overlaps_next_run() -> None:
    """With serial runs, a slow judge must not block the next run.

    Deterministic overlap proof: item g-000's judge call blocks on
    an event that ONLY item g-001's run phase sets.  If judging
    were still serialised into the critical path (pre-HARNESS-31
    behaviour), this would deadlock — guarded by wait_for.
    """
    second_run_started = asyncio.Event()

    async def _run_first() -> SystemRunResult:
        return _run_result("manual_agent", "first")

    async def _run_second() -> SystemRunResult:
        second_run_started.set()
        return _run_result("manual_agent", "second")

    async def _grade_fn(entry, run, client=None):  # noqa: ANN001
        if entry.id == "g-000":
            await second_run_started.wait()
        return _grade()

    items = [
        WorkItem("manual_agent", _entry("g-000"), _run_first),
        WorkItem("manual_agent", _entry("g-001"), _run_second),
    ]
    results = await asyncio.wait_for(
        execute(items, run_concurrency=1, grade_fn=_grade_fn),
        timeout=5.0,
    )
    assert all(o.error is None for o in results.values())


@pytest.mark.asyncio
async def test_item_error_is_isolated() -> None:
    """A raising run fails only its own item, others complete."""

    async def _boom() -> SystemRunResult:
        raise RuntimeError("agent exploded")

    items = _items(2)
    items.insert(1, WorkItem(
        system_label="manual_agent",
        entry=_entry("g-bad"),
        run_fn=_boom,
    ))

    results = await execute(items, grade_fn=_stub_grade_fn)

    bad = results[("manual_agent", "g-bad")]
    assert isinstance(bad.error, RuntimeError)
    assert bad.grade is None
    for key in (("manual_agent", "g-000"), ("manual_agent", "g-001")):
        assert results[key].error is None
        assert results[key].grade is not None


@pytest.mark.asyncio
async def test_judge_error_is_captured() -> None:
    """A raising judge is captured; the run result is retained."""

    async def _grade_fn(entry, run, client=None):  # noqa: ANN001
        raise ValueError("judge exploded")

    results = await execute(_items(1), grade_fn=_grade_fn)
    outcome = results[("manual_agent", "g-000")]
    assert isinstance(outcome.error, ValueError)
    assert outcome.run is not None
    assert outcome.grade is None


@pytest.mark.asyncio
async def test_invalid_concurrency_raises() -> None:
    """Concurrency knobs below 1 are rejected up front."""
    with pytest.raises(ValueError, match="concurrency"):
        await execute(_items(1), run_concurrency=0)
    with pytest.raises(ValueError, match="concurrency"):
        await execute(_items(1), judge_concurrency=0)


def test_run_pipeline_sync_wrapper(monkeypatch) -> None:  # noqa: ANN001
    """``run_pipeline`` executes outside a running loop via asyncio.run."""
    import tests.harness.evals.orchestrator as orch

    monkeypatch.setattr(orch, "grade_run", _stub_grade_fn)
    results = run_pipeline(_items(2))
    assert len(results) == 2
    assert all(
        isinstance(o, PipelineOutcome) and o.error is None
        for o in results.values()
    )

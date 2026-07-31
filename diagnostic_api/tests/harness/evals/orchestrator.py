"""Session-level pipelined orchestrator for the eval lanes.

HARNESS-31 (#225): a full 30-golden manual-lane run was ~55 min
because the pytest parametrisation executed each golden strictly
serially — agent run (~70-100 s) THEN judge call (~15-30 s), one
test at a time.  The judge calls are independent OpenRouter
requests, so serialising them behind the GPU-bound agent runs
wasted ~10-15 min per run.

This module decouples the two phases:

- **Run phase** (GPU-bound): each work item's ``run_fn`` executes
  under a semaphore of ``run_concurrency`` slots.  The default of
  1 preserves today's single-stream GPU access exactly — per-run
  wall-clock latencies stay meaningful as single-user numbers and
  scores are byte-comparable with pre-HARNESS-31 runs.  Values
  > 1 are the direction-2 knob (concurrent agent runs) and require
  ``OLLAMA_NUM_PARALLEL`` > 1 server-side to actually help.
- **Judge phase** (network-bound): as soon as an item's run
  completes it is judged under a separate ``judge_concurrency``
  semaphore — concurrently with the NEXT item's run phase.  The
  judge therefore falls off the critical path entirely: total
  wall clock ≈ sum(agent runs) + last judge call.

The orchestrator is driven once per pytest session by the
``pipeline_results`` fixture (see ``conftest.py``); the
parametrised tests then just look up their item's outcome and
assert.  Report format, test ids, ``-k`` filtering, and pass
thresholds are unchanged.

Deliberately imports nothing from ``app.harness_agents`` /
``runner.py`` — run callables are injected by ``lanes.py`` — so
this module (and its unit tests) stay importable offline
(tiktoken download gotcha, see .claude/memory.md).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from tests.harness.evals.judge import grade_run
from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    SystemRunResult,
)

ResultKey = Tuple[str, str]
"""``(system_label, entry_id)`` — unique per work item."""


@dataclass
class WorkItem:
    """One (lane, golden) pair to run and grade.

    Attributes:
        system_label: ``"manual_agent"`` or ``"rag"`` — must match
            the ``SystemRunResult.system_label`` the run produces.
        entry: The golden entry to grade against.
        run_fn: Zero-arg async callable executing the system under
            test and returning its ``SystemRunResult``.  Built by
            ``lanes.py`` so the orchestrator stays app-import-free.
    """

    system_label: str
    entry: GoldenEntry
    run_fn: Callable[[], Awaitable[SystemRunResult]]


@dataclass
class PipelineOutcome:
    """Result envelope for one work item.

    Exactly one of (``grade``, ``error``) is set on completion:
    a raised exception in either phase is captured here and
    re-raised by the owning test, mirroring the pre-pipeline
    behaviour where the exception propagated out of the test body.

    Attributes:
        run: The system run result (``None`` if the run raised).
        grade: The judge's grade (``None`` if either phase raised).
        error: Captured exception, if any.
        run_seconds: Wall-clock duration of the run phase.
        judge_seconds: Wall-clock duration of the judge phase.
    """

    run: Optional[SystemRunResult] = None
    grade: Optional[Grade] = None
    error: Optional[Exception] = None
    run_seconds: float = 0.0
    judge_seconds: float = 0.0


def _progress(message: str) -> None:
    """Emit a live progress line to stderr.

    pytest captures stdio during fixture setup, so these lines are
    only visible when the session runs with ``-s`` /
    ``--capture=no`` — which ``run_eval.sh`` now passes.  Flushed
    so ``tee``'d run logs update in real time during the ~30 min
    session.
    """
    print(f"[pipeline] {message}", file=sys.stderr, flush=True)


async def execute(
    items: List[WorkItem],
    run_concurrency: int = 1,
    judge_concurrency: int = 4,
    judge_client: Optional[Any] = None,
    grade_fn: Optional[Callable[..., Awaitable[Grade]]] = None,
) -> Dict[ResultKey, PipelineOutcome]:
    """Run and grade all work items with pipelined judging.

    Args:
        items: Work items in desired run order.  Under
            ``run_concurrency=1`` the run phase executes in exactly
            this order (semaphore waiters are FIFO).
        run_concurrency: Max system runs in flight (GPU-bound
            phase).  Keep at 1 unless Ollama is configured for
            parallel slots.
        judge_concurrency: Max judge calls in flight (network-bound
            phase).
        judge_client: Optional pre-built judge client, forwarded to
            ``grade_fn`` (``None`` → the judge builds its own).
        grade_fn: Grading coroutine; parameterised for unit tests.
            ``None`` resolves to the real ``judge.grade_run`` at
            call time (so tests can monkeypatch the module
            attribute).

    Returns:
        Mapping of ``(system_label, entry_id)`` to outcome — one
        key per input item, always present even on failure.

    Raises:
        ValueError: If either concurrency knob is < 1.
    """
    if run_concurrency < 1 or judge_concurrency < 1:
        raise ValueError(
            f"concurrency knobs must be >= 1, got "
            f"run={run_concurrency} judge={judge_concurrency}",
        )
    if grade_fn is None:
        # Module-attribute lookup at call time (not a bound
        # default) so tests can monkeypatch ``grade_run``.
        grade_fn = grade_run

    run_sema = asyncio.Semaphore(run_concurrency)
    judge_sema = asyncio.Semaphore(judge_concurrency)
    results: Dict[ResultKey, PipelineOutcome] = {}
    done_count = 0
    total = len(items)
    started = time.perf_counter()

    async def _one(item: WorkItem) -> None:
        nonlocal done_count
        key = (item.system_label, item.entry.id)
        outcome = PipelineOutcome()
        results[key] = outcome
        try:
            async with run_sema:
                t0 = time.perf_counter()
                outcome.run = await item.run_fn()
                outcome.run_seconds = time.perf_counter() - t0
            # Deliberately OUTSIDE the run semaphore: the next
            # item's run phase proceeds while this judge call is
            # awaiting OpenRouter.
            async with judge_sema:
                t0 = time.perf_counter()
                outcome.grade = await grade_fn(
                    item.entry, outcome.run, client=judge_client,
                )
                outcome.judge_seconds = time.perf_counter() - t0
        except Exception as exc:  # pylint: disable=broad-except
            # Captured per-item and re-raised by the owning test —
            # one broken golden must not sink the whole session.
            outcome.error = exc
        done_count += 1
        if outcome.error is not None:
            _progress(
                f"{done_count}/{total} {key[0]}:{key[1]} "
                f"ERROR {type(outcome.error).__name__}: "
                f"{outcome.error}",
            )
        else:
            _progress(
                f"{done_count}/{total} {key[0]}:{key[1]} "
                f"overall={outcome.grade.overall:.3f} "
                f"run={outcome.run_seconds:.1f}s "
                f"judge={outcome.judge_seconds:.1f}s",
            )

    _progress(
        f"starting {total} items "
        f"(run-concurrency={run_concurrency}, "
        f"judge-concurrency={judge_concurrency})",
    )
    await asyncio.gather(*(_one(item) for item in items))
    _progress(
        f"all {total} items done in "
        f"{time.perf_counter() - started:.1f}s",
    )
    return results


def run_pipeline(
    items: List[WorkItem],
    run_concurrency: int = 1,
    judge_concurrency: int = 4,
    judge_client: Optional[Any] = None,
) -> Dict[ResultKey, PipelineOutcome]:
    """Synchronous entry point for the session fixture.

    Wraps ``execute`` in ``asyncio.run`` — safe because pytest
    fixture setup runs outside any event loop (pytest-asyncio
    creates per-test loops only around test coroutines).

    Args:
        items: Work items in desired run order.
        run_concurrency: Max system runs in flight.
        judge_concurrency: Max judge calls in flight.
        judge_client: Optional pre-built judge client.

    Returns:
        Same mapping as ``execute``.
    """
    return asyncio.run(execute(
        items,
        run_concurrency=run_concurrency,
        judge_concurrency=judge_concurrency,
        judge_client=judge_client,
    ))

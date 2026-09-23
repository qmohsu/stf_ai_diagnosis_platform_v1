# Adapted from diagnostic_api/tests/harness/evals/orchestrator.py @ 9c9e7a9
# (PROD-10).  The V2 API (``WorkItem`` / ``PipelineOutcome`` / ``execute``)
# is kept so V2's orchestrator tests still apply; V3 adds a per-item hard
# timeout, a delayed re-judge on judge failure, per-item status, run
# statistics and a completion callback (incremental writes).
"""Pipelined run + judge orchestrator for the golden eval.

The run phase (GPU-bound: one sub-agent run per golden) executes under
``run_concurrency`` slots; each finished run is judged under a separate
``judge_concurrency`` semaphore while the next runs proceed, so the judge
falls off the critical path (V2 HARNESS-31).

V3 additions (PROD-10):

* ``item_timeout_s`` -- an eval-level hard stop per golden (FM-32 / FM-48).
  The sub-agent has its own wall clock; this only catches a run that
  never returns.  A hit marks the item ``eval_timeout`` (scorecard
  invalid), the rest of the batch continues.
* ``judge_retry_delays`` -- the judge already retries once immediately and
  then falls back to ``[judge failure]`` with answer_quality 0 (V2
  behaviour).  Here the whole grading is retried after each delay
  (429 / provider hiccups, FM-22); still failing → ``judge_failed``
  (scorecard invalid, never a silent zero, FM-19 / FM-44).
* ``on_done`` -- called once per finished item (progress line + the
  incremental scorecard write, FM-14 / FM-29).

Author: Li-Ta Hsu (V2 pipeline) / Xiangzhu Yan (V3 additions)
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union

from stf_v3.evals.judge import grade_run
from stf_v3.evals.schemas import GoldenEntry, Grade, SystemRunResult

ResultKey = Tuple[str, str]
"""``(system_label, entry_id)`` — unique per work item."""

JUDGE_FAILURE_MARK = "[judge failure]"

STATUS_OK = "ok"
STATUS_JUDGE_FAILED = "judge_failed"
STATUS_EVAL_TIMEOUT = "eval_timeout"
STATUS_ERROR = "error"

RunReturn = Union[SystemRunResult, Tuple[SystemRunResult, Any]]


class EvalTimeout(RuntimeError):
    """The system under test did not return within the eval-level hard limit."""


@dataclass
class WorkItem:
    """One (lane, golden) pair to run and grade.

    Attributes:
        system_label: ``"manual_agent"`` or ``"obd_agent"`` — must match
            the ``SystemRunResult.system_label`` the run produces.
        entry: The golden entry to grade against.
        run_fn: Zero-arg async callable executing the system under test;
            returns a ``SystemRunResult`` or ``(SystemRunResult, stats)``.
    """

    system_label: str
    entry: GoldenEntry
    run_fn: Callable[[], Awaitable[RunReturn]]


@dataclass
class PipelineOutcome:
    """Result envelope for one work item.

    Attributes:
        run: The system run result (``None`` if the run raised).
        grade: The grade (``None`` if either phase raised).
        error: Captured exception, if any.
        run_seconds: Wall-clock duration of the run phase.
        judge_seconds: Wall-clock duration of the judge phase.
        stats: Run statistics returned by the lane (``ItemStats``).
        status: ``ok`` / ``judge_failed`` / ``eval_timeout`` / ``error``.
        judge_attempts: Number of ``grade_fn`` calls made.
    """

    run: Optional[SystemRunResult] = None
    grade: Optional[Grade] = None
    error: Optional[Exception] = None
    run_seconds: float = 0.0
    judge_seconds: float = 0.0
    stats: Any = None
    status: str = STATUS_OK
    judge_attempts: int = 0


def is_judge_failure(grade: Optional[Grade]) -> bool:
    """True when the judge fell back to its ``[judge failure]`` default."""
    return grade is not None and JUDGE_FAILURE_MARK in (grade.reasoning or "")


def _progress(message: str) -> None:
    """Live progress line on stderr (visible in ``podman logs -f``)."""
    print(f"[pipeline] {message}", file=sys.stderr, flush=True)


async def _call_maybe_async(fn: Callable[..., Any], *args: Any) -> None:
    res = fn(*args)
    if inspect.isawaitable(res):
        await res


async def execute(
    items: List[WorkItem],
    run_concurrency: int = 1,
    judge_concurrency: int = 4,
    judge_client: Optional[Any] = None,
    grade_fn: Optional[Callable[..., Awaitable[Grade]]] = None,
    *,
    item_timeout_s: Optional[float] = None,
    judge_retry_delays: Sequence[float] = (),
    on_done: Optional[Callable[[ResultKey, PipelineOutcome], Any]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Dict[ResultKey, PipelineOutcome]:
    """Run and grade all work items with pipelined judging.

    Args:
        items: Work items in run order.
        run_concurrency: Max system runs in flight (GPU-bound phase).
        judge_concurrency: Max judge calls in flight.
        judge_client: Optional pre-built judge client for ``grade_fn``.
        grade_fn: Grading coroutine; ``None`` → ``judge.grade_run``
            (looked up at call time so tests can monkeypatch it).
        item_timeout_s: Eval-level hard limit per run (``None`` = none).
        judge_retry_delays: Seconds to wait before each extra grading
            attempt after a ``[judge failure]``.
        on_done: Called with ``(key, outcome)`` when an item finishes.
        sleep: Injected for tests.

    Returns:
        ``(system_label, entry_id)`` → outcome, one key per input item.

    Raises:
        ValueError: If either concurrency knob is < 1.
    """
    if run_concurrency < 1 or judge_concurrency < 1:
        raise ValueError(
            f"concurrency knobs must be >= 1, got "
            f"run={run_concurrency} judge={judge_concurrency}",
        )
    if grade_fn is None:
        # Module-attribute lookup at call time (tests monkeypatch it).
        grade_fn = grade_run

    run_sema = asyncio.Semaphore(run_concurrency)
    judge_sema = asyncio.Semaphore(judge_concurrency)
    results: Dict[ResultKey, PipelineOutcome] = {}
    done_count = 0
    total = len(items)
    started = time.perf_counter()

    async def _grade(item: WorkItem, outcome: PipelineOutcome) -> None:
        assert grade_fn is not None and outcome.run is not None
        delays = list(judge_retry_delays)
        while True:
            outcome.judge_attempts += 1
            outcome.grade = await grade_fn(item.entry, outcome.run, client=judge_client)
            if not is_judge_failure(outcome.grade) or not delays:
                break
            await sleep(delays.pop(0))
        if is_judge_failure(outcome.grade):
            outcome.status = STATUS_JUDGE_FAILED

    async def _one(item: WorkItem) -> None:
        nonlocal done_count
        key = (item.system_label, item.entry.id)
        outcome = PipelineOutcome()
        results[key] = outcome
        try:
            async with run_sema:
                t0 = time.perf_counter()
                try:
                    if item_timeout_s:
                        res = await asyncio.wait_for(item.run_fn(), timeout=item_timeout_s)
                    else:
                        res = await item.run_fn()
                except asyncio.TimeoutError as exc:
                    raise EvalTimeout(f"run exceeded the eval hard limit of {item_timeout_s:.0f}s") from exc
                finally:
                    outcome.run_seconds = time.perf_counter() - t0
                if isinstance(res, tuple):
                    outcome.run, outcome.stats = res
                else:
                    outcome.run = res
            # Outside the run semaphore: the next run proceeds meanwhile.
            async with judge_sema:
                t0 = time.perf_counter()
                await _grade(item, outcome)
                outcome.judge_seconds = time.perf_counter() - t0
        except EvalTimeout as exc:
            outcome.error = exc
            outcome.status = STATUS_EVAL_TIMEOUT
        except Exception as exc:  # pylint: disable=broad-except
            # One broken golden must not sink the whole batch.
            outcome.error = exc
            outcome.status = STATUS_ERROR
        done_count += 1
        if outcome.error is not None:
            _progress(f"{done_count}/{total} {key[0]}:{key[1]} {outcome.status.upper()} "
                      f"{type(outcome.error).__name__}: {outcome.error}")
        else:
            assert outcome.grade is not None
            _progress(f"{done_count}/{total} {key[0]}:{key[1]} overall={outcome.grade.overall:.3f} "
                      f"run={outcome.run_seconds:.1f}s judge={outcome.judge_seconds:.1f}s"
                      f"{' ' + outcome.status.upper() if outcome.status != STATUS_OK else ''}")
        if on_done is not None:
            await _call_maybe_async(on_done, key, outcome)

    _progress(f"starting {total} items (run-concurrency={run_concurrency}, "
              f"judge-concurrency={judge_concurrency})")
    await asyncio.gather(*(_one(item) for item in items))
    _progress(f"all {total} items done in {time.perf_counter() - started:.1f}s")
    return results


def run_pipeline(
    items: List[WorkItem],
    run_concurrency: int = 1,
    judge_concurrency: int = 4,
    judge_client: Optional[Any] = None,
) -> Dict[ResultKey, PipelineOutcome]:
    """Synchronous entry point (V2 API)."""
    return asyncio.run(execute(
        items,
        run_concurrency=run_concurrency,
        judge_concurrency=judge_concurrency,
        judge_client=judge_client,
    ))


__all__ = [
    "EvalTimeout", "JUDGE_FAILURE_MARK", "PipelineOutcome", "ResultKey", "STATUS_ERROR",
    "STATUS_EVAL_TIMEOUT", "STATUS_JUDGE_FAILED", "STATUS_OK", "WorkItem", "execute",
    "is_judge_failure", "run_pipeline",
]

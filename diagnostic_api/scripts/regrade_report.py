"""Re-grade a stored eval report's runs with the current judge.

Triage tool for issue #234 (manual-lane regression 0.891 → 0.790
between 2026-07-29 and 2026-07-31).  Every eval report serialises
the full ``(entry, result, grade)`` triple per golden, so the
system outputs from an old run can be re-fed to TODAY's judge
without touching the GPU:

- If the fresh grades reproduce the stored ones, the judge is
  stable and the regression must come from changed content /
  agent behaviour (e.g. the #230 sidecar redeploy).
- If the fresh grades drop on identical inputs, the judge itself
  drifted (the OpenRouter-served model changed) and every
  cross-day comparison is suspect until re-baselined.

Deterministic rubric dimensions are recomputed from the stored
run and cancel out — any per-entry delta is attributable to the
judge's ``answer_quality`` / pitfall verdicts.

Run inside the eval container (mirrors run_eval.sh's mounts)::

    podman run --rm \\
      -v $API_DIR/tests:/app/tests \\
      -v $API_DIR/scripts:/app/scripts:ro \\
      --env-file <captured-env> -e PYTHONPATH=/app \\
      --network host localhost/stf-diagnostic-api:0.1.0 \\
      python /app/scripts/regrade_report.py \\
        /app/tests/harness/evals/reports/<report>.json

Author: Li-Ta Hsu
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from typing import Any, Dict, List, Tuple

from tests.harness.evals.judge import grade_run
from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    SystemRunResult,
)

_JUDGE_CONCURRENCY = 6
"""Parallel judge calls — same ballpark as the eval pipeline."""


def _load_records(
    path: str, lane: str,
) -> List[Dict[str, Any]]:
    """Load one lane's records from a report JSON.

    Args:
        path: Report file path.
        lane: ``system_label`` to filter on.

    Returns:
        Raw record dicts in file order.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [
        rec for rec in payload.get("records", [])
        if rec.get("result", {}).get("system_label") == lane
    ]


async def _regrade_one(
    rec: Dict[str, Any],
    sema: asyncio.Semaphore,
) -> Tuple[str, float, Grade]:
    """Re-grade one stored record with the current judge.

    Args:
        rec: Raw ``{entry, result, grade}`` record.
        sema: Concurrency limiter.

    Returns:
        ``(entry_id, stored_overall, fresh_grade)``.
    """
    entry = GoldenEntry.model_validate(rec["entry"])
    run = SystemRunResult.model_validate(rec["result"])
    stored = float(rec["grade"]["overall"])
    async with sema:
        fresh = await grade_run(entry, run)
    return entry.id, stored, fresh


async def _main_async(args: argparse.Namespace) -> int:
    """Re-grade all selected records and print the comparison.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    records = _load_records(args.report, args.lane)
    if args.limit:
        records = records[: args.limit]
    if not records:
        print(f"no '{args.lane}' records in {args.report}")
        return 1

    sema = asyncio.Semaphore(_JUDGE_CONCURRENCY)
    results = await asyncio.gather(*(
        _regrade_one(rec, sema) for rec in records
    ))

    stored_all: List[float] = []
    fresh_all: List[float] = []
    print(
        f"{'entry':<22} {'stored':>7} {'fresh':>7} "
        f"{'delta':>7}  fresh_aq",
    )
    for entry_id, stored, fresh in results:
        short = entry_id.split("-", 5)[-1]
        stored_all.append(stored)
        fresh_all.append(fresh.overall)
        print(
            f"{short:<22} {stored:>7.3f} {fresh.overall:>7.3f} "
            f"{fresh.overall - stored:>+7.3f}  "
            f"{fresh.answer_quality:.3f}",
        )

    print(
        f"\nstored mean={statistics.mean(stored_all):.3f}  "
        f"fresh mean={statistics.mean(fresh_all):.3f}  "
        f"mean delta="
        f"{statistics.mean(fresh_all) - statistics.mean(stored_all):+.4f}",
    )
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Re-grade a stored eval report with the current judge "
            "(issue #234 triage)."
        ),
    )
    parser.add_argument("report", help="Path to eval report JSON.")
    parser.add_argument(
        "--lane",
        default="manual_agent",
        help="system_label to re-grade (default: manual_agent).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Re-grade only the first N records (0 = all).",
    )
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

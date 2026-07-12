"""Aggregate a HARNESS-23 combined eval report into baseline stats.

One-off analysis tool for the agent-vs-RAG baseline (issue #107).
Reads a combined ``eval_{ts}.json`` report (both ``manual_agent``
and ``rag`` lanes written into one session-scoped report) and emits:

  * Per-lane mean / median / stdev / min / max of ``Grade.overall``
    plus pass-rate at a reference threshold.
  * Per-lane per-dimension means (the 9 weighted dims + the two
    reported-only dims).
  * Per-``question_type`` x lane ``overall`` means (the comparison
    matrix the ticket asks for).
  * A flat per-entry table (id, qtype, lane, overall, key subscores,
    stopped_reason, iterations) for manual failure attribution.
  * A suggested ``_PASS_THRESHOLD`` per lane: ``mean - 1*stdev``
    floored to one decimal (the rule-of-thumb from the ticket).

Pure stdlib so it runs anywhere::

    python docs/eval-reports/aggregate_phase6.py \\
        docs/eval-reports/phase6_baseline_eval.json

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from typing import Any, Dict, List


_LANES = ("manual_agent", "rag")
_QTYPES = (
    "lookup",
    "procedural",
    "cross-section",
    "image-required",
    "adversarial",
)
# Dimensions that feed ``overall`` (weights from metrics.py), plus
# the two reported-only dims at the end.
_DIMS = (
    "section_recall",
    "claim_precision",
    "exploration_cost",
    "fact_recall",
    "fact_density",
    "hallucination_penalty",
    "citation_quality",
    "value_accuracy",
    "answer_quality",
    "trajectory_efficiency",
)
_REF_THRESHOLD = 0.7


def _floor_1dp(value: float) -> float:
    """Floor a value to one decimal place."""
    return math.floor(value * 10) / 10.0


def _stats(values: List[float]) -> Dict[str, float]:
    """Mean / median / stdev / min / max for a list of floats."""
    if not values:
        return {
            "n": 0, "mean": 0.0, "median": 0.0,
            "stdev": 0.0, "min": 0.0, "max": 0.0,
        }
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "stdev": stdev,
        "min": min(values),
        "max": max(values),
    }


def _load(path: str) -> List[Dict[str, Any]]:
    """Load report records from the combined report JSON."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("records", [])


def main(path: str) -> None:
    """Print the full baseline aggregation for ``path``."""
    records = _load(path)
    by_lane: Dict[str, List[Dict[str, Any]]] = {
        lane: [] for lane in _LANES
    }
    for rec in records:
        lane = rec["result"].get("system_label", "?")
        by_lane.setdefault(lane, []).append(rec)

    print(f"# Phase 6 baseline aggregation\n")
    print(f"source: {path}")
    print(f"total grades: {len(records)}")
    for lane in _LANES:
        print(f"  {lane}: {len(by_lane.get(lane, []))} entries")
    print()

    # ── Per-lane overall summary ─────────────────────────────────
    print("## Per-lane Grade.overall\n")
    header = (
        "| lane | n | mean | median | stdev | min | max | "
        f"pass@{_REF_THRESHOLD} | suggested threshold |"
    )
    print(header)
    print("|" + "---|" * 9)
    suggested: Dict[str, float] = {}
    for lane in _LANES:
        recs = by_lane.get(lane, [])
        overalls = [r["grade"]["overall"] for r in recs]
        s = _stats(overalls)
        passes = sum(1 for v in overalls if v >= _REF_THRESHOLD)
        thr = _floor_1dp(max(0.0, s["mean"] - s["stdev"]))
        suggested[lane] = thr
        pass_rate = (passes / s["n"]) if s["n"] else 0.0
        print(
            f"| {lane} | {s['n']} | {s['mean']:.3f} | "
            f"{s['median']:.3f} | {s['stdev']:.3f} | {s['min']:.3f} "
            f"| {s['max']:.3f} | {passes}/{s['n']} "
            f"({pass_rate:.0%}) | {thr:.1f} |"
        )
    print()

    # ── Per-lane per-dimension means ─────────────────────────────
    print("## Per-lane dimension means\n")
    print("| dimension | " + " | ".join(_LANES) + " |")
    print("|" + "---|" * (len(_LANES) + 1))
    for dim in _DIMS:
        cells = []
        for lane in _LANES:
            recs = by_lane.get(lane, [])
            vals = [r["grade"].get(dim, float("nan")) for r in recs]
            # ``None`` = N/A (#148: adversarial section_recall);
            # excluded from the mean rather than counted as 1.0.
            vals = [
                v for v in vals
                if v is not None and not math.isnan(v)
            ]
            cells.append(f"{statistics.mean(vals):.3f}" if vals else "n/a")
        print(f"| {dim} | " + " | ".join(cells) + " |")
    print()

    # ── Per-question_type x lane overall means ───────────────────
    print("## Per-question_type overall means\n")
    print("| question_type | " + " | ".join(_LANES) + " | delta (agent-rag) |")
    print("|" + "---|" * (len(_LANES) + 2))
    for qt in _QTYPES:
        cells = []
        means: Dict[str, float] = {}
        for lane in _LANES:
            recs = [
                r for r in by_lane.get(lane, [])
                if r["entry"].get("question_type") == qt
            ]
            vals = [r["grade"]["overall"] for r in recs]
            if vals:
                means[lane] = statistics.mean(vals)
                cells.append(f"{means[lane]:.3f} (n={len(vals)})")
            else:
                cells.append("n/a")
        delta = (
            means.get("manual_agent", 0.0) - means.get("rag", 0.0)
            if "manual_agent" in means and "rag" in means else 0.0
        )
        sign = "+" if delta >= 0 else ""
        print(f"| {qt} | " + " | ".join(cells) + f" | {sign}{delta:.3f} |")
    print()

    # ── Flat per-entry table for attribution ─────────────────────
    print("## Per-entry detail (for failure attribution)\n")
    print(
        "| short_id | qtype | lane | overall | sec_rec | fact_rec "
        "| halluc | cite | ans_q | stopped | iters |"
    )
    print("|" + "---|" * 11)
    rows = []
    for lane in _LANES:
        for r in by_lane.get(lane, []):
            e, g, res = r["entry"], r["grade"], r["result"]
            short = e.get("id", "?").split("-")[-2:]
            short_id = "-".join(short)
            rows.append((
                e.get("question_type", "?"), lane, g["overall"],
                short_id, g, res,
            ))
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    for qt, lane, overall, short_id, g, res in rows:
        sec_rec = g["section_recall"]
        sec_rec_cell = (
            f"{sec_rec:.2f}" if sec_rec is not None else "n/a"
        )
        print(
            f"| {short_id} | {qt} | {lane} | {overall:.3f} | "
            f"{sec_rec_cell} | {g['fact_recall']:.2f} | "
            f"{g['hallucination_penalty']:.2f} | "
            f"{g['citation_quality']:.2f} | {g['answer_quality']:.2f} | "
            f"{res.get('stopped_reason', '?')} | "
            f"{res.get('iterations', '?')} |"
        )
    print()

    # ── Threshold recommendation ─────────────────────────────────
    print("## Suggested _PASS_THRESHOLD (mean - 1*stdev, floored 1dp)\n")
    for lane in _LANES:
        print(f"  {lane}: {suggested.get(lane, 0.0):.1f}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python aggregate_phase6.py <report.json>")
        raise SystemExit(2)
    main(sys.argv[1])

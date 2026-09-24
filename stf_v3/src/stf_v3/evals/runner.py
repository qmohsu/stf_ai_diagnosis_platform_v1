"""Drive one golden eval run: goldens → sub-agent runs → grades → scorecard.

``run_eval`` is the testable core (model, judge and manual inventory are
injected); ``stf_v3.evals.cli`` wires the real ones.  Guarantees:

* the data directory matches its manifest before anything runs (FM-8);
* golden manual ids are checked against the V3 library; a missing id is
  mapped to the single library manual that matches the corpus vehicle,
  else the run refuses (PROD-08 FM-56 insurance);
* every golden gets a fresh ``DiagDeps`` (FM-50) and an eval-level hard
  limit of 2 × sub-agent wall clock + 60 s (the one-shot nudge can take a
  second wall clock, FM-48 / FM-32);
* each finished golden is appended to ``<base>.partial.json`` and one
  progress line to ``<base>.progress.jsonl`` (FM-14 / FM-29);
* a whole-run watchdog writes an invalid scorecard instead of hanging;
* nothing is written to the database or the data volumes (FM-15).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import structlog

from stf_v3.diagnosis.agent.deps import Budgets, ManualInfo
from stf_v3.evals import data as evdata
from stf_v3.evals import scorecard as sc
from stf_v3.evals.lanes import (
    CORPUS_MANUFACTURER,
    CORPUS_MODEL,
    LANE_MANUAL,
    LANE_OBD,
    RUNNERS,
    ItemStats,
    LaneContext,
)
from stf_v3.evals.orchestrator import (
    STATUS_OK,
    PipelineOutcome,
    ResultKey,
    WorkItem,
    execute,
)
from stf_v3.evals.schemas import GoldenEntry

logger = structlog.get_logger(__name__)

LANE_ALIASES = {"manual": LANE_MANUAL, "manual_agent": LANE_MANUAL, "obd": LANE_OBD, "obd_agent": LANE_OBD}


@dataclass
class RunOptions:
    """One eval run's knobs (``--…`` flags of ``python -m stf_v3.evals run``)."""

    purpose: str
    lanes: List[str] = field(default_factory=lambda: [LANE_MANUAL, LANE_OBD])
    thinking: bool = False
    cloud: bool = False
    budget_scale: float = 1.0
    concurrency: int = 6
    judge_concurrency: int = 4
    ids: Optional[List[str]] = None
    out_dir: Path = Path("/out")
    data_dir: Optional[str] = None
    judge_retry_delays: Tuple[float, ...] = (10.0, 30.0)
    max_total_s: Optional[float] = None


class RunRefused(RuntimeError):
    """Preflight refused the run (exit code 4)."""


def apply_overrides(settings: Any, opts: RunOptions) -> str:
    """Set thinking + scaled sub-agent budgets on ``settings``; returns the profile.

    Only the eval process sees these values: the production default
    stays ``llm_thinking=False`` and profile budgets (FM-34 / FM-35).
    """
    from stf_v3.diagnosis.agent.model import profile_defaults, select_profile

    profile = select_profile(settings, cloud=opts.cloud)
    d = profile_defaults(profile)
    wall = settings.subagent_wall_clock_s if settings.subagent_wall_clock_s is not None else d["subagent_wall_clock_s"]
    reqs = (settings.subagent_request_limit if settings.subagent_request_limit is not None
            else d["subagent_request_limit"])
    mtok = settings.subagent_max_tokens if settings.subagent_max_tokens is not None else d["subagent_max_tokens"]
    s = float(opts.budget_scale)
    settings.subagent_wall_clock_s = float(wall) * s
    settings.subagent_request_limit = int(round(reqs * s))
    settings.subagent_max_tokens = int(round(mtok * s))
    settings.llm_thinking = bool(opts.thinking)
    return profile


def item_timeout_s(budgets: Budgets) -> float:
    """Eval-level hard limit for one golden: two sub-agent wall clocks + 60 s."""
    return 2.0 * float(budgets.subagent_wall_clock_s) + 60.0


def map_manual_ids(entries: List[GoldenEntry], manuals: Sequence[ManualInfo]
                   ) -> Tuple[List[GoldenEntry], Dict[str, str]]:
    """Make golden citations point at manual ids present in the V3 library.

    Returns:
        ``(entries, mapping)`` — ``mapping`` is empty when every id exists.

    Raises:
        RunRefused: an id is missing and no single library manual matches
            the corpus vehicle.
    """
    from stf_v3.diagnosis.tools.manual_tools import manual_matches_vehicle

    have = {m.id for m in manuals}
    wanted = {c.manual_id for e in entries for c in e.golden_citations}
    missing = sorted(wanted - have)
    if not missing:
        return entries, {}
    matches = [m for m in manuals if manual_matches_vehicle(m, CORPUS_MANUFACTURER, CORPUS_MODEL)]
    if len(matches) != 1:
        raise RunRefused(f"golden manual ids {missing} are not in the V3 library and "
                         f"{len(matches)} library manuals match {CORPUS_MANUFACTURER} {CORPUS_MODEL}")
    mapping = {old: matches[0].id for old in missing}
    out = []
    for e in entries:
        cites = [c.model_copy(update={"manual_id": mapping.get(c.manual_id, c.manual_id)}) for c in e.golden_citations]
        out.append(e.model_copy(update={"golden_citations": cites}))
    return out, mapping


def select_entries(root: Path, lanes: Sequence[str], ids: Optional[Sequence[str]]) -> Dict[str, List[GoldenEntry]]:
    out: Dict[str, List[GoldenEntry]] = {}
    matched: set = set()
    for lane in lanes:
        entries = evdata.load_golden(root, lane)
        if ids:
            keep = []
            for e in entries:
                hits = [i for i in ids if e.id == i or e.id.endswith(i)]
                if hits:
                    keep.append(e)
                    matched.update(hits)
            entries = keep
        bad = [e.id for e in entries if e.vehicle is not None]
        if bad:
            raise RunRefused(f"goldens with per-entry vehicle overrides are not supported: {bad[:3]}")
        out[lane] = entries
    unknown = sorted(set(ids or []) - matched)
    if unknown:
        raise RunRefused(f"--ids not found in the selected lanes: {unknown}")
    return out


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def run_eval(
    opts: RunOptions,
    *,
    model: Any,
    manuals: Sequence[ManualInfo],
    manual_root: Path,
    budgets_factory: Callable[[], Budgets],
    config: Dict[str, Any],
    git_commit: str,
    judge_client: Any = None,
    grade_fn: Any = None,
    secrets: Sequence[str] = (),
    stamp: Optional[str] = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> Tuple[Path, Dict[str, Any]]:
    """Run the selected goldens and write the scorecard.

    Returns:
        ``(final scorecard path, scorecard dict)``.

    Raises:
        RunRefused: data or preflight problems (nothing was run).
    """
    try:
        root = evdata.data_dir(opts.data_dir)
        data_hashes = evdata.verify(root)
    except evdata.EvalDataError as exc:
        raise RunRefused(str(exc)) from exc
    if opts.purpose not in sc.PURPOSES:
        raise RunRefused(f"unknown purpose {opts.purpose!r} (one of {', '.join(sc.PURPOSES)})")
    selected = select_entries(root, opts.lanes, opts.ids)
    mapping: Dict[str, str] = {}
    if LANE_MANUAL in selected:
        selected[LANE_MANUAL], mapping = map_manual_ids(selected[LANE_MANUAL], manuals)
    total = sum(len(v) for v in selected.values())
    if total == 0:
        raise RunRefused("no goldens selected")

    ctx = LaneContext(model=model, manuals=list(manuals), manual_root=Path(manual_root),
                      fixture_dir=evdata.fixture_dir(root), budgets_factory=budgets_factory,
                      model_is_local=not opts.cloud)
    probe = budgets_factory()
    per_item_limit = item_timeout_s(probe)
    waves = math.ceil(total / max(1, opts.concurrency))
    max_total = opts.max_total_s or (waves * per_item_limit + 900.0)

    out_dir = Path(opts.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _stamp()
    base = sc.unique_base(out_dir, sc.base_name(stamp, git_commit, opts.purpose,
                                                "cloud" if opts.cloud else "local", opts.thinking))
    started = time.time()
    card: Dict[str, Any] = {
        "started_at": started, "ended_at": None, "count": 0,
        "meta": {
            "schema": sc.SCHEMA, "stamp": stamp, "base": base, "git_commit": git_commit,
            "mode": {"backend": "cloud" if opts.cloud else "local", "thinking": "on" if opts.thinking else "off",
                     "purpose": opts.purpose, "budget_scale": float(opts.budget_scale)},
            "lanes": list(selected), "ids": list(opts.ids) if opts.ids else None,
            "expected": total, "completed": 0, "complete": False, "valid": False, "invalid_reasons": [],
            "failed_items": [],
            "config": {**config, "data_sha256": data_hashes, "manual_id_map": mapping},
            "pipeline": {"run_concurrency": opts.concurrency, "judge_concurrency": opts.judge_concurrency,
                         "item_count": total, "item_timeout_s": per_item_limit, "max_total_s": max_total},
            "secrets_redacted": 0, "slim": False,
        },
        "records": [],
    }
    progress = out_dir / f"{base}.progress.jsonl"
    lock = asyncio.Lock()
    done = {"n": 0}

    async def on_done(key: ResultKey, outcome: PipelineOutcome) -> None:
        async with lock:
            done["n"] += 1
            stats: ItemStats = outcome.stats or ItemStats()
            if outcome.status == STATUS_OK and stats.stopped_reason == "error":
                # The sub-agent hit an exception (model endpoint / tool crash):
                # an infrastructure failure, not the model's answer — the
                # scorecard is invalid.  Budget / timeout stops score normally
                # (FM-19: that IS the system's behaviour).
                outcome.status = "run_error"
            item = {"status": outcome.status, "judge_attempts": outcome.judge_attempts,
                    "run_seconds": round(outcome.run_seconds, 2), "judge_seconds": round(outcome.judge_seconds, 2),
                    **stats.to_dict()}
            if outcome.grade is not None and outcome.run is not None:
                entry = next(e for e in selected[key[0]] if e.id == key[1])
                card["records"].append({"entry": entry.model_dump(), "result": outcome.run.model_dump(),
                                        "grade": outcome.grade.model_dump(), "item": item})
            else:
                card["meta"]["failed_items"].append({"lane": key[0], "id": key[1], "status": outcome.status,
                                                     "error": f"{type(outcome.error).__name__}: {outcome.error}"})
            line = {"n": done["n"], "total": total, "lane": key[0], "id": key[1], "status": outcome.status,
                    "overall": round(outcome.grade.overall, 4) if outcome.grade else None,
                    "run_s": round(outcome.run_seconds, 1), "judge_s": round(outcome.judge_seconds, 1),
                    "stopped_reason": stats.stopped_reason}
            with progress.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            sc.write_partial(out_dir, base, card, secrets)

    items: List[WorkItem] = []
    for lane, entries in selected.items():
        runner = RUNNERS[lane]
        for e in entries:
            items.append(WorkItem(system_label=lane, entry=e,
                                  run_fn=(lambda e=e, runner=runner: runner(e, ctx))))

    try:
        await asyncio.wait_for(
            execute(items, run_concurrency=opts.concurrency, judge_concurrency=opts.judge_concurrency,
                    judge_client=judge_client, grade_fn=grade_fn, item_timeout_s=per_item_limit,
                    judge_retry_delays=opts.judge_retry_delays, on_done=on_done, sleep=sleep),
            timeout=max_total)
    except asyncio.TimeoutError:
        card["meta"].setdefault("extra_invalid_reasons", []).append(
            f"watchdog: whole run exceeded {max_total:.0f}s; {done['n']}/{total} finished")
    card["ended_at"] = time.time()
    card["records"].sort(key=lambda r: (r["result"]["system_label"] != LANE_MANUAL, r["entry"]["id"]))
    full, _ = sc.write_final(out_dir, base, card, secrets)
    return full, sc.load(full)


def exit_code(card: Dict[str, Any]) -> int:
    """0 valid · 2 finished but invalid · 6 watchdog."""
    meta = card.get("meta", {})
    if any(r.startswith("watchdog") for r in meta.get("extra_invalid_reasons", [])):
        return 6
    return 0 if meta.get("valid") else 2


__all__ = ["LANE_ALIASES", "RunOptions", "RunRefused", "apply_overrides", "exit_code", "item_timeout_s",
           "map_manual_ids", "run_eval", "select_entries", "STATUS_OK"]

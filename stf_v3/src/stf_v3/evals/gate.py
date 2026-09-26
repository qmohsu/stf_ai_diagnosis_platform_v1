"""Gate rules for the golden eval (PROD-10 D1 / D2) — pure functions.

Imports only the standard library and PyYAML so the CI gate job can run
it without installing the V3 package.

Rules (decided on the PROD-10 board):

* **Eligible scorecard** (FM-5 / FM-25): local model, thinking off,
  purpose ``gate`` or ``baseline``, complete, valid, judged by the
  baseline's judge model.  Cloud / thinking-on / demo / calibration /
  invalid / incomplete scorecards never count.
* **Lane passes** (D1 + FM-3 + D6, floor revised by #253 on 2026-09-27):
  one of the (at most two) newest eligible runs of the lane has every
  baseline golden and a mean ≥ baseline mean − the lane's tolerance
  (manual 0.03; OBD 0.06 — 15 goldens, the same code scored
  0.897 / 0.872 / 0.844 on three runs) — "one re-run allowed"; and the
  **floor** holds across ALL comparable eligible runs of the PR: no golden
  whose baseline is ≥ 0.6 is under 0.4 in two or more runs, no single run
  has three goldens under it, and a dip seen in the only run is "pending
  confirmation" (not passed — run once more).  One random dip per run no
  longer fails a PR (PROD-11 hit it twice on different goldens).
* **Contention record** (#253): ``<base>.contention.json`` next to a
  scorecard (GPU memory / compute of other users, Ollama, vLLM
  preemptions during the run) only produces warnings.
* **Acceptance of a baseline** (FM-11): per lane, the mean of the two
  run means ≥ the acceptance line, and each run ≥ line − the lane's
  tolerance.  OBD's acceptance line is V3's own first baseline (D5: the
  0.938 in the dev plan was qwen3.5 on V2).
* **Stale baseline warning** (FM-28): config keys outside the managed
  paths (judge model, vLLM version, manual library hashes, model) differ
  between the scorecard and the baseline.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import fnmatch
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

LANES = ("manual_agent", "obd_agent")
DEFAULT_JUDGE_MODEL = "z-ai/glm-5.1"
UNMANAGED_CONFIG_KEYS = ("judge_model", "vllm_version", "manual_sha256", "model", "tokenizer")

DEFAULT_MANAGED_PATHS = [
    "stf_v3/src/stf_v3/diagnosis/*",
    "stf_v3/src/stf_v3/knowledge/manual_index.py",
    "stf_v3/src/stf_v3/knowledge/manual_fs.py",
    "stf_v3/src/stf_v3/ingest/loader.py",
    # the evaluator's score-affecting modules (not gate.py / summary.py /
    # scorecard.py: they decide or report, they do not change a score)
    "stf_v3/src/stf_v3/evals/schemas.py",
    "stf_v3/src/stf_v3/evals/metrics.py",
    "stf_v3/src/stf_v3/evals/metrics_obd.py",
    "stf_v3/src/stf_v3/evals/judge.py",
    "stf_v3/src/stf_v3/evals/judge_prompts.py",
    "stf_v3/src/stf_v3/evals/lanes.py",
    "stf_v3/src/stf_v3/evals/orchestrator.py",
    "stf_v3/src/stf_v3/evals/runner.py",
    "stf_v3/src/stf_v3/evals/data.py",
    "stf_v3/src/stf_v3/evals/cli.py",
    "stf_v3/evals/*",
    "stf_v3/src/stf_v3/settings.py",
    "stf_v3/pyproject.toml",
    "infra/docker-compose.vllm.yml",
]
"""What counts as "touching the agent's brain" (D2 + FM-6).  ``*`` spans
directories (fnmatch).  Over-inclusion is cheap: the ``eval-exempt`` label
(added by the user) skips the gate for a PR that only looks managed."""

DEFAULT_LANE_TOLERANCE = {"manual_agent": 0.03, "obd_agent": 0.06}
"""D6 (2026-09-24): per-lane tolerance for the gate and the acceptance rule."""


@dataclass
class LaneBaseline:
    """One lane's baseline: its mean and per-golden scores (mean of the runs)."""

    mean: float
    per_item: Dict[str, float] = field(default_factory=dict)
    acceptance_line: Optional[float] = None
    tolerance: Optional[float] = None      # None → Thresholds.tolerance


@dataclass
class Thresholds:
    """Parsed ``thresholds.yaml``."""

    tolerance: float = 0.03
    floor: float = 0.4
    floor_min_baseline: float = 0.6
    max_runs_considered: int = 2
    judge_model: str = DEFAULT_JUDGE_MODEL
    lanes: Dict[str, LaneBaseline] = field(default_factory=dict)
    managed_paths: List[str] = field(default_factory=list)
    thresholds_path: str = "stf_v3/evals/thresholds.yaml"
    baseline_scorecards: List[str] = field(default_factory=list)
    baseline_config: Dict[str, Any] = field(default_factory=dict)
    exempt_label: str = "eval-exempt"
    reset_label: str = "baseline-reset"


def parse_thresholds(doc: Dict[str, Any]) -> Thresholds:
    t = Thresholds(
        tolerance=float(doc.get("tolerance", 0.03)),
        floor=float(doc.get("floor", 0.4)),
        floor_min_baseline=float(doc.get("floor_min_baseline", 0.6)),
        max_runs_considered=int(doc.get("max_runs_considered", 2)),
        judge_model=str(doc.get("judge_model", DEFAULT_JUDGE_MODEL)),
        managed_paths=list(doc.get("managed_paths", [])),
        thresholds_path=str(doc.get("thresholds_path", "stf_v3/evals/thresholds.yaml")),
        baseline_scorecards=list(doc.get("baseline_scorecards", [])),
        baseline_config=dict(doc.get("baseline_config", {})),
        exempt_label=str(doc.get("exempt_label", "eval-exempt")),
        reset_label=str(doc.get("reset_label", "baseline-reset")),
    )
    for lane, rec in (doc.get("lanes") or {}).items():
        t.lanes[lane] = LaneBaseline(mean=float(rec["mean"]),
                                     per_item={k: float(v) for k, v in (rec.get("per_item") or {}).items()},
                                     acceptance_line=rec.get("acceptance_line"),
                                     tolerance=(float(rec["tolerance"]) if rec.get("tolerance") is not None
                                                else None))
    return t


def load_thresholds(path: Path) -> Thresholds:
    return parse_thresholds(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


# ── scorecards ────────────────────────────────────────────────────

def mode_of(card: Dict[str, Any]) -> Dict[str, Any]:
    return card.get("meta", {}).get("mode", {})


def eligibility(card: Dict[str, Any], judge_model: str = DEFAULT_JUDGE_MODEL) -> Tuple[bool, List[str]]:
    """Whether a scorecard may feed the gate / a baseline, and why not."""
    meta = card.get("meta", {})
    mode = mode_of(card)
    why: List[str] = []
    if mode.get("backend") != "local":
        why.append(f"backend={mode.get('backend')} (only local counts)")
    if mode.get("thinking") != "off":
        why.append(f"thinking={mode.get('thinking')} (only thinking off counts)")
    if mode.get("purpose") not in ("gate", "baseline"):
        why.append(f"purpose={mode.get('purpose')} (only gate / baseline count)")
    if float(mode.get("budget_scale", 1.0)) != 1.0:
        why.append(f"budget_scale={mode.get('budget_scale')} (only production budgets count)")
    if not meta.get("complete"):
        why.append("incomplete")
    if not meta.get("valid"):
        why.append("invalid: " + "; ".join(meta.get("invalid_reasons", [])[:3]))
    jm = meta.get("config", {}).get("judge_model")
    if jm != judge_model:
        why.append(f"judge_model={jm} (baseline uses {judge_model})")
    return (not why), why


def lane_scores(card: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """``{lane: {golden id: overall}}``."""
    out: Dict[str, Dict[str, float]] = {}
    for r in card.get("records", []):
        lane = r["result"]["system_label"]
        out.setdefault(lane, {})[r["entry"]["id"]] = float(r["grade"]["overall"])
    return out


def lane_means(card: Dict[str, Any]) -> Dict[str, float]:
    return {lane: statistics.mean(v.values()) for lane, v in lane_scores(card).items() if v}


def check_lane(scores: Dict[str, float], base: LaneBaseline, t: Thresholds) -> Tuple[bool, List[str]]:
    """One lane of one scorecard against its baseline, floor included
    (the per-run form, kept for callers; the gate uses ``_judge_lane``)."""
    why: List[str] = []
    missing = sorted(set(base.per_item) - set(scores))
    if missing:
        why.append(f"{len(missing)} baseline goldens missing: {', '.join(missing[:3])}")
    if not scores:
        return False, why + ["no scores"]
    mean = statistics.mean(scores.values())
    tol = base.tolerance if base.tolerance is not None else t.tolerance
    if mean < base.mean - tol - 1e-9:
        why.append(f"mean {mean:.3f} < baseline {base.mean:.3f} − {tol:.2f}")
    for gid, s in sorted(scores.items()):
        b = base.per_item.get(gid)
        if b is not None and b >= t.floor_min_baseline and s < t.floor:
            why.append(f"{gid} fell to {s:.3f} (< floor {t.floor}; baseline {b:.3f})")
    return (not why), why


FLOOR_MAX_DIPS_PER_RUN = 3
"""#253 FM-24: a single run with this many goldens under the floor fails the
floor on its own (the observed noise is 1-2 per run)."""

PENDING_MARK = "pending confirmation"
"""Substring of the verdict line when a lone run has dips (#253 D2)."""


def _name(card: Dict[str, Any]) -> str:
    return str(card.get("_path") or card.get("meta", {}).get("stamp", "?"))


def order_key(card: Dict[str, Any]) -> Tuple[str, str]:
    """#253 FM-29: newest last by the run stamp recorded in the scorecard, then name."""
    return (str(card.get("meta", {}).get("stamp", "")), _name(card))


def check_mean(scores: Dict[str, float], base: LaneBaseline, t: Thresholds) -> Tuple[bool, List[str]]:
    """One lane of one scorecard: every baseline golden present and the mean
    ≥ baseline − the lane's tolerance (the floor is judged across runs)."""
    why: List[str] = []
    missing = sorted(set(base.per_item) - set(scores))
    if missing:
        why.append(f"{len(missing)} baseline goldens missing: {', '.join(missing[:3])}")
    if not scores:
        return False, why + ["no scores"]
    mean = statistics.mean(scores.values())
    tol = base.tolerance if base.tolerance is not None else t.tolerance
    if mean < base.mean - tol - 1e-9:
        why.append(f"mean {mean:.3f} < baseline {base.mean:.3f} − {tol:.2f}")
    return (not why), why


def floor_dips(scores: Dict[str, float], base: LaneBaseline, t: Thresholds) -> Dict[str, float]:
    """Goldens whose baseline is ≥ ``floor_min_baseline`` and whose score is
    strictly below the floor; a missing score counts as 0 (#253 FM-26 / FM-27)."""
    out: Dict[str, float] = {}
    for gid, b in sorted(base.per_item.items()):
        if b < t.floor_min_baseline:
            continue
        s = scores.get(gid, 0.0)
        if s < t.floor:
            out[gid] = s
    return out


def _signature(card: Dict[str, Any], lane: str) -> Tuple[Any, ...]:
    """What must match for two runs of a lane to be compared (#253 FM-26)."""
    data = card.get("meta", {}).get("config", {}).get("data_sha256")
    return (tuple(sorted(lane_scores(card).get(lane, {}))), repr(data))


def _judge_lane(lane: str, base: LaneBaseline, cards: Sequence[Dict[str, Any]],
                t: Thresholds) -> Tuple[bool, List[str]]:
    """One lane across the PR's eligible runs (#253).

    * **Mean** (unchanged, D1): one of the newest ``max_runs_considered``
      runs that contain this lane must have every baseline golden and a
      mean ≥ baseline − tolerance.
    * **Floor**: counted over every comparable run of the lane (same golden
      ids and golden data as the newest, FM-26 / FM-31).  It fails when one
      golden is under the floor in two or more runs (FM-23), or one run has
      ``FLOOR_MAX_DIPS_PER_RUN`` goldens under it (FM-24).  A dip seen in
      only one comparable run is "pending confirmation" (D2): not passed,
      until another run shows it does not repeat.
    """
    lines: List[str] = []
    lane_cards = [c for c in cards if lane_scores(c).get(lane)]
    if not lane_cards:
        return False, [f"{lane}: FAIL: no eligible scorecard has this lane"]
    tol = base.tolerance if base.tolerance is not None else t.tolerance
    window = lane_cards[-t.max_runs_considered:]
    newest = lane_cards[-1]
    group = [c for c in lane_cards if _signature(c, lane) == _signature(newest, lane)]
    lines.append(f"{lane}: mean from {', '.join(_name(c) for c in window)}; "
                 f"floor over {len(group)} comparable run(s)")
    for c in lane_cards:
        if c not in group:
            lines.append(f"{lane}: {_name(c)} not compared for the floor (different goldens or golden data "
                         f"than {_name(newest)})")
    for c in lane_cards:
        if c not in window:
            lines.append(f"{lane}: {_name(c)} older than the newest {t.max_runs_considered} — not used for the mean")

    mean_ok = False
    for c in window:
        scores = lane_scores(c)[lane]
        ok, why = check_mean(scores, base, t)
        mean = statistics.mean(scores.values()) if scores else float("nan")
        lines.append(f"{lane} @ {_name(c)}: mean {mean:.3f} vs baseline {base.mean:.3f} → "
                     f"{'PASS' if ok else 'FAIL: ' + '; '.join(why)}")
        if scores and mean < base.mean - 2 * tol - 1e-9:
            lines.append(f"WARN {lane} @ {_name(c)}: mean {mean:.3f} is below baseline − 2×tolerance "
                         f"({base.mean - 2 * tol:.3f}); if the lane passes, it passes on another run's mean")
        mean_ok = mean_ok or ok

    dips = {_name(c): floor_dips(lane_scores(c)[lane], base, t) for c in group}
    runs_with = {}  # type: Dict[str, List[str]]
    for name, d in dips.items():
        for gid in d:
            runs_with.setdefault(gid, []).append(name)
    for gid in sorted(runs_with):
        b = base.per_item.get(gid, float("nan"))
        others = ", ".join(f"{_name(c)}={lane_scores(c)[lane].get(gid, 0.0):.3f}"
                           for c in group if _name(c) not in runs_with[gid])
        for name in runs_with[gid]:
            lines.append(f"{lane}: {gid} fell to {dips[name][gid]:.3f} (< floor {t.floor}; baseline {b:.3f}) "
                         f"in {name}; other runs: {others or 'none'}")
    confirmed = sorted(g for g, names in runs_with.items() if len(names) >= 2)
    collapsed = sorted(n for n, d in dips.items() if len(d) >= FLOOR_MAX_DIPS_PER_RUN)
    pending = sorted(runs_with) if runs_with and len(group) < 2 else []
    floor_ok = not (confirmed or collapsed or pending)
    for gid in confirmed:
        lines.append(f"{lane}: FAIL floor: {gid} is under {t.floor} in {len(runs_with[gid])} runs")
    for name in collapsed:
        lines.append(f"{lane}: FAIL floor: {name} has {len(dips[name])} goldens under {t.floor} "
                     f"(≥ {FLOOR_MAX_DIPS_PER_RUN} in one run)")
    if pending and not (confirmed or collapsed):
        lines.append(f"{lane}: NOT PASSED — {PENDING_MARK} (未通过：待确认): {', '.join(pending)} fell under "
                     f"the floor in the only comparable run; run the gate once more — it passes if they do "
                     f"not fall again")
    ok = mean_ok and floor_ok
    lines.append(f"{lane}: {'PASS' if ok else 'FAIL'}")
    return ok, lines


def decide(cards: Sequence[Dict[str, Any]], t: Thresholds) -> Tuple[bool, List[str]]:
    """Gate verdict over the PR's current scorecards.

    Only eligible scorecards count, ordered by their run stamp (FM-29);
    each lane is judged by ``_judge_lane`` (#253).
    """
    lines: List[str] = []
    eligible: List[Dict[str, Any]] = []
    for c in sorted(cards, key=order_key):
        ok, why = eligibility(c, t.judge_model)
        if ok:
            eligible.append(c)
        else:
            lines.append(f"ignored {_name(c)}: {'; '.join(why)}")
    if not eligible:
        return False, lines + ["no eligible scorecard (local, thinking off, gate/baseline, complete, valid)"]
    overall_ok = True
    for lane, base in t.lanes.items():
        ok, lane_lines = _judge_lane(lane, base, eligible, t)
        lines += lane_lines
        overall_ok = overall_ok and ok
    return overall_ok, lines


# ── GPU contention record (#253) ──────────────────────────────────

CONTENTION_SCHEMA = 1
CONTENTION_SUFFIX = ".contention.json"
CONTENTION_TOP_KEYS = frozenset({
    "schema", "kind", "run", "scorecard", "started_at", "ended_at", "interval_s", "samples",
    "sample_errors", "concurrency", "preemptions_before", "preemptions_after", "preemption_delta",
    "vllm_restarted", "gpus",
})
CONTENTION_GPU_KEYS = frozenset({
    "index", "total_mib", "others_mib_max", "others_mib_mean", "others_sm_pct_max", "others_sm_pct_mean",
    "vllm_mib_max", "project_other_mib_max", "ollama_mib_max", "util_pct_max", "util_pct_mean",
})
CONTENTION_OTHERS_MIB_WARN = 2048
CONTENTION_OTHERS_SM_WARN = 20.0
_STRING_KEYS = {"kind", "run", "scorecard", "started_at", "ended_at"}


def _allowed_string(key: str, value: str) -> bool:
    import re

    pattern = {
        "kind": r"gpu_contention",
        "run": r"stf-v3-eval-\d{8}T\d{6}Z",
        "scorecard": r"\d{8}T\d{6}Z(?:_[0-9A-Za-z-]{1,40}){1,6}",
        "started_at": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        "ended_at": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
    }[key]
    if any(bad in value.lower() for bad in ("key", "token", "secret", "sk-", "bearer")):
        return False                       # FM-19: nothing secret-looking, ever
    return re.fullmatch(pattern, value) is not None


def validate_contention(rec: Any) -> List[str]:
    """Why a contention record cannot be used (empty = usable).

    Whitelist only (#253 FM-18 / FM-19): numbers per GPU, the run and
    scorecard names, UTC times; anything else — names, command lines,
    environment — makes the record unusable.
    """
    if not isinstance(rec, dict):
        return ["not a JSON object"]
    if rec.get("schema") != CONTENTION_SCHEMA:
        return [f"schema {rec.get('schema')!r} unknown (this gate reads {CONTENTION_SCHEMA})"]
    extra = sorted(set(rec) - CONTENTION_TOP_KEYS)
    if extra:
        return [f"fields outside the whitelist: {', '.join(extra[:5])}"]
    for key in _STRING_KEYS:
        v = rec.get(key)
        if v is not None and not (isinstance(v, str) and _allowed_string(key, v)):
            return [f"field {key} has an unexpected value"]
    for key in CONTENTION_TOP_KEYS - _STRING_KEYS - {"gpus", "schema", "vllm_restarted"}:
        v = rec.get(key)
        if v is not None and not isinstance(v, (int, float)):
            return [f"field {key} is not a number"]
    gpus = rec.get("gpus")
    if not isinstance(gpus, list):
        return ["gpus is not a list"]
    for g in gpus:
        if not isinstance(g, dict) or set(g) - CONTENTION_GPU_KEYS or \
                any(v is not None and not isinstance(v, (int, float)) for v in g.values()):
            return ["a GPU entry has fields outside the whitelist or non-numbers"]
    return []


def contention_warnings(rec: Any, scorecard_base: str) -> List[str]:
    """Warning lines for one run's contention record — never a verdict (#253 D3)."""
    bad = validate_contention(rec)
    if bad:
        return [f"contention record unusable: {bad[0]}"]
    out: List[str] = []
    if rec.get("scorecard") and rec["scorecard"] != scorecard_base:
        out.append(f"contention record belongs to {rec['scorecard']}, not {scorecard_base}")
    if not rec.get("samples"):
        out.append("contention record has no samples")
    for g in rec.get("gpus", []):
        i = g.get("index")
        if (g.get("others_mib_max") or 0) > CONTENTION_OTHERS_MIB_WARN:
            out.append(f"GPU {i}: other workloads held up to {g['others_mib_max']:.0f} MiB during the run "
                       f"(other users, or this account outside the V3 services)")
        if (g.get("others_sm_pct_max") or 0) > CONTENTION_OTHERS_SM_WARN:
            out.append(f"GPU {i}: other workloads' compute peaked at {g['others_sm_pct_max']:.0f} % during the run "
                       f"(scores may be slower / more time-outs)")
        if (g.get("ollama_mib_max") or 0) > 0:
            out.append(f"GPU {i}: Ollama held {g['ollama_mib_max']:.0f} MiB during the run (must not share with vLLM)")
    if rec.get("vllm_restarted"):
        out.append("vLLM restarted during the run (preemption counter went backwards)")
    elif (rec.get("preemption_delta") or 0) > 0:
        out.append(f"vLLM preempted {rec['preemption_delta']} request(s) during the run")
    return out


def acceptance(cards: Sequence[Dict[str, Any]], lines: Dict[str, float],
               tolerance: Optional[Any] = None) -> Tuple[bool, List[str]]:
    """FM-11: mean of the run means ≥ line and each run ≥ line − tolerance.

    ``tolerance``: a number for every lane, a ``{lane: tol}`` map, or
    ``None`` → ``DEFAULT_LANE_TOLERANCE`` (D6).
    """
    out: List[str] = []
    ok_all = True
    for lane, line in lines.items():
        means = [lane_means(c).get(lane) for c in cards]
        if any(m is None for m in means):
            out.append(f"{lane}: missing in a run")
            ok_all = False
            continue
        avg = statistics.mean(means)  # type: ignore[arg-type]
        if isinstance(tolerance, dict):
            tol = float(tolerance.get(lane, DEFAULT_LANE_TOLERANCE.get(lane, 0.03)))
        elif tolerance is None:
            tol = DEFAULT_LANE_TOLERANCE.get(lane, 0.03)
        else:
            tol = float(tolerance)
        ok = avg >= line - 1e-9 and all(m >= line - tol - 1e-9 for m in means)  # type: ignore[operator]
        out.append(f"{lane}: runs {', '.join(f'{m:.3f}' for m in means)} → mean {avg:.3f} vs line {line:.3f} "
                   f"({'PASS' if ok else 'FAIL'})")
        ok_all = ok_all and ok
    return ok_all, out


def build_baseline(cards: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per lane: mean of the run means and per-golden mean scores."""
    lanes: Dict[str, Dict[str, Any]] = {}
    for lane in LANES:
        per_run = [lane_scores(c).get(lane, {}) for c in cards]
        if not all(per_run):
            continue
        ids = sorted(set().union(*per_run))
        per_item = {i: round(statistics.mean(r[i] for r in per_run if i in r), 4) for i in ids}
        lanes[lane] = {"mean": round(statistics.mean(statistics.mean(r.values()) for r in per_run), 4),
                       "tolerance": DEFAULT_LANE_TOLERANCE.get(lane, 0.03),
                       "per_item": per_item}
    return lanes


def stale_warnings(card: Dict[str, Any], t: Thresholds) -> List[str]:
    """FM-28: unmanaged config drift between a scorecard and the baseline."""
    cfg = card.get("meta", {}).get("config", {})
    out = []
    for key in UNMANAGED_CONFIG_KEYS:
        if key in t.baseline_config and cfg.get(key) != t.baseline_config.get(key):
            out.append(f"config '{key}' differs from the baseline — the baseline may be stale "
                       f"(consider a baseline-reset PR)")
    return out


# ── paths ─────────────────────────────────────────────────────────

def is_managed(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def managed(paths: Iterable[str], patterns: Iterable[str]) -> List[str]:
    pats = list(patterns)
    return [p for p in paths if is_managed(p, pats)]


# ── calibration (FM-36 / FM-40) ───────────────────────────────────

def p95(values: Sequence[float]) -> float:
    """Nearest-rank 95th percentile."""
    xs = sorted(values)
    if not xs:
        return 0.0
    k = max(0, math.ceil(0.95 * len(xs)) - 1)
    return xs[k]


def calibrate(card: Dict[str, Any], margin: float = 1.5, max_censored: float = 0.10) -> Dict[str, Any]:
    """Propose sub-agent budget defaults from a (budget-scaled) run.

    Censored goldens (stopped by a budget / timeout, or an eval-level
    failure) have their numbers capped by the budget itself; if more than
    ``max_censored`` of them are censored, no proposal is made.
    """
    stats = [r.get("item", {}) for r in card.get("records", [])]
    n = len(stats) + len(card.get("meta", {}).get("failed_items", []))
    censored = [s for s in stats if s.get("stopped_reason") not in ("complete", None)]
    censored_n = len(censored) + len(card.get("meta", {}).get("failed_items", []))
    walls = [float(s.get("wall_s", 0.0)) for s in stats]
    reqs = [float(s.get("requests", 0)) for s in stats]
    toks = [float(s.get("total_tokens", 0)) for s in stats]
    out: Dict[str, Any] = {
        "goldens": n, "censored": censored_n,
        "censored_share": round(censored_n / n, 3) if n else 0.0,
        "p95": {"wall_s": p95(walls), "requests": p95(reqs), "total_tokens": p95(toks)},
        "max": {"wall_s": max(walls, default=0.0), "requests": max(reqs, default=0.0),
                "total_tokens": max(toks, default=0.0)},
        "margin": margin, "proposal": None, "refused": None,
    }
    if n and censored_n / n > max_censored:
        out["refused"] = (f"{censored_n}/{n} goldens censored by the budget (> {max_censored:.0%}) — "
                          "raise the budget scale and re-run before calibrating")
        return out
    out["proposal"] = {
        "subagent_wall_clock_s": float(math.ceil(out["p95"]["wall_s"] * margin / 10.0) * 10),
        "subagent_request_limit": int(math.ceil(out["p95"]["requests"] * margin)),
    }
    return out


__all__ = ["CONTENTION_SCHEMA", "CONTENTION_SUFFIX", "DEFAULT_JUDGE_MODEL", "DEFAULT_LANE_TOLERANCE",
           "DEFAULT_MANAGED_PATHS", "FLOOR_MAX_DIPS_PER_RUN", "LANES", "LaneBaseline", "PENDING_MARK",
           "Thresholds", "acceptance", "build_baseline", "calibrate", "check_lane", "check_mean",
           "contention_warnings", "decide", "eligibility", "floor_dips", "is_managed", "lane_means",
           "lane_scores", "load_thresholds", "managed", "order_key", "p95", "parse_thresholds",
           "stale_warnings", "validate_contention"]

"""Gate rules for the golden eval (PROD-10 D1 / D2) — pure functions.

Imports only the standard library and PyYAML so the CI gate job can run
it without installing the V3 package.

Rules (decided on the PROD-10 board):

* **Eligible scorecard** (FM-5 / FM-25): local model, thinking off,
  purpose ``gate`` or ``baseline``, complete, valid, judged by the
  baseline's judge model.  Cloud / thinking-on / demo / calibration /
  invalid / incomplete scorecards never count.
* **Lane passes** (D1 + FM-3 + D6): mean ≥ baseline mean − the lane's
  tolerance (manual 0.03; OBD 0.06 — 15 goldens, the same code scored
  0.897 / 0.872 / 0.844 on three runs),
  and no golden whose baseline score is ≥ 0.6 falls below the floor
  (0.4); every baseline golden must be present.  A lane passes when
  either of the (at most two) newest eligible scorecards passes it —
  "one re-run allowed".
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
    """One lane of one scorecard against its baseline."""
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


def decide(cards: Sequence[Dict[str, Any]], t: Thresholds) -> Tuple[bool, List[str]]:
    """Gate verdict over the PR's current scorecards (newest last).

    Only eligible scorecards count; per lane, pass if one of the newest
    ``max_runs_considered`` eligible scorecards passes.
    """
    lines: List[str] = []
    eligible: List[Dict[str, Any]] = []
    for c in cards:
        ok, why = eligibility(c, t.judge_model)
        name = c.get("_path", c.get("meta", {}).get("stamp", "?"))
        if ok:
            eligible.append(c)
        else:
            lines.append(f"ignored {name}: {'; '.join(why)}")
    if not eligible:
        return False, lines + ["no eligible scorecard (local, thinking off, gate/baseline, complete, valid)"]
    considered = eligible[-t.max_runs_considered:]
    overall_ok = True
    for lane, base in t.lanes.items():
        lane_ok = False
        for c in considered:
            scores = lane_scores(c).get(lane, {})
            ok, why = check_lane(scores, base, t)
            name = c.get("_path", c.get("meta", {}).get("stamp", "?"))
            mean = statistics.mean(scores.values()) if scores else float("nan")
            lines.append(f"{lane} @ {name}: mean {mean:.3f} vs baseline {base.mean:.3f} → "
                         f"{'PASS' if ok else 'FAIL: ' + '; '.join(why)}")
            lane_ok = lane_ok or ok
        overall_ok = overall_ok and lane_ok
    return overall_ok, lines


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


__all__ = ["DEFAULT_JUDGE_MODEL", "DEFAULT_LANE_TOLERANCE", "DEFAULT_MANAGED_PATHS", "LANES", "LaneBaseline", "Thresholds", "acceptance", "build_baseline",
           "calibrate", "check_lane", "decide", "eligibility", "is_managed", "lane_means", "lane_scores",
           "load_thresholds", "managed", "p95", "parse_thresholds", "stale_warnings"]

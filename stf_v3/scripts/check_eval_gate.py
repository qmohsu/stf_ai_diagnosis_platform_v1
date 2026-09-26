#!/usr/bin/env python3
"""CI gate: a PR that touches the agent's brain must carry a fresh, passing
golden scorecard (PROD-10 D1 / D2).

    python stf_v3/scripts/check_eval_gate.py --base origin/main --head <sha> \\
        [--labels "eval-exempt,…"] [--repo .]

Decision (printed line by line, exit 0 = green, 1 = red):

1. Changed files = ``git diff merge-base(base, head)..head``.  None under the
   managed paths → SKIP (green).  Label ``eval-exempt`` → SKIP with a notice.
2. **Bootstrap** (no ``thresholds.yaml`` on the base): the PR must add the
   thresholds file and the baseline scorecards it names; they must be
   eligible, fresh and pass the acceptance rule.
3. **Baseline reset** (thresholds changed on a PR that already had them):
   no other managed path may change (FM-13), the ``baseline-reset`` label
   is required, and the new baseline must be eligible, fresh, accepted.
4. **Normal**: scorecards ADDED by this PR under ``docs/evals/`` that are
   eligible (local, thinking off, gate/baseline, complete, valid, same
   judge) and **fresh** — their ``git_commit`` is an ancestor of head and
   no managed path changed after it (FM-4 / FM-45) — are checked against
   the baseline: one of the newest two runs of a lane passes the mean
   (≥ baseline − the lane's tolerance: manual 0.03, OBD 0.06, every
   baseline golden present); the floor holds across all comparable runs
   (#253: a golden with baseline ≥ 0.6 under 0.4 in two runs, or three
   under it in one run, fails; a dip in the only run is "pending
   confirmation" — red until another run).  Unmanaged config drift
   against the baseline prints a warning (FM-28); a contention record
   ``<base>.contention.json`` beside a scorecard only prints warnings
   (other users on the GPUs, Ollama, vLLM preemptions — #253).

Warnings and the verdict also go to the GitHub step summary / annotations
when run in Actions (#253 FM-13).

Only needs Python + PyYAML + git (imports ``stf_v3.evals.gate`` by path).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from stf_v3.evals import gate  # noqa: E402

SCORECARD_DIR = "docs/evals"
CONTENTION_SINCE = "20260928T000000Z"
"""Scorecards stamped from here on are expected to carry a contention record
(#253 FM-7); older ones are not warned about."""
THRESHOLDS = "stf_v3/evals/thresholds.yaml"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def _exists_at(repo: Path, rev: str, path: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{rev}:{path}"],
                          capture_output=True).returncode == 0


def _show(repo: Path, rev: str, path: str) -> str:
    return _git(repo, "show", f"{rev}:{path}")


def _is_ancestor(repo: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def _changed(repo: Path, a: str, b: str, filt: Optional[str] = None) -> List[str]:
    args = ["diff", "--name-only"] + ([f"--diff-filter={filt}"] if filt else []) + [a, b]
    return [p for p in _git(repo, *args).splitlines() if p.strip()]


def _is_scorecard(path: str) -> bool:
    return (path.startswith(SCORECARD_DIR + "/") and path.endswith(".json")
            and not path.endswith(".partial.json") and not path.endswith(gate.CONTENTION_SUFFIX))


def _pick_per_run(paths: Sequence[str]) -> List[str]:
    """One file per run (``meta.base``): regraded > full > slim."""
    rank = {".regraded.json": 0, ".slim.json": 2}

    def key(p: str) -> int:
        for suffix, r in rank.items():
            if p.endswith(suffix):
                return r
        return 1

    best: Dict[str, str] = {}
    for p in paths:
        stem = Path(p).name
        for suffix in (".regraded.json", ".slim.json", ".json"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if stem not in best or key(p) < key(best[stem]):
            best[stem] = p
    return sorted(best.values())


def _load_cards(repo: Path, head: str, paths: Sequence[str]) -> List[Dict[str, Any]]:
    cards = []
    for p in _pick_per_run(paths):
        c = json.loads(_show(repo, head, p))
        c["_path"] = Path(p).name
        cards.append(c)
    cards.sort(key=gate.order_key)
    return cards


def _contention_lines(repo: Path, head: str, card: Dict[str, Any]) -> List[str]:
    """WARN lines from the run's contention record, if any (#253; never a verdict)."""
    meta = card.get("meta", {})
    base = str(meta.get("base", ""))
    path = f"{SCORECARD_DIR}/{base}{gate.CONTENTION_SUFFIX}"
    if not base or not _exists_at(repo, head, path):
        if str(meta.get("stamp", "")) >= CONTENTION_SINCE:
            return [f"WARN {card['_path']}: no contention record ({Path(path).name}) — copy it with the "
                    f"scorecard (runbook §5.2)"]
        return []
    try:
        rec = json.loads(_show(repo, head, path))
    except (RuntimeError, ValueError):
        return [f"WARN {card['_path']}: contention record unreadable ({Path(path).name})"]
    return [f"WARN {card['_path']}: {w}" for w in gate.contention_warnings(rec, base)]


def _fresh(repo: Path, head: str, card: Dict[str, Any], patterns: Sequence[str],
           ignore: Sequence[str] = ()) -> Tuple[bool, str]:
    commit = str(card.get("meta", {}).get("git_commit", ""))
    if not commit or commit == "unknown":
        return False, "scorecard has no commit"
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", commit + "^{commit}"],
                      capture_output=True).returncode != 0 or not _is_ancestor(repo, commit, head):
        return False, f"commit {commit[:8]} is not in this branch's history (rebased?) — re-run the eval"
    later = [p for p in gate.managed(_changed(repo, commit, head), patterns) if p not in ignore]
    if later:
        return False, (f"managed paths changed after the eval commit {commit[:8]}: "
                       f"{', '.join(later[:4])} — re-run the eval on the latest commit")
    return True, "fresh"


def decide(repo: Path, base: str, head: str, labels: Sequence[str]) -> Tuple[int, List[str]]:
    """Returns ``(exit code, report lines)``."""
    out: List[str] = []
    mb = _git(repo, "merge-base", base, head).strip()
    changed = _changed(repo, mb, head)
    at_head = _exists_at(repo, head, THRESHOLDS)
    t = gate.parse_thresholds(__import__("yaml").safe_load(_show(repo, head, THRESHOLDS))) if at_head else None
    patterns = (t.managed_paths if t and t.managed_paths else gate.DEFAULT_MANAGED_PATHS)
    hit = gate.managed(changed, patterns)
    if not hit:
        return 0, ["SKIP: no managed path changed (golden eval not required)"]
    out.append(f"managed paths changed ({len(hit)}): {', '.join(hit[:6])}{' …' if len(hit) > 6 else ''}")
    exempt = t.exempt_label if t else "eval-exempt"
    if exempt in labels:
        return 0, out + [f"SKIP: label '{exempt}' — golden eval waived for this PR (label set by the user)"]
    added = [p for p in _changed(repo, mb, head, "A") if _is_scorecard(p)]
    at_base = _exists_at(repo, mb, THRESHOLDS)

    if not at_base or THRESHOLDS in changed:
        mode = "bootstrap" if not at_base else "baseline reset"
        if t is None:
            return 1, out + [f"FAIL ({mode}): {THRESHOLDS} missing at head"]
        if at_base:
            others = [p for p in hit if p != THRESHOLDS]
            if others:
                return 1, out + [f"FAIL (FM-13): the baseline / thresholds changed together with managed code "
                                 f"({', '.join(others[:4])}); split into a code PR and a baseline-reset PR"]
            if t.reset_label not in labels:
                return 1, out + [f"FAIL (baseline reset): needs the '{t.reset_label}' label (set by the user)"]
        names = set(t.baseline_scorecards)
        cards = [c for c in _load_cards(repo, head, added) if c["_path"] in names]
        if len(cards) != len(names):
            return 1, out + [f"FAIL ({mode}): baseline scorecards named in thresholds not added by this PR: "
                             f"{sorted(names - {c['_path'] for c in cards})}"]
        ok_all = True
        for c in cards:
            ok, why = gate.eligibility(c, t.judge_model)
            if not ok or c["meta"]["mode"].get("purpose") != "baseline":
                out.append(f"FAIL ({mode}): {c['_path']} cannot be a baseline: {'; '.join(why) or 'purpose'}")
                ok_all = False
            fresh, msg = _fresh(repo, head, c, patterns, ignore=[THRESHOLDS])
            if not fresh:
                out.append(f"FAIL ({mode}): {c['_path']}: {msg}")
                ok_all = False
        lines = {lane: b.acceptance_line for lane, b in t.lanes.items() if b.acceptance_line is not None}
        tols = {lane: (b.tolerance if b.tolerance is not None else t.tolerance) for lane, b in t.lanes.items()}
        acc_ok, acc = gate.acceptance(cards, lines, tols)
        out += [f"{mode}: {line}" for line in acc]
        if not (ok_all and acc_ok):
            return 1, out + [f"FAIL ({mode})"]
        note = " — baseline reset approved by label" if at_base else ""
        return 0, out + [f"PASS ({mode}){note}"]

    assert t is not None
    cards = _load_cards(repo, head, added)
    fresh_cards = []
    for c in cards:
        ok, msg = _fresh(repo, head, c, patterns)
        if ok:
            fresh_cards.append(c)
        else:
            out.append(f"ignored {c['_path']}: {msg}")
    if not fresh_cards:
        return 1, out + [f"FAIL: no fresh golden scorecard added under {SCORECARD_DIR}/ — run "
                         "stf_v3/scripts/run_golden_eval.sh --purpose gate on the latest commit"]
    ok, lines = gate.decide(fresh_cards, t)
    out += lines
    for c in fresh_cards:
        out += [f"WARN {c['_path']}: {w}" for w in gate.stale_warnings(c, t)]
        out += _contention_lines(repo, head, c)
    if ok:
        return 0, out + ["PASS"]
    if any(gate.PENDING_MARK in line for line in lines) and not any("FAIL" in line and "floor" in line
                                                                     for line in lines):
        return 1, out + ["FAIL: NOT PASSED — pending confirmation (未通过：待确认): run the gate once more"]
    return 1, out + ["FAIL: golden eval below the gate"]


def _publish(code: int, lines: Sequence[str]) -> None:
    """GitHub Actions: warnings as annotations, verdict in the step summary (#253 FM-13)."""
    import os

    if os.environ.get("GITHUB_ACTIONS") == "true":
        for line in lines:
            if line.startswith("WARN"):
                print(f"::warning title=golden eval gate::{line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    verdict = lines[-1] if lines else ("PASS" if code == 0 else "FAIL")
    body = ["## Golden eval gate", "", f"**{verdict}**", "", "```", *lines, "```", ""]
    with open(summary, "a", encoding="utf-8") as fh:
        fh.write("\n".join(body))


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True)
    p.add_argument("--head", default="HEAD")
    p.add_argument("--labels", default="")
    p.add_argument("--repo", default=".")
    a = p.parse_args(argv)
    labels = [x.strip() for x in a.labels.split(",") if x.strip()]
    try:
        code, lines = decide(Path(a.repo), a.base, a.head, labels)
    except RuntimeError as exc:
        code, lines = 1, [f"FAIL: {exc}"]
    for line in lines:
        print(line)
    _publish(code, lines)
    return code


if __name__ == "__main__":
    sys.exit(main())

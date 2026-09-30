"""PROD-10 T-4: the CI gate script on a throwaway git history
(FM-4 / FM-45 / FM-6 / FM-13 / FM-28 / FM-7).

Each scenario builds a tiny repository with a ``main`` branch and a PR
branch, then calls the script's ``decide``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import pytest
import yaml

from stf_v3.evals import gate
from tests.evals_helpers import card, uniform

# These tests build throwaway git repositories; the V3 image (nightly unbound
# check, D3 2) has no git, so they run in CI's checkout jobs only.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed (image)")

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_eval_gate.py"
_spec = importlib.util.spec_from_file_location("check_eval_gate", _SCRIPT)
assert _spec and _spec.loader
cg: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)

M = "manual_agent"
PROMPT = "stf_v3/src/stf_v3/diagnosis/agent/prompts.py"
API = "stf_v3/src/stf_v3/vehicles/router.py"
THR = "stf_v3/evals/thresholds.yaml"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


class Repo:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True, env=ENV).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, msg: str, files: Dict[str, str]) -> str:
        for rel, text in files.items():
            self.write(rel, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def decide(self, labels: Optional[List[str]] = None):
        return cg.decide(self.root, "main", "HEAD", labels or [])


def _thresholds(mean: float = 0.85, n: int = 4, baselines: Optional[List[str]] = None,
                line: Optional[float] = None) -> str:
    lane = {"mean": mean, "per_item": {f"g-{i:03d}": mean for i in range(n)}}
    if line is not None:
        lane["acceptance_line"] = line
    return yaml.safe_dump({"tolerance": 0.03, "floor": 0.4, "floor_min_baseline": 0.6,
                           "judge_model": gate.DEFAULT_JUDGE_MODEL, "managed_paths": gate.DEFAULT_MANAGED_PATHS,
                           "baseline_scorecards": baselines or [],
                           "baseline_config": {"vllm_version": "0.24.0"}, "lanes": {M: lane}})


def _card_json(commit: str, value: float, purpose: str = "gate", stamp: str = "20260923T000001Z",
               **kw: Any) -> str:
    return json.dumps(card(uniform(M, 4, value), commit=commit, purpose=purpose, stamp=stamp, **kw))


@pytest.fixture()
def repo(tmp_path: pathlib.Path) -> Repo:
    """main = code + thresholds (normal mode); the test then branches."""
    r = Repo(tmp_path / "r")
    r.commit("base", {PROMPT: "v1\n", API: "v1\n", THR: _thresholds()})
    r.git("checkout", "-q", "-b", "pr")
    return r


def test_a_managed_change_without_a_scorecard_is_red(repo: Repo) -> None:
    """a: prompt changed, no scorecard → red, telling how to run the eval."""
    repo.commit("prompt", {PROMPT: "v2\n"})
    code, lines = repo.decide()
    assert code == 1 and "run_golden_eval.sh" in lines[-1]


def test_b_a_change_after_the_eval_commit_makes_the_scorecard_stale(repo: Repo) -> None:
    """b / FM-4: eval at A, prompt changed again at B → the scorecard is ignored."""
    a = repo.commit("prompt", {PROMPT: "v2\n"})
    repo.commit("card", {"docs/evals/run1.json": _card_json(a, 0.86)})
    repo.commit("prompt again", {PROMPT: "v3\n"})
    code, lines = repo.decide()
    assert code == 1 and any("changed after the eval commit" in line for line in lines)


def test_c_a_fresh_passing_scorecard_is_green(repo: Repo) -> None:
    """c: eval on the latest managed commit, above baseline − 0.03 → green."""
    a = repo.commit("prompt", {PROMPT: "v2\n"})
    repo.commit("card + docs", {"docs/evals/run1.json": _card_json(a, 0.83), "docs/notes.md": "x\n"})
    code, lines = repo.decide()
    assert code == 0 and lines[-1] == "PASS"


def test_c2_a_fresh_scorecard_below_the_gate_is_red(repo: Repo) -> None:
    """The demo case: a broken prompt scores below baseline − 0.03 → red."""
    a = repo.commit("broken prompt", {PROMPT: "broken\n"})
    repo.commit("card", {"docs/evals/run1.json": _card_json(a, 0.60)})
    code, lines = repo.decide()
    assert code == 1 and any("mean 0.600 < baseline 0.850" in line for line in lines)


def test_d_an_unmanaged_change_skips_the_eval(repo: Repo) -> None:
    """d: API-only change → SKIP (green)."""
    repo.commit("api", {API: "v2\n"})
    code, lines = repo.decide()
    assert code == 0 and lines[0].startswith("SKIP")


def test_e_code_and_baseline_in_one_pr_is_red(repo: Repo) -> None:
    """e / FM-13: prompt + thresholds changed together → red."""
    a = repo.commit("prompt + baseline", {PROMPT: "v2\n", THR: _thresholds(0.70, baselines=["b1.json"], line=0.6)})
    repo.commit("card", {"docs/evals/b1.json": _card_json(a, 0.70, purpose="baseline")})
    code, lines = repo.decide(["baseline-reset"])
    assert code == 1 and any("FM-13" in line for line in lines)


def test_f_a_threshold_only_pr_needs_the_reset_label(repo: Repo) -> None:
    """f / FM-28: baseline reset = thresholds + new baseline scorecards only;
    it needs the ``baseline-reset`` label and a fresh, accepted baseline."""
    head = repo.git("rev-parse", "HEAD")
    repo.commit("reset", {THR: _thresholds(0.80, baselines=["b1.json", "b2.json"], line=0.78),
                          "docs/evals/b1.json": _card_json(head, 0.80, "baseline", "20260923T000001Z"),
                          "docs/evals/b2.json": _card_json(head, 0.81, "baseline", "20260923T000002Z")})
    code, lines = repo.decide()
    assert code == 1 and "baseline-reset" in lines[-1]
    code, lines = repo.decide(["baseline-reset"])
    assert code == 0 and "approved by label" in lines[-1]


def test_g_the_exempt_label_skips_with_a_notice(repo: Repo) -> None:
    """g: ``eval-exempt`` → SKIP and say so."""
    repo.commit("prompt", {PROMPT: "v2\n"})
    code, lines = repo.decide(["eval-exempt"])
    assert code == 0 and "waived" in lines[-1]


def test_h_a_scorecard_from_a_rewritten_history_is_red(repo: Repo) -> None:
    """h: the scorecard's commit is not in the branch history (rebased) → re-run."""
    repo.commit("prompt", {PROMPT: "v2\n"})
    repo.commit("card", {"docs/evals/run1.json": _card_json("f" * 40, 0.9)})
    code, lines = repo.decide()
    assert code == 1 and any("not in this branch's history" in line for line in lines)


def test_i_unmanaged_drift_passes_with_a_warning(repo: Repo) -> None:
    """i / FM-28 / FM-7: vLLM version differs from the baseline's → WARN, still green."""
    a = repo.commit("prompt", {PROMPT: "v2\n"})
    repo.commit("card", {"docs/evals/run1.json": _card_json(a, 0.86, config={"vllm_version": "0.25.0"})})
    code, lines = repo.decide()
    assert code == 0 and any(line.startswith("WARN") and "vllm_version" in line for line in lines)


def test_j_bootstrap_adds_thresholds_and_accepted_baselines(tmp_path: pathlib.Path) -> None:
    """j: main has no thresholds (this PR itself) → the PR must add them with
    two fresh baseline scorecards that pass acceptance."""
    r = Repo(tmp_path / "boot")
    r.commit("base", {PROMPT: "v1\n", API: "v1\n"})
    r.git("checkout", "-q", "-b", "pr")
    code_commit = r.commit("evaluator", {PROMPT: "v2\n", "stf_v3/src/stf_v3/evals/gate.py": "x\n"})
    r.commit("baseline", {THR: _thresholds(0.85, baselines=["b1.json", "b2.json"], line=0.831),
                          "docs/evals/b1.json": _card_json(code_commit, 0.86, "baseline", "20260923T000001Z"),
                          "docs/evals/b2.json": _card_json(code_commit, 0.84, "baseline", "20260923T000002Z")})
    code, lines = r.decide()
    assert code == 0 and lines[-1].startswith("PASS (bootstrap)")
    # a later code change without re-running → the baseline is stale → red
    r.commit("late fix", {PROMPT: "v3\n"})
    code, lines = r.decide()
    assert code == 1 and any("changed after the eval commit" in line for line in lines)


def test_one_run_per_base_name_prefers_regraded_then_full(repo: Repo) -> None:
    """Full + slim + regraded files of one run count once (the regraded one)."""
    picked = cg._pick_per_run(["docs/evals/x.json", "docs/evals/x.slim.json", "docs/evals/x.regraded.json",
                               "docs/evals/y.slim.json"])
    assert picked == ["docs/evals/x.regraded.json", "docs/evals/y.slim.json"]


def test_ci_checks_out_full_history_and_the_pr_head() -> None:
    """The gate job fetches all history (the freshness check walks commits)
    and checks out the PR head, not the merge commit."""
    wf = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "v3.yml"
    if not wf.is_file():
        pytest.skip("repo root not present (portable copy)")
    doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
    job = doc["jobs"]["eval-gate"]
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    assert "pull_request.head.sha" in checkout["with"]["ref"]
    assert "check_eval_gate.py" in json.dumps(job["steps"])


# ── #253: contention records beside scorecards (T-6) and the step summary (T-10) ──

NEW_STAMP = "20260928T010000Z"          # on / after CONTENTION_SINCE → a record is expected


def _record(base: str, **over: Any) -> str:
    rec: Dict[str, Any] = {
        "schema": 1, "kind": "gpu_contention", "run": "stf-v3-eval-20260928T005900Z", "scorecard": base,
        "started_at": "2026-09-28T00:59:00Z", "ended_at": "2026-09-28T01:30:00Z", "interval_s": 30,
        "samples": 60, "sample_errors": 0, "concurrency": 3, "preemptions_before": 0, "preemptions_after": 0,
        "preemption_delta": 0, "vllm_restarted": False,
        "gpus": [{"index": 0, "total_mib": 46068, "others_mib_max": 0, "others_mib_mean": 0,
                  "others_sm_pct_max": 0, "others_sm_pct_mean": 0, "vllm_mib_max": 28400,
                  "project_other_mib_max": 0, "ollama_mib_max": 0, "util_pct_max": 90, "util_pct_mean": 60}]}
    rec.update(over)
    return json.dumps(rec)


def _passing_pr_with(repo: Repo, extra: Dict[str, str], stamp: str = NEW_STAMP):
    a = repo.commit("prompt", {PROMPT: "v2\n"})
    card_text = _card_json(a, 0.86, stamp=stamp)
    base = json.loads(card_text)["meta"]["base"]
    files = {f"docs/evals/{base}.json": card_text}
    files.update({k.replace("{base}", base): v.replace("{base}", base) for k, v in extra.items()})
    repo.commit("card", files)
    return base, repo.decide()


def test_a_contention_record_is_never_read_as_a_scorecard(repo: Repo) -> None:
    """① FM-1: the record beside the scorecard is not a second run."""
    base, (code, lines) = _passing_pr_with(repo, {"docs/evals/{base}.contention.json": _record("{base}")})
    assert code == 0 and lines[-1] == "PASS"
    assert not any("ignored" in line for line in lines)
    assert not cg._is_scorecard(f"docs/evals/{base}.contention.json")


def test_a_new_scorecard_without_its_record_warns_but_passes(repo: Repo) -> None:
    """② FM-7: new scorecards are expected to bring the record; old ones are not."""
    _, (code, lines) = _passing_pr_with(repo, {})
    assert code == 0 and any("no contention record" in line for line in lines)


def test_an_old_scorecard_without_a_record_is_not_warned_about(repo: Repo) -> None:
    _, (code, lines) = _passing_pr_with(repo, {}, stamp="20260926T000000Z")
    assert code == 0 and not any("contention" in line for line in lines)


@pytest.mark.parametrize("text,expect", [
    ("{not json", "contention record unreadable"),                                      # ③ FM-2
    (None, "schema 7 unknown"),                                                          # ④ FM-12
    ("mismatch", "belongs to"),                                                          # ⑤ FM-5
])
def test_bad_records_only_warn(repo: Repo, text: Optional[str], expect: str) -> None:
    """③–⑤: a broken, unknown-version or mismatched record → one WARN; the
    verdict is the same as without a record."""
    if text is None:
        body = _record("{base}", schema=7)
    elif text == "mismatch":
        body = _record("20260101T000000Z_deadbeef_gate")
    else:
        body = text
    _, (code, lines) = _passing_pr_with(repo, {"docs/evals/{base}.contention.json": body})
    assert code == 0 and lines[-1] == "PASS"
    assert any(line.startswith("WARN") and expect in line for line in lines), lines


def test_contention_warnings_show_up_in_the_step_summary_and_annotations(
        repo: Repo, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """T-10 / FM-13: in Actions, WARN lines become annotations and the
    verdict + lines go to the step summary (visible on the PR)."""
    busy = _record("{base}", gpus=[{"index": 1, "total_mib": 46068, "others_mib_max": 9000,
                                    "others_mib_mean": 8000, "others_sm_pct_max": 95,
                                    "others_sm_pct_mean": 80, "vllm_mib_max": 28400,
                                    "project_other_mib_max": 0, "ollama_mib_max": 0,
                                    "util_pct_max": 100, "util_pct_mean": 90}])
    _passing_pr_with(repo, {"docs/evals/{base}.contention.json": busy})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = cg.main(["--base", "main", "--head", "HEAD", "--repo", str(repo.root)])
    out = capsys.readouterr().out
    assert code == 0
    assert "::warning title=golden eval gate::WARN" in out and "other workloads held up to 9000 MiB" in out
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("## Golden eval gate") and "**PASS**" in text and "9000 MiB" in text


def test_no_summary_variable_is_fine(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside Actions nothing is written and nothing fails."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    repo.commit("api", {API: "v2\n"})
    assert cg.main(["--base", "main", "--head", "HEAD", "--repo", str(repo.root)]) == 0


def test_a_pending_confirmation_is_red_with_its_own_wording(repo: Repo) -> None:
    """FM-30: a dip in the only run → exit 1, 'pending confirmation / 未通过：待确认'."""
    a = repo.commit("prompt", {PROMPT: "v2\n"})
    c = card({M: {"g-000": 0.30, "g-001": 0.95, "g-002": 0.95, "g-003": 0.95}}, commit=a,
             stamp="20260926T000001Z")
    repo.commit("card", {"docs/evals/run1.json": json.dumps(c)})
    code, lines = repo.decide()
    assert code == 1 and "pending confirmation" in lines[-1] and "未通过：待确认" in lines[-1]

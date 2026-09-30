"""#253: the golden gate's floor across runs, the verdict wording, and the
contention-record rules (T-1 … T-5, T-7, T-9) — pure functions.

The floor used to fail a scorecard on any single golden under 0.4; PROD-11
failed twice at one commit on four different goldens that were fine in
the other runs.  Now a golden fails the floor only when it dips in two
runs of the PR (or three goldens dip in one run); a dip in the only run is
"pending confirmation".

Author: Xiangzhu Yan
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any, Dict, List, Optional

import pytest

from stf_v3.evals import gate
from tests.evals_helpers import card

M = "manual_agent"
O = "obd_agent"
IDS = [f"g-{i:03d}" for i in range(10)]
REPO = pathlib.Path(__file__).resolve().parents[2]


def _t(obd: bool = False) -> gate.Thresholds:
    lanes = {M: gate.LaneBaseline(mean=0.85, per_item={g: 0.85 for g in IDS}, tolerance=0.03)}
    if obd:
        lanes[O] = gate.LaneBaseline(mean=0.85, per_item={f"o-{i}": 0.85 for i in range(5)}, tolerance=0.06)
    return gate.Thresholds(lanes=lanes)


def _run(dips: Dict[str, float], stamp: str, lane: str = M, ids: Optional[List[str]] = None,
         config: Optional[Dict[str, Any]] = None, value: float = 0.9, obd: Optional[Dict[str, float]] = None
         ) -> Dict[str, Any]:
    scores = {g: dips.get(g, value) for g in (ids or IDS)}
    lanes = {lane: scores}
    if obd is not None:
        lanes[O] = obd
    c = card(lanes, stamp=stamp, config=config)
    c["_path"] = f"{stamp}.slim.json"
    return c


# ── T-1 the floor matrix (FM-23 / 24 / 26 / 27 / 30 / 32) ─────────


def test_same_golden_under_the_floor_in_two_runs_fails() -> None:
    """① A golden under 0.4 in both runs is a confirmed collapse → red."""
    ok, lines = gate.decide([_run({"g-003": 0.30}, "20260927T000001Z"),
                             _run({"g-003": 0.35}, "20260927T000002Z")], _t())
    assert ok is False and any("FAIL floor: g-003 is under 0.4 in 2 runs" in line for line in lines)


def test_different_goldens_dipping_once_each_pass() -> None:
    """② The PROD-11 pattern: one random dip per run on different goldens → green."""
    ok, lines = gate.decide([_run({"g-001": 0.30}, "20260927T000001Z"),
                             _run({"g-007": 0.35}, "20260927T000002Z")], _t())
    assert ok is True, lines


def test_a_third_run_cannot_push_a_confirmed_collapse_out_of_the_window() -> None:
    """③ FM-23: two runs dip on g-003, a third is clean → still red (the floor
    counts every comparable run of the PR, not just the newest two)."""
    ok, lines = gate.decide([_run({"g-003": 0.30}, "20260927T000001Z"),
                             _run({"g-003": 0.31}, "20260927T000002Z"),
                             _run({}, "20260927T000003Z")], _t())
    assert ok is False and any("g-003 is under 0.4 in 2 runs" in line for line in lines)


def test_a_dip_in_the_only_run_is_pending_confirmation_and_red() -> None:
    """④ D2 / FM-30: one run with a dip → not passed, 'pending confirmation'."""
    ok, lines = gate.decide([_run({"g-004": 0.30}, "20260927T000001Z")], _t())
    assert ok is False
    assert any(gate.PENDING_MARK in line and "未通过：待确认" in line and "g-004" in line for line in lines)


def test_three_dips_in_one_run_fail_the_floor_on_their_own() -> None:
    """⑤ FM-24: three goldens under the floor in one run → red even if the
    other run is clean (the observed noise is 1–2 per run)."""
    ok, lines = gate.decide([_run({"g-001": 0.3, "g-002": 0.3, "g-003": 0.3}, "20260927T000001Z"),
                             _run({}, "20260927T000002Z")], _t())
    assert ok is False and any("has 3 goldens under 0.4" in line for line in lines)


@pytest.mark.parametrize("score,dips", [(0.4, False), (0.3999, True)])
def test_exactly_0_4_is_not_a_dip(score: float, dips: bool) -> None:
    """⑥ FM-27: strictly below the floor, no rounding."""
    base = _t().lanes[M]
    got = gate.floor_dips({g: (score if g == "g-000" else 0.9) for g in IDS}, base, _t())
    assert ("g-000" in got) is dips


def test_a_missing_golden_fails_its_run_and_counts_as_a_dip() -> None:
    """⑦ FM-26 / FM-32: a run without g-009 fails the mean check ('missing')
    and g-009 counts as a 0.0 dip for the floor."""
    short = _run({}, "20260927T000001Z", ids=IDS[:-1])
    ok, why = gate.check_mean(gate.lane_scores(short)[M], _t().lanes[M], _t())
    assert ok is False and "baseline goldens missing" in why[0]
    assert gate.floor_dips(gate.lane_scores(short)[M], _t().lanes[M], _t()) == {"g-009": 0.0}


def test_both_means_failing_still_fails() -> None:
    """⑧ The mean rule is unchanged: two runs under baseline − tolerance → red."""
    ok, lines = gate.decide([_run({}, "20260927T000001Z", value=0.80),
                             _run({}, "20260927T000002Z", value=0.81)], _t())
    assert ok is False and any("mean 0.810 < baseline 0.850" in line for line in lines)


def test_a_floor_below_0_6_baseline_is_ignored_as_before() -> None:
    """FM-3 unchanged: goldens with a baseline under 0.6 have no floor."""
    t = gate.Thresholds(lanes={M: gate.LaneBaseline(mean=0.5, per_item={"a": 0.5, "b": 0.9})})
    ok, _ = gate.decide([card({M: {"a": 0.1, "b": 0.95}})], t)
    assert ok is True


# ── T-2 what the gate says (FM-16 / FM-25) ─────────────────────────


def test_output_names_the_runs_and_the_other_runs_scores() -> None:
    """FM-16: the lines say which runs fed the mean and the floor, and each
    dip shows the golden's score in the other runs."""
    a = _run({"g-001": 0.30}, "20260927T000001Z")
    b = _run({"g-007": 0.35}, "20260927T000002Z")
    ok, lines = gate.decide([a, b], _t())
    assert any(line.startswith(f"{M}: mean from 20260927T000001Z.slim.json, 20260927T000002Z.slim.json")
               for line in lines)
    assert any("g-001 fell to 0.300" in line and "20260927T000002Z.slim.json=0.900" in line for line in lines)


def test_a_run_far_below_the_mean_line_warns_even_when_the_lane_passes() -> None:
    """FM-25: A passes the mean with a dip, B's mean is below baseline − 2×tol
    with a different dip → green, but a WARN line flags B."""
    a = _run({"g-001": 0.30}, "20260927T000001Z", value=0.90)
    b = _run({"g-002": 0.35}, "20260927T000002Z", value=0.75)
    ok, lines = gate.decide([a, b], _t())
    assert ok is True, lines
    assert any(line.startswith(f"WARN {M} @ 20260927T000002Z.slim.json") and "2×tolerance" in line for line in lines)


def test_older_runs_outside_the_mean_window_are_named() -> None:
    """FM-16: a third run is listed as not used for the mean."""
    runs = [_run({}, f"20260927T00000{i}Z") for i in (1, 2, 3)]
    _, lines = gate.decide(runs, _t())
    assert any("20260927T000001Z.slim.json older than the newest 2" in line for line in lines)


# ── T-3 pairing per lane (FM-26 / FM-31) ──────────────────────────


def test_a_manual_only_rerun_pairs_for_manual_and_leaves_obd_to_the_full_run() -> None:
    """FM-31: full run (manual g-001 dip, OBD o-2 dip) + a manual-only rerun
    → manual confirmed clean by the pair; OBD has one run → pending."""
    obd_scores = {f"o-{i}": 0.9 for i in range(5)}
    full = _run({"g-001": 0.3}, "20260927T000001Z", obd=dict(obd_scores, **{"o-2": 0.2}))
    rerun = _run({}, "20260927T000002Z")
    ok, lines = gate.decide([full, rerun], _t(obd=True))
    assert f"{M}: PASS" in lines
    assert ok is False and any(line.startswith(f"{O}: NOT PASSED") and "o-2" in line for line in lines)


@pytest.mark.parametrize("second", ["ids", "data"])
def test_runs_with_other_goldens_or_golden_data_are_not_paired(second: str) -> None:
    """FM-26: different golden ids / golden data → the newest run stands
    alone for the floor (its dip is pending)."""
    first = _run({}, "20260927T000001Z", config={"data_sha256": {"golden/m.jsonl": "aa"}})
    if second == "ids":
        newest = _run({"g-001": 0.3}, "20260927T000002Z", ids=IDS + ["g-extra"],
                      config={"data_sha256": {"golden/m.jsonl": "aa"}})
    else:
        newest = _run({"g-001": 0.3}, "20260927T000002Z", config={"data_sha256": {"golden/m.jsonl": "bb"}})
    ok, lines = gate.decide([first, newest], _t())
    assert ok is False
    assert any("not compared for the floor" in line for line in lines)
    assert any(gate.PENDING_MARK in line for line in lines)


# ── T-4 order (FM-29) ──────────────────────────────────────────────


def test_order_is_the_recorded_run_stamp_then_the_name() -> None:
    """FM-29: input order does not matter; equal stamps tie-break on the name."""
    a = _run({"g-001": 0.3}, "20260927T000001Z")
    b = _run({}, "20260927T000002Z")
    c = _run({}, "20260927T000003Z")
    assert gate.decide([c, a, b], _t()) == gate.decide([a, b, c], _t())
    x, y = _run({}, "20260927T000005Z"), _run({}, "20260927T000005Z")
    x["_path"], y["_path"] = "b.json", "a.json"
    assert [gate.order_key(z) for z in sorted([x, y], key=gate.order_key)] == [
        ("20260927T000005Z", "a.json"), ("20260927T000005Z", "b.json")]


# ── T-5 the real scorecards (FM-8 / FM-35) ─────────────────────────

REAL = REPO / "docs" / "evals"
P11 = ["20260926T184632Z_335e8ad7_gate_local_think-off.slim.json",
       "20260926T192619Z_335e8ad7_gate_local_think-off.slim.json"]


def _real(name: str) -> Dict[str, Any]:
    path = REAL / name
    if not path.is_file():
        pytest.skip("docs/evals not present (portable copy)")
    c = json.loads(path.read_text(encoding="utf-8"))
    c["_path"] = name
    return c


def _real_thresholds() -> gate.Thresholds:
    path = REPO / "stf_v3" / "evals" / "thresholds.yaml"
    if not path.is_file():
        pytest.skip("thresholds not present")
    return gate.load_thresholds(path)


def test_prod11_scorecards_pass_together_without_the_exempt_label() -> None:
    """The case that forced `eval-exempt` on PR #252 now passes on its own."""
    ok, lines = gate.decide([_real(P11[0]), _real(P11[1])], _real_thresholds())
    assert ok is True, lines
    assert any("lookup-006 fell to 0.358" in line and "=0.992" in line for line in lines)


def test_prod11_first_run_alone_is_pending_on_its_two_dips() -> None:
    ok, lines = gate.decide([_real(P11[0])], _real_thresholds())
    pending = [line for line in lines if gate.PENDING_MARK in line]
    assert ok is False and len(pending) == 2
    assert any("lookup-006" in line for line in pending) and any("compound-001" in line for line in pending)


def test_the_0924_gate_and_baseline_verdicts_are_unchanged() -> None:
    """FM-35: the 09-24 gate run still passes; the two baseline runs still
    pass acceptance on the lines in the thresholds file."""
    t = _real_thresholds()
    assert gate.decide([_real("20260924T110437Z_2fd63fb2_gate_local_think-off.slim.json")], t)[0] is True
    cards = [c for c in (_real_or_none(n) for n in t.baseline_scorecards) if c is not None]
    if len(cards) != len(t.baseline_scorecards):
        pytest.skip("baseline scorecards not in docs/evals")
    lines = {lane: b.acceptance_line for lane, b in t.lanes.items() if b.acceptance_line is not None}
    tols = {lane: (b.tolerance if b.tolerance is not None else t.tolerance) for lane, b in t.lanes.items()}
    assert gate.acceptance(cards, lines, tols)[0] is True


def _real_or_none(name: str) -> Optional[Dict[str, Any]]:
    for cand in (REAL / name, REAL / name.replace(".json", ".slim.json")):
        if cand.is_file():
            c = json.loads(cand.read_text(encoding="utf-8"))
            c["_path"] = cand.name
            return c
    return None


# ── T-7 contention warnings (FM-4 / 10 / 15 / 21) ──────────────────

BASE = "20260927T010203Z_0123abcd_gate_local_think-off"


def _rec(**gpu: float) -> Dict[str, Any]:
    g = {"index": 0, "total_mib": 46068, "others_mib_max": 0, "others_mib_mean": 0, "others_sm_pct_max": 0,
         "others_sm_pct_mean": 0, "vllm_mib_max": 28400, "project_other_mib_max": 0, "ollama_mib_max": 0,
         "util_pct_max": 90, "util_pct_mean": 60}
    g.update(gpu)
    return {"schema": 1, "kind": "gpu_contention", "run": "stf-v3-eval-20260927T010200Z", "scorecard": BASE,
            "started_at": "2026-09-27T01:02:00Z", "ended_at": "2026-09-27T01:40:00Z", "interval_s": 30,
            "samples": 70, "sample_errors": 0, "concurrency": 3, "preemptions_before": 5,
            "preemptions_after": 5, "preemption_delta": 0, "vllm_restarted": False, "gpus": [g]}


@pytest.mark.parametrize("gpu,expect", [
    ({"others_mib_max": 1024, "others_sm_pct_max": 5}, None),
    ({"project_other_mib_max": 30000}, None),                  # our own MinerU is not "other users"
    ({"others_mib_max": 3000}, "other workloads held up to 3000 MiB"),
    ({"others_sm_pct_max": 60}, "compute peaked at 60 %"),
    ({"ollama_mib_max": 28000}, "Ollama held 28000 MiB"),
])
def test_contention_thresholds(gpu: Dict[str, float], expect: Optional[str]) -> None:
    """FM-15: only > 2 GB / > 20 % of other users, or Ollama, warn."""
    warns = gate.contention_warnings(_rec(**gpu), BASE)
    assert (warns == []) if expect is None else any(expect in w for w in warns), warns


def test_preemptions_and_restarts() -> None:
    """FM-10: a positive delta warns; a restart (counter went back) says so."""
    r = _rec()
    r.update(preemption_delta=4)
    assert any("preempted 4" in w for w in gate.contention_warnings(r, BASE))
    r.update(preemption_delta=None, vllm_restarted=True)
    assert any("restarted" in w for w in gate.contention_warnings(r, BASE))


def test_a_record_of_another_scorecard_is_flagged() -> None:
    """FM-5: record and scorecard names must match."""
    assert any("belongs to" in w for w in gate.contention_warnings(_rec(), "20260101T000000Z_x_gate"))


@pytest.mark.parametrize("patch", [
    {"user": "martin"}, {"cmdline": "python train.py"}, {"api_key": "x"},
    {"run": "stf-v3-eval-20260927T010200Z sk-abcdef"}, {"scorecard": BASE + "_token"},
])
def test_anything_outside_the_whitelist_makes_the_record_unusable(patch: Dict[str, str]) -> None:
    """FM-18 / FM-19: names, command lines, keys, secret-looking strings → rejected."""
    r = _rec()
    r.update(patch)
    assert gate.validate_contention(r) != []
    assert gate.contention_warnings(r, BASE)[0].startswith("contention record unusable")


def test_unknown_schema_is_reported_not_read() -> None:
    """FM-12: a newer record format → one warning, nothing else."""
    r = _rec()
    r["schema"] = 2
    assert gate.contention_warnings(r, BASE) == ["contention record unusable: schema 2 unknown (this gate reads 1)"]


# ── T-9 the CI gate stays stdlib + PyYAML (FM-34) ─────────────────


@pytest.mark.parametrize("path", [
    "src/stf_v3/evals/gate.py", "scripts/check_eval_gate.py", "scripts/gpu_contention.py"])
def test_gate_code_imports_only_stdlib_and_yaml(path: str) -> None:
    """The CI gate job installs nothing but PyYAML; the sampler runs on the
    host's python3 with nothing installed."""
    import sys

    src = (pathlib.Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    allowed = set(getattr(sys, "stdlib_module_names", set())) | {"yaml", "stf_v3", "__future__"}
    for node in ast.walk(ast.parse(src)):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            if path.endswith("gpu_contention.py"):
                assert top != "yaml" and top != "stf_v3", n
            assert top in allowed or not allowed - {"yaml", "stf_v3", "__future__"}, n

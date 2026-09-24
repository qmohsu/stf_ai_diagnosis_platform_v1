"""PROD-10 T-3 / T-9: gate rules, acceptance and budget calibration
(FM-3 / FM-5 / FM-11 / FM-19 / FM-25 / FM-36 / FM-40) — pure functions.

Author: Xiangzhu Yan
"""

from __future__ import annotations

from typing import Dict

import pytest

from tests.evals_helpers import card, record, uniform
from stf_v3.evals import gate
from stf_v3.evals import scorecard as sc

M = "manual_agent"
O = "obd_agent"


def _t(per_item: Dict[str, float], mean: float) -> gate.Thresholds:
    return gate.Thresholds(lanes={M: gate.LaneBaseline(mean=mean, per_item=per_item)})


def test_mean_boundary_around_baseline_minus_tolerance() -> None:
    """0.85 baseline: 0.82 passes (== baseline − 0.03), 0.819 fails."""
    base = {f"g-{i:03d}": 0.85 for i in range(4)}
    t = _t(base, 0.85)
    assert gate.decide([card(uniform(M, 4, 0.82))], t)[0] is True
    ok, lines = gate.decide([card(uniform(M, 4, 0.819))], t)
    assert ok is False and any("mean 0.819" in line for line in lines)


def test_floor_only_for_goldens_whose_baseline_is_at_least_0_6() -> None:
    """FM-3: a golden with baseline 0.5 may dip to 0.39; one with 0.8 may not."""
    base = {"g-a": 0.5, "g-b": 0.8, "g-c": 0.9, "g-d": 0.9}
    t = _t(base, 0.775)
    dip_low = card({M: {"g-a": 0.39, "g-b": 0.85, "g-c": 0.95, "g-d": 0.95}})
    assert gate.decide([dip_low], t)[0] is True
    dip_high = card({M: {"g-a": 0.9, "g-b": 0.39, "g-c": 0.95, "g-d": 0.95}})
    ok, lines = gate.decide([dip_high], t)
    assert ok is False and any("g-b fell to 0.390" in line for line in lines)


def test_one_rerun_allowed_second_passing_run_lets_it_through() -> None:
    """D1: first run fails, second passes → green; only the newest two count."""
    base = {f"g-{i:03d}": 0.85 for i in range(4)}
    t = _t(base, 0.85)
    bad = card(uniform(M, 4, 0.70), stamp="20260923T000001Z")
    good = card(uniform(M, 4, 0.86), stamp="20260923T000002Z")
    assert gate.decide([bad, good], t)[0] is True
    # three runs: the oldest passing one is outside the window
    assert gate.decide([good, bad, bad], t)[0] is False


def test_missing_baseline_goldens_fail_the_lane() -> None:
    """A run that skips a baseline golden cannot pass (no cherry-picking)."""
    t = _t({"g-a": 0.8, "g-b": 0.8}, 0.8)
    assert gate.decide([card({M: {"g-a": 0.9}})], t)[0] is False


@pytest.mark.parametrize("kwargs,reason", [
    ({"backend": "cloud"}, "backend=cloud"),
    ({"thinking": "on"}, "thinking=on"),
    ({"purpose": "demo"}, "purpose=demo"),
    ({"purpose": "calibration"}, "purpose=calibration"),
    ({"budget_scale": 2.0}, "budget_scale=2.0"),
    ({"judge": "deepseek/deepseek-v4-pro"}, "judge_model="),
    ({"statuses": {"g-000": "judge_failed"}}, "invalid"),
    ({"failed": [{"lane": M, "id": "g-9", "status": "eval_timeout"}]}, "incomplete"),
])
def test_scorecards_that_never_count(kwargs: dict, reason: str) -> None:
    """FM-5 / FM-19 / FM-25: cloud, thinking-on, demo, calibration, scaled
    budgets, another judge, a judge failure, an eval timeout → ignored."""
    c = card(uniform(M, 3, 0.95), **kwargs)
    ok, why = gate.eligibility(c)
    assert ok is False and any(reason in w for w in why), why
    t = _t({f"g-{i:03d}": 0.5 for i in range(3)}, 0.5)
    assert gate.decide([c], t)[0] is False


def test_acceptance_mean_of_two_and_each_run_within_tolerance() -> None:
    """FM-11: 0.84 + 0.82 → mean 0.83 < 0.831 fails; 0.85 + 0.815 passes;
    0.87 + 0.80 fails (second run below line − 0.03)."""
    lines = {M: 0.831}
    mk = lambda v: card(uniform(M, 3, v))  # noqa: E731
    assert gate.acceptance([mk(0.84), mk(0.82)], lines)[0] is False
    assert gate.acceptance([mk(0.85), mk(0.815)], lines)[0] is True
    assert gate.acceptance([mk(0.87), mk(0.80)], lines)[0] is False


def test_build_baseline_is_the_mean_of_the_runs() -> None:
    """Baseline mean and per-golden values are averages of the two runs."""
    a = card({M: {"g-a": 0.8, "g-b": 1.0}, O: {"o-a": 0.9}})
    b = card({M: {"g-a": 0.6, "g-b": 1.0}, O: {"o-a": 1.0}})
    base = gate.build_baseline([a, b])
    assert base[M]["per_item"] == {"g-a": 0.7, "g-b": 1.0} and base[M]["mean"] == pytest.approx(0.85)
    assert base[O]["mean"] == pytest.approx(0.95)


def test_stale_baseline_warning_on_unmanaged_drift() -> None:
    """FM-28: judge model / vLLM version / manual library drift → warning."""
    t = gate.Thresholds(baseline_config={"vllm_version": "0.24.0", "judge_model": gate.DEFAULT_JUDGE_MODEL,
                                         "manual_sha256": {"m": {"md": "aa"}}})
    c = card(uniform(M, 1, 0.9), config={"vllm_version": "0.25.0", "manual_sha256": {"m": {"md": "bb"}}})
    warns = gate.stale_warnings(c, t)
    assert any("vllm_version" in w for w in warns) and any("manual_sha256" in w for w in warns)
    assert not any("judge_model" in w for w in warns)


def test_managed_path_patterns() -> None:
    """D2 + FM-6: prompts, tools, sub-agents, model adapter, manual reader,
    the evaluator, its data and the dependency pins are managed; the API is not."""
    pats = gate.DEFAULT_MANAGED_PATHS
    for p in ("stf_v3/src/stf_v3/diagnosis/agent/prompts.py", "stf_v3/src/stf_v3/diagnosis/tools/obd_signals.py",
              "stf_v3/src/stf_v3/knowledge/manual_index.py", "stf_v3/evals/golden/manual_mws150a.jsonl",
              "stf_v3/src/stf_v3/evals/metrics.py", "stf_v3/pyproject.toml", "infra/docker-compose.vllm.yml"):
        assert gate.is_managed(p, pats), p
    for p in ("stf_v3/src/stf_v3/vehicles/router.py", "docs/evals/x.json", "stf_v3/scripts/smoke_e2e.py",
              "stf_v3/src/stf_v3/evals/gate.py", "stf_v3/src/stf_v3/evals/summary.py",
              "stf_v3/src/stf_v3/evals/scorecard.py"):
        assert not gate.is_managed(p, pats), p
    for p in ("stf_v3/src/stf_v3/evals/judge.py", "stf_v3/src/stf_v3/evals/lanes.py", "stf_v3/src/stf_v3/evals/cli.py",
              "stf_v3/evals/thresholds.yaml"):
        assert gate.is_managed(p, pats), p


def test_per_lane_tolerance_obd_006_manual_003() -> None:
    """D6: OBD tolerates 0.06 (15 goldens are noisy), the manual lane 0.03;
    a lane without its own value falls back to the file-level tolerance."""
    t = gate.Thresholds(lanes={
        M: gate.LaneBaseline(mean=0.88, per_item={f"g-{i:03d}": 0.88 for i in range(4)}, tolerance=0.03),
        O: gate.LaneBaseline(mean=0.885, per_item={f"o-{i:03d}": 0.885 for i in range(4)}, tolerance=0.06)})
    c = card({M: {f"g-{i:03d}": 0.85 for i in range(4)}, O: {f"o-{i:03d}": 0.83 for i in range(4)}})
    ok, lines = gate.decide([c], t)
    assert ok is True, lines                          # OBD 0.83 ≥ 0.885 − 0.06
    c2 = card({M: {f"g-{i:03d}": 0.84 for i in range(4)}, O: {f"o-{i:03d}": 0.83 for i in range(4)}})
    ok, lines = gate.decide([c2], t)
    assert ok is False and any("mean 0.840 < baseline 0.880 − 0.03" in line for line in lines)
    fallback = gate.parse_thresholds({"tolerance": 0.05, "lanes": {M: {"mean": 0.9, "per_item": {}}}})
    assert fallback.lanes[M].tolerance is None and fallback.tolerance == 0.05


def test_acceptance_uses_the_lane_tolerance() -> None:
    """D5 + D6: OBD's line is V3's own baseline; each run may sit 0.06 under it."""
    obd = lambda v: card(uniform(O, 3, v))  # noqa: E731
    assert gate.acceptance([obd(0.897), obd(0.872)], {O: 0.884})[0] is True       # line = baseline mean, floored
    assert gate.acceptance([obd(0.897), obd(0.872)], {O: 0.884}, 0.01)[0] is False
    assert gate.build_baseline([obd(0.9), obd(0.87)])[O]["tolerance"] == 0.06


# ── calibration (T-9) ─────────────────────────────────────────────

def _cal_card(walls, reqs, stopped=None):
    stopped = stopped or ["complete"] * len(walls)
    c = card({})
    c["records"] = [record(M, f"g-{i}", 0.9, wall=w, requests=r, stopped=s)
                    for i, (w, r, s) in enumerate(zip(walls, reqs, stopped))]
    c["meta"]["expected"] = len(walls)
    return sc.finalize(c)


def test_calibration_is_p95_times_margin() -> None:
    """New default = nearest-rank 95th percentile × 1.5, rounded up."""
    walls = [float(x) for x in range(10, 210, 10)]     # 20 goldens: 10 … 200 s
    reqs = [3] * 19 + [11]
    out = gate.calibrate(_cal_card(walls, reqs))
    assert out["p95"]["wall_s"] == 190.0 and out["p95"]["requests"] == 3
    assert out["proposal"] == {"subagent_wall_clock_s": 290.0, "subagent_request_limit": 5}
    assert out["max"]["requests"] == 11


def test_calibration_refuses_when_too_many_goldens_hit_the_budget() -> None:
    """FM-36: > 10 % censored (stopped by budget / timeout) → no proposal."""
    walls = [100.0] * 10
    stopped = ["complete"] * 8 + ["timeout", "budget"]
    out = gate.calibrate(_cal_card(walls, [5] * 10, stopped))
    assert out["censored"] == 2 and out["proposal"] is None and "raise the budget scale" in out["refused"]

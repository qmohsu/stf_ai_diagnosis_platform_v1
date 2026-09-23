"""PROD-10 T-8 / T-11: the eval command line — budget scaling, thinking on,
the cloud path, preflight refusals, acceptance → thresholds
(FM-23 / FM-24 / FM-34 / FM-35 / FM-43 / FM-47 / FM-53).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import httpx as httpx1
import httpx2 as httpx
import pytest
import yaml
from pydantic_ai import Agent

from stf_v3.diagnosis.agent import model as m
from stf_v3.diagnosis.agent.deps import Budgets
from stf_v3.evals import cli as evcli
from stf_v3.evals import gate
from stf_v3.evals.lanes import LaneContext, build_item_deps
from stf_v3.evals.runner import RunOptions, apply_overrides
from stf_v3.settings import Settings
from tests.agent_helpers import FIXTURES
from tests.evals_helpers import card, uniform

EVALS_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stf_v3" / "evals"


def _capture(store: List[Dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        store.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _body(settings: Settings, *, subagent: bool = True) -> Dict[str, Any]:
    bodies: List[Dict[str, Any]] = []
    model = m.build_model(settings, http_client=_capture(bodies))
    await Agent(model, instructions="t").run("hi", model_settings=m.model_settings(settings, subagent=subagent,
                                                                                  model=model))
    return bodies[0]


def test_budget_scale_doubles_the_sub_agent_budgets() -> None:
    """FM-35 / FM-36 / FM-53: ``--budget-scale 2`` doubles wall clock,
    request limit and output cap of the sub-agents (eval process only)."""
    s = Settings()
    profile = apply_overrides(s, RunOptions(purpose="calibration", budget_scale=2.0))
    d = m.profile_defaults(profile)
    b = Budgets.from_settings(s, profile)
    assert b.subagent_wall_clock_s == 2 * d["subagent_wall_clock_s"]
    assert b.subagent_request_limit == 2 * d["subagent_request_limit"]
    assert s.subagent_max_tokens == 2 * d["subagent_max_tokens"]
    assert Budgets.from_settings(Settings(), profile).subagent_wall_clock_s == d["subagent_wall_clock_s"]


async def test_thinking_on_changes_the_request_and_the_default_stays_off() -> None:
    """D3 / FM-45: ``--thinking on`` sends ``enable_thinking=true``; the
    production default (a fresh Settings) still sends ``false``."""
    s = Settings()
    apply_overrides(s, RunOptions(purpose="comparison", thinking=True, budget_scale=2.0))
    on = await _body(s)
    off = await _body(Settings())
    assert on["chat_template_kwargs"] == {"enable_thinking": True}
    assert off["chat_template_kwargs"] == {"enable_thinking": False}
    assert on.get("max_completion_tokens", on.get("max_tokens")) == 2 * off.get("max_completion_tokens",
                                                                                   off.get("max_tokens"))
    assert Settings().llm_thinking is False


def test_cloud_goes_through_the_comparison_path_with_a_vin_pseudonym() -> None:
    """FM-34 / FM-47: cloud = the adapter's own cloud path (generic profile,
    source ``cloud``); the vehicle line carries the fake VIN's pseudonym."""
    s = Settings(cloud_llm_enabled=True, cloud_llm_api_key="k")
    profile = apply_overrides(s, RunOptions(purpose="comparison", cloud=True))
    model = m.build_model(s, cloud=True)
    assert profile == m.PROFILE_GENERIC and m.source_of(model).kind == "cloud"
    ctx = LaneContext(model=model, manuals=[], manual_root=FIXTURES, fixture_dir=FIXTURES,
                      budgets_factory=lambda: Budgets.from_settings(s, profile), model_is_local=False)
    label = build_item_deps(ctx).vehicle_label()
    assert "JHMGK5830HX202404" not in label and "VIN V-" in label


def test_the_eval_code_never_touches_the_allow_cloud_switch() -> None:
    """FM-34: no eval module reads or writes the product's cloud opt-in."""
    for f in EVALS_SRC.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "llm_allow_cloud" not in text and "LLM_ALLOW_CLOUD" not in text.replace(
            "Never reads or writes ``STF_V3_LLM_ALLOW_CLOUD``", ""), f.name


def test_check_model_waits_then_refuses_and_reports_the_version() -> None:
    """FM-23: model missing → RuntimeError naming it; present → one warm-up
    generation and the vLLM version recorded."""
    def handler(request: httpx1.Request) -> httpx1.Response:
        if request.url.path.endswith("/models"):
            return httpx1.Response(200, json={"data": [{"id": "Qwen/Qwen3.6-27B-FP8"}]})
        if request.url.path.endswith("/version"):
            return httpx1.Response(200, json={"version": "0.24.0"})
        return httpx1.Response(200, json={"choices": [{"message": {"content": "ready"}}]})

    t = httpx1.MockTransport(handler)
    info = evcli.check_model("http://127.0.0.1:8010/v1", "none", "Qwen/Qwen3.6-27B-FP8", local=True, transport=t)
    assert info == {"vllm_version": "0.24.0"}
    with pytest.raises(RuntimeError, match="other-model"):
        evcli.check_model("http://127.0.0.1:8010/v1", "none", "other-model", local=True, wait_s=0, transport=t)


def test_run_refuses_without_the_real_tokenizer(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """FM-24 / FM-43: cl100k_base unavailable → exit 4 before anything runs
    (never the silent len/4 fallback)."""
    import tiktoken

    def boom(name: str) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(tiktoken, "get_encoding", boom)
    assert evcli.main(["run", "--purpose", "adhoc", "--out", "unused"]) == 4
    assert "tokenizer cl100k_base unavailable" in capsys.readouterr().err


def test_run_refuses_when_the_model_endpoint_is_down(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """FM-23: endpoint check fails → exit 4, no goldens run."""
    def down(*a: Any, **k: Any) -> Any:
        raise RuntimeError("model endpoint check failed: ConnectError")

    monkeypatch.setattr(evcli, "check_model", down)
    monkeypatch.setattr(evcli, "check_tokenizer", lambda: "cl100k_base")
    assert evcli.main(["run", "--purpose", "adhoc", "--out", "unused"]) == 4
    assert "ConnectError" in capsys.readouterr().err


def test_run_refuses_thinking_on_the_cloud() -> None:
    """Thinking is a vLLM-profile switch; with --cloud it would be meaningless."""
    assert evcli.main(["run", "--purpose", "comparison", "--cloud", "--thinking", "on"]) == 4


def test_accept_writes_thresholds_from_two_baseline_runs(tmp_path: pathlib.Path) -> None:
    """``accept``: acceptance rule → thresholds.yaml with baseline means,
    per-golden values, the managed paths and the unmanaged config snapshot."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(card(uniform("manual_agent", 3, 0.86) | uniform("obd_agent", 2, 0.95, "o"),
                                 purpose="baseline")), encoding="utf-8")
    b.write_text(json.dumps(card(uniform("manual_agent", 3, 0.84) | uniform("obd_agent", 2, 0.94, "o"),
                                 purpose="baseline", stamp="20260923T000009Z")), encoding="utf-8")
    out = tmp_path / "thresholds.yaml"
    assert evcli.main(["accept", "--scorecards", str(a), str(b), "--lines", "manual_agent=0.831,obd_agent=0.938",
                       "--write-thresholds", str(out)]) == 0
    t = gate.load_thresholds(out)
    assert t.lanes["manual_agent"].mean == pytest.approx(0.85) and t.lanes["obd_agent"].acceptance_line == 0.938
    assert t.baseline_scorecards == ["a.json", "b.json"] and t.managed_paths == gate.DEFAULT_MANAGED_PATHS
    assert set(yaml.safe_load(out.read_text(encoding="utf-8"))["baseline_config"]) == set(gate.UNMANAGED_CONFIG_KEYS)
    # below the line → refused, nothing written
    low = tmp_path / "low.json"
    low.write_text(json.dumps(card(uniform("manual_agent", 3, 0.70), purpose="baseline")), encoding="utf-8")
    assert evcli.main(["accept", "--scorecards", str(low), str(low), "--lines", "manual_agent=0.831"]) == 1


def test_accept_refuses_a_non_baseline_scorecard(tmp_path: pathlib.Path) -> None:
    """A calibration / cloud / thinking-on scorecard can never be a baseline."""
    c = tmp_path / "c.json"
    c.write_text(json.dumps(card(uniform("manual_agent", 3, 0.9), purpose="baseline", backend="cloud")),
                 encoding="utf-8")
    assert evcli.main(["accept", "--scorecards", str(c), str(c), "--lines", "manual_agent=0.5"]) == 1


def test_calibrate_and_summary_commands(tmp_path: pathlib.Path, capsys: Any) -> None:
    """``calibrate`` prints the proposal; ``summary`` writes Markdown."""
    c = tmp_path / "c.json"
    c.write_text(json.dumps(card(uniform("manual_agent", 20, 0.9), purpose="calibration", budget_scale=2.0)),
                 encoding="utf-8")
    assert evcli.main(["calibrate", "--scorecard", str(c)]) == 0
    assert '"proposal"' in capsys.readouterr().out
    md = tmp_path / "s.md"
    assert evcli.main(["summary", "--scorecards", str(c), "--out", str(md)]) == 0
    assert "# Golden 评测摘要" in md.read_text(encoding="utf-8")

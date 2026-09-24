"""PROD-10 T-5 / T-6 / T-7 / T-10: a whole eval run offline.

The real lanes run the real V3 sub-agents on a scripted ``FunctionModel``
(no network), against the real golden files and the road-test fixture;
the judge is a stub.  Covers the scorecard contract, completeness,
secret scrubbing, per-golden isolation, eval-level timeouts, the
watchdog, judge retries, re-grading and the summary.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any, Dict, List

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from stf_v3.diagnosis.agent.deps import Budgets
from stf_v3.evals import cli as evcli
from stf_v3.evals import scorecard as sc
from stf_v3.evals.lanes import LANE_MANUAL, LANE_OBD, ItemStats, LaneContext, run_manual_item
from stf_v3.evals.orchestrator import (
    STATUS_EVAL_TIMEOUT,
    STATUS_JUDGE_FAILED,
    STATUS_OK,
    WorkItem,
    execute,
)
from stf_v3.evals.runner import RunOptions, RunRefused, exit_code, item_timeout_s, map_manual_ids, run_eval
from stf_v3.evals.schemas import GoldenEntry, Grade, SystemRunResult
from stf_v3.evals.summary import summarize
from tests.agent_helpers import FIXTURES, response, small_manual, tool_call, which_agent

V3 = pathlib.Path(__file__).resolve().parents[1]
DATA = str(V3 / "evals")
GOLDEN_MANUAL_ID = "0a2ba199-665f-41aa-a106-1163cad68d16"
MAN_IDS = ["lookup-001", "procedural-002", "cross-001"]
OBD_IDS = ["yamaha-road-test-signal-stats-001", "yamaha-road-test-event-finding-001"]
FAKE_KEY = "sk-or-v1-" + "ab12" * 16


def _yamaha_manual(mid: str = GOLDEN_MANUAL_ID) -> Any:
    return small_manual(mid, manufacturer="Yamaha", vehicle_model="TRICITY155", factory_code="MWS150-A")


def _question(messages: List[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def scripted_model(hang_on: str = "", fail_on: str = "", delay: float = 0.0) -> FunctionModel:
    """Manual: read one section chosen by the question, then answer.
    OBD: answer with one signal citation.  ``hang_on`` / ``fail_on`` make the
    golden whose question contains that text hang / raise."""

    async def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        q = _question(messages)
        if hang_on and hang_on in q:
            await asyncio.sleep(3600)
        if fail_on and fail_on in q:
            raise RuntimeError(f"model exploded with key {FAKE_KEY}")
        if delay:
            await asyncio.sleep(delay)
        agent = which_agent(info)
        n = sum(1 for m in messages if isinstance(m, ModelResponse))
        if agent == "obd":
            return response(TextPart(content=json.dumps({
                "summary": "Peak RPM 3906 on A_KL_RPM.",
                "signal_citations": [{"signal": "A_KL_RPM", "stat": "max", "value": 3906.0}],
                "dtc_citations": [], "limitations": []})))
        section = "3.1 Battery" if "battery" in q.lower() else "2.1 Fuel Pump Troubleshooting"
        if n == 0:
            return response(tool_call("read_manual_section", manual_id=_yamaha_manual().id, section=section))
        return response(TextPart(content=json.dumps({
            "summary": f"Answer from {section}.",
            "citations": [{"manual_id": _yamaha_manual().id, "slug": section, "quote": ""}]})))

    return FunctionModel(fn, model_name="scripted-eval")


def grade_stub(overall: float = 0.9, fail_times: int = 0):
    calls = {"n": 0}

    async def grade_fn(entry: GoldenEntry, run: SystemRunResult, client: Any = None) -> Grade:
        calls["n"] += 1
        failing = calls["n"] <= fail_times
        return Grade(exploration_cost=0.0, fact_recall=1.0, fact_density=1.0, hallucination_penalty=1.0,
                     citation_quality=1.0, answer_quality=0.0 if failing else overall,
                     overall=0.45 if failing else overall,
                     reasoning="Judge (0.00): [judge failure] api error: RateLimitError" if failing else "ok")

    grade_fn.calls = calls  # type: ignore[attr-defined]
    return grade_fn


def _budgets() -> Budgets:
    return Budgets(wall_clock_s=60.0, subagent_wall_clock_s=20.0, subagent_request_limit=6)


async def _run(tmp_path: pathlib.Path, model: FunctionModel, *, ids=None, grade_fn=None, manuals=None,
               max_total_s=None, lanes=(LANE_MANUAL, LANE_OBD), secrets=()) -> Any:
    opts = RunOptions(purpose="gate", lanes=list(lanes), ids=ids or MAN_IDS + OBD_IDS, out_dir=tmp_path,
                      data_dir=DATA, concurrency=3, judge_retry_delays=(0.0,), max_total_s=max_total_s)
    return await run_eval(opts, model=model, manuals=manuals or [_yamaha_manual()], manual_root=FIXTURES,
                          budgets_factory=_budgets, config={"judge_model": "z-ai/glm-5.1", "tokenizer": "cl100k_base",
                                                            "model": "scripted"},
                          git_commit="a" * 40, grade_fn=grade_fn or grade_stub(), secrets=secrets,
                          stamp="20260923T010203Z")


async def test_a_full_offline_run_writes_a_complete_valid_scorecard(tmp_path: pathlib.Path) -> None:
    """T-6: 3 manual + 2 OBD goldens → one record each with the V3 item
    stats; meta carries mode, counts, config; files: full, slim, progress."""
    path, card = await _run(tmp_path, scripted_model())
    meta = card["meta"]
    assert meta["complete"] and meta["valid"] and meta["expected"] == meta["completed"] == 5
    assert meta["mode"] == {"backend": "local", "thinking": "off", "purpose": "gate", "budget_scale": 1.0}
    assert meta["config"]["data_sha256"] and meta["config"]["manual_id_map"] == {}
    for key in sc.REQUIRED_META_KEYS:
        assert key in meta, key
    for r in card["records"]:
        for key in sc.REQUIRED_RECORD_KEYS:
            assert key in r
        for key in sc.REQUIRED_GRADE_KEYS:
            assert key in r["grade"]
        assert r["item"]["status"] == STATUS_OK and r["item"]["requests"] >= 1
        assert set(r["item"]) >= {"stopped_reason", "requests", "tool_calls", "total_tokens", "wall_s", "nudged",
                                  "thinking_chars"}
    manual = [r for r in card["records"] if r["result"]["system_label"] == LANE_MANUAL]
    assert all("--- Cited sections (1) ---" in r["result"]["output_text"] for r in manual)
    obd = [r for r in card["records"] if r["result"]["system_label"] == LANE_OBD]
    assert all("A_KL_RPM (max) = 3906.0" in r["result"]["output_text"] for r in obd)
    assert path.name == "20260923T010203Z_aaaaaaaa_gate_local_think-off.json"
    assert (tmp_path / "20260923T010203Z_aaaaaaaa_gate_local_think-off.slim.json").is_file()
    assert not list(tmp_path.glob("*.partial.json"))
    progress = (tmp_path / "20260923T010203Z_aaaaaaaa_gate_local_think-off.progress.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(progress) == 5 and json.loads(progress[-1])["n"] == 5
    assert exit_code(card) == 0


async def test_a_second_run_in_the_same_second_does_not_overwrite(tmp_path: pathlib.Path) -> None:
    """FM-14: same stamp → ``-2`` suffix, both files kept."""
    p1, _ = await _run(tmp_path, scripted_model(), ids=MAN_IDS[:1], lanes=(LANE_MANUAL,))
    p2, _ = await _run(tmp_path, scripted_model(), ids=MAN_IDS[:1], lanes=(LANE_MANUAL,))
    assert p1 != p2 and p1.is_file() and p2.is_file() and p2.stem.endswith("-2")


async def test_a_sub_agent_error_invalidates_and_no_secret_is_written(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-6 / FM-33 / FM-18: one golden's model raises with a key in the
    message → the sub-agent ends with stopped_reason=error, the golden is a
    ``run_error`` (infrastructure, not the model's answer), the scorecard is
    invalid, and the key appears in no written file."""
    monkeypatch.setenv("STF_V3_OPENROUTER_API_KEY", FAKE_KEY)
    path, card = await _run(tmp_path, scripted_model(fail_on="coolant"), secrets=sc.secret_values())
    meta = card["meta"]
    bad = [r for r in card["records"] if r["item"]["status"] == "run_error"]
    assert len(bad) == 1 and bad[0]["item"]["stopped_reason"] == "error"
    assert meta["valid"] is False and any("sub-agent error" in r for r in meta["invalid_reasons"])
    for f in tmp_path.iterdir():
        assert FAKE_KEY not in f.read_text(encoding="utf-8"), f.name
    assert exit_code(card) == 2


async def test_eval_level_timeout_marks_one_golden_and_the_rest_finish() -> None:
    """T-7 / FM-32: a run that never returns hits the eval hard limit; the
    others complete; its status is eval_timeout."""
    entry = lambda i: GoldenEntry(id=f"g-{i}", category="dtc", question_type="lookup",  # noqa: E731
                                  difficulty="easy", question=f"q{i}", golden_summary="r")

    async def ok() -> SystemRunResult:
        return SystemRunResult(system_label="manual_agent", question="q", output_text="a")

    async def hang() -> SystemRunResult:
        await asyncio.sleep(3600)
        raise AssertionError

    items = [WorkItem("manual_agent", entry(0), ok), WorkItem("manual_agent", entry(1), hang),
             WorkItem("manual_agent", entry(2), ok)]
    res = await execute(items, run_concurrency=3, grade_fn=grade_stub(), item_timeout_s=0.2)
    assert res[("manual_agent", "g-1")].status == STATUS_EVAL_TIMEOUT
    assert res[("manual_agent", "g-0")].status == STATUS_OK and res[("manual_agent", "g-2")].grade is not None


def test_item_limit_allows_the_one_shot_nudge() -> None:
    """FM-48: 2 × sub-agent wall clock + 60 s (the nudge is a second drive)."""
    assert item_timeout_s(Budgets(subagent_wall_clock_s=180.0)) == 420.0


async def test_the_watchdog_writes_an_invalid_scorecard_instead_of_hanging(tmp_path: pathlib.Path) -> None:
    """T-7: the whole-run limit fires → scorecard written, invalid, exit 6."""
    path, card = await _run(tmp_path, scripted_model(hang_on="MWS-150-A"), max_total_s=1.0)
    assert path.is_file() and card["meta"]["valid"] is False
    assert any(r.startswith("watchdog") for r in card["meta"]["invalid_reasons"])
    assert exit_code(card) == 6


async def test_concurrent_goldens_do_not_share_state(tmp_path: pathlib.Path) -> None:
    """T-7 / FM-50: two goldens run at the same time read different sections;
    each result holds only its own read and citation."""
    ctx = LaneContext(model=scripted_model(delay=0.05), manuals=[_yamaha_manual()], manual_root=FIXTURES,
                      fixture_dir=V3 / "evals" / "fixtures", budgets_factory=_budgets)
    mk = lambda gid, q: GoldenEntry(id=gid, category="dtc", question_type="lookup", difficulty="easy",  # noqa: E731
                                    question=q, golden_summary="r")
    (a, sa), (b, sb) = await asyncio.gather(run_manual_item(mk("a", "How do I test the battery?"), ctx),
                                            run_manual_item(mk("b", "Fuel pump does not run"), ctx))
    assert a.read_slugs != b.read_slugs and len(a.read_slugs) == len(b.read_slugs) == 1
    assert "battery" in a.read_slugs[0] and "fuel-pump" in b.read_slugs[0]
    assert isinstance(sa, ItemStats) and sa.requests == 2 and sb.requests == 2


async def test_judge_failure_is_retried_after_a_delay_then_recovers(tmp_path: pathlib.Path) -> None:
    """T-5 / FM-22: the first grading returns the judge-failure fallback →
    one delayed retry → graded normally, scorecard valid."""
    g = grade_stub(fail_times=1)
    _, card = await _run(tmp_path, scripted_model(), ids=MAN_IDS[:1], lanes=(LANE_MANUAL,), grade_fn=g)
    rec = card["records"][0]
    assert rec["item"]["judge_attempts"] == 2 and rec["item"]["status"] == STATUS_OK and card["meta"]["valid"]


def test_persistent_judge_failure_invalidates_then_regrade_fixes(tmp_path: pathlib.Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """T-5 / FM-19 / FM-44: still failing after the retry → judge_failed,
    scorecard invalid, summary names it; ``regrade`` re-judges only that
    golden from the stored output (no agent run) → valid."""
    path, card = asyncio.run(_run(tmp_path, scripted_model(), ids=MAN_IDS[:2], lanes=(LANE_MANUAL,),
                                  grade_fn=grade_stub(fail_times=99)))
    assert card["meta"]["valid"] is False
    assert all(r["item"]["status"] == STATUS_JUDGE_FAILED for r in card["records"])
    assert "判卷失败" in summarize([card])

    import stf_v3.evals.judge as evjudge

    calls = {"n": 0}

    async def good(entry: GoldenEntry, run: SystemRunResult, client: Any = None) -> Grade:
        calls["n"] += 1
        return await grade_stub(0.88)(entry, run)

    monkeypatch.setattr(evjudge, "grade_run", good)
    assert evcli.main(["regrade", "--scorecard", str(path)]) == 0
    fixed = sc.load(path.with_name(path.stem + ".regraded.json"))
    assert calls["n"] == 2 and fixed["meta"]["valid"] is True
    assert all(r["item"]["status"] == "ok" and r["item"]["regraded"] for r in fixed["records"])


async def test_manual_id_insurance_maps_a_renamed_library_manual(tmp_path: pathlib.Path) -> None:
    """PROD-08 FM-56: golden ids not in the library → mapped to the single
    manual matching the corpus vehicle; recorded in the config."""
    _, card = await _run(tmp_path, scripted_model(), ids=MAN_IDS[:1], lanes=(LANE_MANUAL,),
                         manuals=[_yamaha_manual("new-id")])
    assert card["meta"]["config"]["manual_id_map"] == {GOLDEN_MANUAL_ID: "new-id"}


def test_unknown_ids_are_refused(tmp_path: pathlib.Path) -> None:
    """Asking for a golden that does not exist refuses instead of silently
    running fewer goldens."""
    with pytest.raises(RunRefused, match="not found"):
        asyncio.run(_run(tmp_path, scripted_model(), ids=["procedural-001"], lanes=(LANE_MANUAL,)))


def test_manual_id_insurance_refuses_when_ambiguous() -> None:
    """Zero or several matching manuals → the run refuses (loudly)."""
    from stf_v3.evals import data as evdata

    entries = evdata.load_golden(pathlib.Path(DATA), LANE_MANUAL)
    with pytest.raises(RunRefused, match="not in the V3 library"):
        map_manual_ids(entries, [small_manual("honda")])
    with pytest.raises(RunRefused, match="2 library manuals match"):
        map_manual_ids(entries, [_yamaha_manual("x1"), _yamaha_manual("x2")])


async def test_summary_and_slim_and_id_selection(tmp_path: pathlib.Path) -> None:
    """T-10 / FM-10 / FM-30 / FM-51: the summary shows per-lane means, the
    per-golden delta against a reference, budget stops and the image goldens;
    the slim copy drops cited section text and is still gate-readable."""
    img_id = "image-001"
    path, card = await _run(tmp_path, scripted_model(), ids=MAN_IDS + [img_id] + OBD_IDS)
    ref = json.loads(json.dumps(card))
    for r in ref["records"]:
        r["grade"]["overall"] = 0.8
    md = summarize([card], {LANE_MANUAL: ref, LANE_OBD: ref})
    for needle in ("# Golden 评测摘要", "手册 lane", "OBD lane", "按题型", "按维度", "被预算截断", "依赖图的题",
                   "逐题", "+0.100"):
        assert needle in md, needle
    lite = sc.load(path.with_name(path.stem + ".slim.json"))
    assert lite["meta"]["slim"] is True
    assert all("--- Cited sections" not in r["result"]["output_text"] for r in lite["records"])
    assert path.with_name(path.stem + ".slim.json").stat().st_size < path.stat().st_size
    from stf_v3.evals import gate

    assert gate.lane_means(lite) == gate.lane_means(card)
    assert len(card["records"]) == 6


def test_secret_scrub_patterns_and_env_values() -> None:
    """FM-33: env values and key-shaped strings are replaced; the count kept."""
    env = {"STF_V3_OPENROUTER_API_KEY": FAKE_KEY, "STF_V3_DATABASE_URL": "postgresql+psycopg://u:hunter22@h/db",
           "STF_V3_LLM_API_KEY": "none", "PATH": "/usr/bin"}
    secrets = sc.secret_values(env)
    assert FAKE_KEY in secrets and "hunter22" in secrets and "none" not in secrets
    obj, n = sc.scrub({"a": f"x {FAKE_KEY} y", "b": ["Bearer abcdefghijklmnopqrstu", "db hunter22"]}, secrets)
    assert FAKE_KEY not in json.dumps(obj) and "hunter22" not in json.dumps(obj) and n >= 3

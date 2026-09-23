"""``python -m stf_v3.evals`` — the golden eval command line (PROD-10).

Sub-commands::

    run       --purpose baseline|gate|calibration|comparison|demo|adhoc
              [--lanes manual,obd] [--thinking on] [--cloud] [--budget-scale 2]
              [--concurrency 6] [--ids a,b] [--out /out] [--judge-model m]
    regrade   --scorecard x.json [--all] [--out dir]   re-judge stored outputs only
    summary   --scorecards a.json [b.json] [--reference manual_agent=v2.json …] [--out s.md]
    calibrate --scorecard x.json                       sub-agent budget proposal
    accept    --scorecards a.json b.json --lines manual_agent=0.831,obd_agent=0.938
              [--write-thresholds stf_v3/evals/thresholds.yaml]

Exit codes: 0 ok · 1 gate / acceptance failed · 2 finished but invalid ·
4 preflight refused · 6 watchdog.

Normally started by ``scripts/run_golden_eval.sh`` in a one-off container
(same image as the API, manual library mounted read-only, output on the
host).  Never reads or writes ``STF_V3_LLM_ALLOW_CLOUD``: the cloud
comparison goes through the model adapter's own cloud path (FM-34).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

from stf_v3.evals import gate as evgate
from stf_v3.evals import scorecard as sc

TOKENIZER = "cl100k_base"
JUDGE_MAX_TOKENS = 8192
"""The copied judge caps its reply at 2048 tokens (V2).  glm-5.1 on
OpenRouter now spends hidden reasoning tokens first; on long Chinese
goldens that exhausts 2048 and the reply comes back empty (finish=length,
PROD-10 T-17: image-005 needed 3448).  The cap only decides whether the
judge finishes — grades that fitted in 2048 are unchanged — so V3 raises
it here and records it in every scorecard, keeping judge.py verbatim."""


def configure_judge(judge_model: Optional[str] = None) -> Dict[str, Any]:
    """Apply the V3 judge settings on the copied module; returns them."""
    from stf_v3.evals import judge as evjudge

    evjudge._JUDGE_MAX_TOKENS = JUDGE_MAX_TOKENS
    if judge_model:
        evjudge._JUDGE_MODEL = judge_model   # V2's module-constant override pattern
    return {"judge_model": evjudge._JUDGE_MODEL, "judge_temperature": evjudge._JUDGE_TEMPERATURE,
            "judge_max_tokens": evjudge._JUDGE_MAX_TOKENS}


def _parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m stf_v3.evals", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--purpose", required=True, choices=sc.PURPOSES)
    r.add_argument("--lanes", default="manual,obd")
    r.add_argument("--thinking", choices=("off", "on"), default="off")
    r.add_argument("--cloud", action="store_true")
    r.add_argument("--budget-scale", type=float, default=1.0)
    r.add_argument("--concurrency", type=int, default=6)
    r.add_argument("--judge-concurrency", type=int, default=4)
    r.add_argument("--ids", default="")
    r.add_argument("--out", default="/out")
    r.add_argument("--data-dir", default=None)
    r.add_argument("--judge-model", default=None, help="override (scorecard then never counts for the gate)")
    r.add_argument("--max-total-s", type=float, default=None)
    r.add_argument("--model-wait-s", type=float, default=60.0)
    g = sub.add_parser("regrade")
    g.add_argument("--scorecard", required=True)
    g.add_argument("--all", action="store_true", help="re-judge every record (default: judge failures only)")
    g.add_argument("--out", default=None)
    g.add_argument("--judge-concurrency", type=int, default=4)
    s = sub.add_parser("summary")
    s.add_argument("--scorecards", nargs="+", required=True)
    s.add_argument("--reference", action="append", default=[], help="lane=path of a V2 reference scorecard")
    s.add_argument("--out", default=None)
    c = sub.add_parser("calibrate")
    c.add_argument("--scorecard", required=True)
    c.add_argument("--margin", type=float, default=1.5)
    a = sub.add_parser("accept")
    a.add_argument("--scorecards", nargs="+", required=True)
    a.add_argument("--lines", required=True)
    a.add_argument("--write-thresholds", default=None)
    return p.parse_args(argv)


# ── preflight helpers (the run command) ───────────────────────────

def check_tokenizer() -> str:
    """The scorer's token counter must be the real cl100k_base (FM-24 / FM-43)."""
    import tiktoken

    enc = tiktoken.get_encoding(TOKENIZER)
    if len(enc.encode("測試 test")) <= 0:
        raise RuntimeError("tokenizer returned no tokens")
    return TOKENIZER


def check_model(base_url: str, api_key: str, model: str, *, local: bool, wait_s: float = 60.0,
                transport: Optional[httpx.BaseTransport] = None) -> Dict[str, Any]:
    """The endpoint serves ``model`` (and, locally, generates + reports its version)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    deadline = time.monotonic() + wait_s
    last = "not checked"
    info: Dict[str, Any] = {}
    with httpx.Client(timeout=30.0, headers=headers, transport=transport) as client:
        while True:
            try:
                r = client.get(base_url.rstrip("/") + "/models")
                r.raise_for_status()
                ids = [m.get("id") for m in r.json().get("data", [])]
                if model in ids:
                    break
                last = f"model {model!r} not served (served: {ids[:10]})"
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}"
            if time.monotonic() >= deadline:
                raise RuntimeError(f"model endpoint check failed: {last}")
            time.sleep(5.0)
        if local:
            r = client.post(base_url.rstrip("/") + "/chat/completions", json={
                "model": model, "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "max_tokens": 16}, timeout=120.0)
            r.raise_for_status()
            try:
                root = base_url.rstrip("/").rsplit("/v1", 1)[0]
                info["vllm_version"] = client.get(root + "/version").json().get("version")
            except (httpx.HTTPError, ValueError):
                info["vllm_version"] = None
    return info


async def check_judge(client: Any, judge_model: str) -> None:
    """One tiny judge call so a missing key / blocked model fails before the run (FM-25)."""
    completion = await client.chat.completions.create(
        model=judge_model, messages=[{"role": "user", "content": 'Return {"ok": true} as JSON.'}],
        temperature=0.0, max_tokens=2048)   # the judge reasons before answering
    if not completion.choices or not completion.choices[0].message.content:
        raise RuntimeError("judge returned no content")


def judge_credit(api_key: str, base_url: str) -> Optional[Dict[str, Any]]:
    """OpenRouter key usage / remaining limit (never prints the key)."""
    try:
        r = httpx.get(base_url.rstrip("/") + "/key", headers={"Authorization": f"Bearer {api_key}"}, timeout=15.0)
        d = r.json().get("data", {})
        return {"usage": d.get("usage"), "limit": d.get("limit"), "limit_remaining": d.get("limit_remaining")}
    except (httpx.HTTPError, ValueError):
        return None


def _pydantic_ai_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("pydantic-ai-slim")
    except Exception:  # noqa: BLE001
        return None


async def _load_manuals() -> List[Any]:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.bootstrap import load_manual_inventory

    async with SessionLocal() as session:
        return list(await load_manual_inventory(session))


def cmd_run(args: argparse.Namespace) -> int:
    from stf_v3.diagnosis.agent.deps import Budgets
    from stf_v3.diagnosis.agent.model import ModelConfigError, build_model, source_label, source_of
    from stf_v3.evals import data as evdata
    from stf_v3.evals import judge as evjudge
    from stf_v3.evals import metrics as evmetrics
    from stf_v3.evals.runner import LANE_ALIASES, RunOptions, RunRefused, apply_overrides, exit_code, run_eval
    from stf_v3.settings import settings

    def refuse(msg: str) -> int:
        print(f"[preflight] REFUSED: {msg}", file=sys.stderr, flush=True)
        return 4

    lanes = [LANE_ALIASES.get(x.strip(), x.strip()) for x in args.lanes.split(",") if x.strip()]
    if any(lane not in ("manual_agent", "obd_agent") for lane in lanes):
        return refuse(f"unknown lane in {args.lanes!r}")
    if args.cloud and args.thinking == "on":
        return refuse("--thinking on applies to the local vLLM profile only")
    opts = RunOptions(purpose=args.purpose, lanes=lanes, thinking=args.thinking == "on", cloud=args.cloud,
                      budget_scale=args.budget_scale, concurrency=args.concurrency,
                      judge_concurrency=args.judge_concurrency,
                      ids=[i for i in args.ids.split(",") if i] or None, out_dir=Path(args.out),
                      data_dir=args.data_dir, max_total_s=args.max_total_s)
    try:
        tokenizer = check_tokenizer()
    except Exception as exc:  # noqa: BLE001
        return refuse(f"tokenizer {TOKENIZER} unavailable ({type(exc).__name__}); the scorer would fall back")
    profile = apply_overrides(settings, opts)
    try:
        model = build_model(settings, cloud=opts.cloud)
    except ModelConfigError as exc:
        return refuse(str(exc))
    src = source_of(model)
    base_url, key, name = ((settings.cloud_llm_base_url, settings.cloud_api_key, settings.cloud_llm_model)
                           if opts.cloud else (settings.llm_base_url, settings.llm_api_key, settings.llm_model))
    try:
        info = check_model(base_url, key, name, local=not opts.cloud, wait_s=args.model_wait_s)
    except Exception as exc:  # noqa: BLE001
        return refuse(str(exc))
    judge_cfg = configure_judge(args.judge_model)
    try:
        judge_client = evjudge._get_default_client()
        asyncio.run(check_judge(judge_client, evjudge._JUDGE_MODEL))
    except Exception as exc:  # noqa: BLE001
        return refuse(f"judge unavailable: {type(exc).__name__}: {str(exc)[:160]}")
    evjudge._cached_client = None   # the client was bound to the preflight loop
    credit = judge_credit(settings.cloud_api_key, settings.cloud_llm_base_url)
    try:
        manuals = asyncio.run(_load_manuals())
    except Exception as exc:  # noqa: BLE001
        return refuse(f"manual inventory: {type(exc).__name__}")
    manual_root = Path(settings.manual_storage_path)

    def budgets_factory() -> Budgets:
        return Budgets.from_settings(settings, profile)

    b = budgets_factory()
    config = {
        "model": name, "source": source_label(model), "profile": profile,
        "endpoint_host": src.host if src else None, "thinking": opts.thinking, "budget_scale": opts.budget_scale,
        "budgets": {"subagent_wall_clock_s": b.subagent_wall_clock_s,
                    "subagent_request_limit": b.subagent_request_limit,
                    "subagent_max_tokens": settings.subagent_max_tokens,
                    "subagent_temperature": b.subagent_temperature,
                    "tool_result_max_tokens": b.tool_result_max_tokens},
        "images_enabled": False, **judge_cfg, "tokenizer": tokenizer,
        "manual_sha256": evdata.manual_hashes(manual_root, [m.id for m in manuals]),
        "pydantic_ai": _pydantic_ai_version(), "vllm_version": info.get("vllm_version"),
        "judge_credit": credit,
    }
    print(f"[preflight] ok: model={name} source={source_label(model)} profile={profile} thinking={opts.thinking} "
          f"budget_scale={opts.budget_scale} sub-agent wall={b.subagent_wall_clock_s:.0f}s "
          f"requests={b.subagent_request_limit} judge={evjudge._JUDGE_MODEL} tokenizer={tokenizer} "
          f"manuals={len(manuals)} vllm={info.get('vllm_version')} judge_credit={credit}", flush=True)
    try:
        path, card = asyncio.run(run_eval(
            opts, model=model, manuals=manuals, manual_root=manual_root, budgets_factory=budgets_factory,
            config=config, git_commit=settings.git_commit, secrets=sc.secret_values()))
    except RunRefused as exc:
        return refuse(str(exc))
    if evmetrics._ENC is False:   # the scorer silently fell back mid-run (FM-43)
        card["meta"].setdefault("extra_invalid_reasons", []).append("tokenizer fell back to len/4")
        card["meta"]["config"]["tokenizer"] = "len/4"
        sc.write_final(path.parent, card["meta"]["base"], card, sc.secret_values())
    from stf_v3.evals.summary import summarize

    md = summarize([card])
    path.with_suffix(".md").write_text(md, encoding="utf-8")
    meta = card["meta"]
    print(f"\n== {meta['completed']}/{meta['expected']} graded, valid={meta['valid']} "
          f"means={ {k: round(v, 3) for k, v in evgate.lane_means(card).items()} }", flush=True)
    for reason in meta.get("invalid_reasons", []):
        print(f"== invalid: {reason}", flush=True)
    print(f"== files: {path} (+ .slim.json, .md, .progress.jsonl)", flush=True)
    return exit_code(card)


# ── regrade / summary / calibrate / accept ────────────────────────

def cmd_regrade(args: argparse.Namespace) -> int:
    from stf_v3.evals.judge import grade_run
    from stf_v3.evals.orchestrator import is_judge_failure
    from stf_v3.evals.schemas import GoldenEntry, SystemRunResult

    judge_cfg = configure_judge()
    card = sc.load(Path(args.scorecard))
    records = card.get("records", [])
    targets = [i for i, r in enumerate(records)
               if args.all or (r.get("item") or {}).get("status") == "judge_failed"]
    old = json.loads(json.dumps(records))

    async def _go() -> None:
        sema = asyncio.Semaphore(args.judge_concurrency)

        async def one(i: int) -> None:
            r = records[i]
            entry = GoldenEntry.model_validate(r["entry"])
            run = SystemRunResult.model_validate(r["result"])
            async with sema:
                g = await grade_run(entry, run)
                for delay in (10.0, 30.0):
                    if not is_judge_failure(g):
                        break
                    await asyncio.sleep(delay)
                    g = await grade_run(entry, run)
            r["grade"] = g.model_dump()
            item = r.setdefault("item", {})
            item["status"] = "judge_failed" if is_judge_failure(g) else "ok"
            item["regraded"] = True

        await asyncio.gather(*(one(i) for i in targets))

    asyncio.run(_go())
    det = ("section_recall", "claim_precision", "exploration_cost", "fact_recall", "fact_density",
           "citation_quality", "trajectory_efficiency", "value_accuracy")
    diffs = {d: max((abs((records[i]["grade"].get(d) or 0) - (old[i]["grade"].get(d) or 0)) for i in targets),
                    default=0.0) for d in det}
    aq_old = [old[i]["grade"]["answer_quality"] for i in targets]
    aq_new = [records[i]["grade"]["answer_quality"] for i in targets]
    ov_old = [old[i]["grade"]["overall"] for i in targets]
    ov_new = [records[i]["grade"]["overall"] for i in targets]
    report = {"regraded": len(targets), "judge": judge_cfg, "deterministic_max_abs_diff": diffs,
              "answer_quality_mean": {"stored": _m(aq_old), "now": _m(aq_new)},
              "overall_mean": {"stored": _m(ov_old), "now": _m(ov_new)}}
    card.setdefault("meta", {}).setdefault("regrades", []).append({"at": time.time(), **report})
    if "schema" in card.get("meta", {}):
        sc.finalize(card)
    src = Path(args.scorecard)
    out_dir = Path(args.out) if args.out else src.parent
    out = out_dir / (src.name.replace(".json", "") + ".regraded.json")
    out.write_text(sc.dumps(card, sc.secret_values()), encoding="utf-8")
    print(json.dumps(report, indent=1))
    print(f"== written: {out}")
    return 0


def _m(xs: Sequence[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 4) if xs else None


def cmd_summary(args: argparse.Namespace) -> int:
    from stf_v3.evals.summary import summarize

    cards = []
    for p in args.scorecards:
        c = sc.load(Path(p))
        c["_path"] = Path(p).name
        cards.append(c)
    refs = {}
    for spec in args.reference:
        lane, _, path = spec.partition("=")
        refs[lane] = sc.load(Path(path))
    md = summarize(cards, refs)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    else:
        print(md)
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    out = evgate.calibrate(sc.load(Path(args.scorecard)), margin=args.margin)
    print(json.dumps(out, indent=1))
    return 0 if out.get("proposal") else 1


def cmd_accept(args: argparse.Namespace) -> int:
    import yaml

    cards = []
    for p in args.scorecards:
        c = sc.load(Path(p))
        c["_path"] = Path(p).name
        ok, why = evgate.eligibility(c)
        if not ok:
            print(f"== {p} cannot be a baseline: {'; '.join(why)}")
            return 1
        cards.append(c)
    lines = {}
    for spec in args.lines.split(","):
        lane, _, val = spec.partition("=")
        lines[lane.strip()] = float(val)
    ok, report = evgate.acceptance(cards, lines)
    for line in report:
        print("== " + line)
    if not ok:
        return 1
    if args.write_thresholds:
        lanes = evgate.build_baseline(cards)
        for lane, line in lines.items():
            lanes.setdefault(lane, {})["acceptance_line"] = line
        cfg = cards[0]["meta"]["config"]
        doc = {
            "description": ("PROD-10 golden gate. Baseline = mean of the two runs below. Changing this file is a "
                            "baseline-reset PR (label baseline-reset, approved by the user); it may not change "
                            "any other managed path in the same PR."),
            "tolerance": 0.03, "floor": 0.4, "floor_min_baseline": 0.6, "max_runs_considered": 2,
            "judge_model": cfg.get("judge_model"),
            "exempt_label": "eval-exempt", "reset_label": "baseline-reset",
            "thresholds_path": "stf_v3/evals/thresholds.yaml",
            "baseline_scorecards": [c["_path"] for c in cards],
            "baseline_config": {k: cfg.get(k) for k in evgate.UNMANAGED_CONFIG_KEYS},
            "managed_paths": evgate.DEFAULT_MANAGED_PATHS,
            "lanes": lanes,
        }
        Path(args.write_thresholds).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                                               encoding="utf-8")
        print(f"== thresholds written: {args.write_thresholds}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse(argv)
    return {"run": cmd_run, "regrade": cmd_regrade, "summary": cmd_summary,
            "calibrate": cmd_calibrate, "accept": cmd_accept}[args.cmd](args)


__all__ = ["JUDGE_MAX_TOKENS", "TOKENIZER", "configure_judge", "check_judge", "check_model", "check_tokenizer", "main"]

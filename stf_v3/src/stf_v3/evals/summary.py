"""Human-readable Markdown summary of one or more scorecards (PROD-10).

What a reviewer needs to judge a run without opening the JSON (FM-10 /
FM-30): validity, per-lane means per run, per question type, per scoring
dimension, timings and budget stops, the image-dependent goldens on
their own (the manual-images return condition), failures, and a
per-golden table with the delta against a V2 reference scorecard.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence

from stf_v3.evals.gate import lane_scores, p95

DIMS = ("section_recall", "claim_precision", "exploration_cost", "fact_recall", "fact_density",
        "hallucination_penalty", "citation_quality", "answer_quality", "trajectory_efficiency",
        "value_accuracy", "overall")
LANE_TITLE = {"manual_agent": "手册 lane（30）", "obd_agent": "OBD lane（15）"}


def _mean(xs: Sequence[float]) -> float:
    return statistics.mean(xs) if xs else float("nan")


def _fmt(x: Optional[float], nd: int = 3) -> str:
    if x is None or x != x:  # NaN
        return "—"
    return f"{x:.{nd}f}"


def _records(card: Dict[str, Any], lane: str) -> List[Dict[str, Any]]:
    return [r for r in card.get("records", []) if r["result"]["system_label"] == lane]


def _name(card: Dict[str, Any], i: int) -> str:
    return card.get("_path") or card.get("meta", {}).get("stamp") or f"run {i + 1}"


def summarize(cards: Sequence[Dict[str, Any]], references: Optional[Dict[str, Dict[str, Any]]] = None,
              title: str = "Golden 评测摘要") -> str:
    """Markdown for ``cards`` (same configuration, several runs)."""
    references = references or {}
    out: List[str] = [f"# {title}", ""]
    first = cards[0]
    meta = first.get("meta", {})
    mode = meta.get("mode", {})
    cfg = meta.get("config", {})
    out += [
        f"- 提交：`{str(meta.get('git_commit', '?'))[:12]}`；模式：{mode.get('backend')} / 思考 "
        f"{mode.get('thinking')} / 用途 {mode.get('purpose')} / 预算倍数 {mode.get('budget_scale', 1.0)}",
        f"- 模型：{cfg.get('model')}（{cfg.get('source')}）；判卷：{cfg.get('judge_model')}；分词：{cfg.get('tokenizer')}",
        f"- 子代理预算：{cfg.get('budgets')}",
        f"- 并发：{meta.get('pipeline', {}).get('run_concurrency')} 路；单题硬上限 "
        f"{meta.get('pipeline', {}).get('item_timeout_s')} 秒",
    ]
    for i, c in enumerate(cards):
        m = c.get("meta", {})
        status = "有效" if m.get("valid") else "无效：" + "；".join(m.get("invalid_reasons", []))
        out.append(f"- 第 {i + 1} 遍 `{_name(c, i)}`：{m.get('completed')}/{m.get('expected')} 题，"
                   f"{status}，脱敏 {m.get('secrets_redacted', 0)} 处")
    out.append("")

    lanes = [lane for lane in ("manual_agent", "obd_agent") if any(_records(c, lane) for c in cards)]
    out += ["## 总分", "", "| lane | " + " | ".join(f"第 {i + 1} 遍" for i in range(len(cards)))
            + " | 均值 | V2 参考 |", "|---|" + "---|" * (len(cards) + 2)]
    for lane in lanes:
        means = [_mean([r["grade"]["overall"] for r in _records(c, lane)]) for c in cards]
        ref = references.get(lane)
        ref_mean = _mean([r["grade"]["overall"] for r in ref.get("records", [])
                          if r["result"]["system_label"] == lane]) if ref else None
        out.append(f"| {LANE_TITLE.get(lane, lane)} | " + " | ".join(_fmt(m) for m in means)
                   + f" | {_fmt(_mean(means))} | {_fmt(ref_mean)} |")
    out.append("")

    for lane in lanes:
        recs = [r for c in cards for r in _records(c, lane)]
        out += [f"## {LANE_TITLE.get(lane, lane)}", "", "**按题型**", "", "| 题型 | 题数 | 均分 |", "|---|---|---|"]
        by_type: Dict[str, List[float]] = {}
        for r in recs:
            by_type.setdefault(r["entry"].get("question_type", "?"), []).append(r["grade"]["overall"])
        for qt, xs in sorted(by_type.items()):
            out.append(f"| {qt} | {len(xs) // max(1, len(cards))} | {_fmt(_mean(xs))} |")
        out += ["", "**按维度（所有遍的均值）**", "", "| 维度 | 均值 |", "|---|---|"]
        for d in DIMS:
            vals = [r["grade"].get(d) for r in recs if r["grade"].get(d) is not None]
            if vals:
                out.append(f"| {d} | {_fmt(_mean(vals))} |")
        stats = [r.get("item", {}) for r in recs]
        walls = [float(s.get("wall_s", r["result"].get("latency_ms_wall", 0) / 1000)) for s, r in zip(stats, recs)]
        reqs = [float(s.get("requests", r["result"].get("iterations", 0))) for s, r in zip(stats, recs)]
        toks = [float(s.get("total_tokens", 0)) for s in stats]
        stops: Dict[str, int] = {}
        for s, r in zip(stats, recs):
            key = s.get("stopped_reason") or r["result"].get("stopped_reason", "?")
            stops[key] = stops.get(key, 0) + 1
        out += ["", "**耗时与预算**", "",
                f"- 单题耗时：中位 {_fmt(statistics.median(walls) if walls else None, 1)} 秒，"
                f"95 分位 {_fmt(p95(walls), 1)} 秒，最长 {_fmt(max(walls, default=None), 1)} 秒",
                f"- 请求数 95 分位 {_fmt(p95(reqs), 0)}，token 95 分位 {_fmt(p95(toks), 0)}",
                f"- 收尾原因：{', '.join(f'{k} {v}' for k, v in sorted(stops.items()))}；"
                f"被预算截断（非 complete）{sum(v for k, v in stops.items() if k != 'complete')} 题；"
                f"触发补问 {sum(1 for s in stats if s.get('nudged'))} 题；"
                f"思考字数合计 {sum(int(s.get('thinking_chars', 0)) for s in stats)}", ""]
        if lane == "manual_agent":
            imgs = [r for r in _records(first, lane) if r["entry"].get("requires_image")]
            if imgs:
                out += ["**依赖图的题（手册图片回头条件用）**", "", "| 题 | 得分 |", "|---|---|"]
                for r in imgs:
                    out.append(f"| {r['entry']['id'][-22:]} | {_fmt(r['grade']['overall'])} |")
                out += [f"| 均值 | {_fmt(_mean([r['grade']['overall'] for r in imgs]))} |", ""]

        per_run = [lane_scores(c).get(lane, {}) for c in cards]
        ref_scores = lane_scores(references[lane]).get(lane, {}) if lane in references else {}
        ids = sorted(set().union(*per_run)) if per_run else []
        qtypes = {r["entry"]["id"]: r["entry"].get("question_type", "") for r in recs}
        out += ["**逐题**", "", "| 题 | 题型 | " + " | ".join(f"第 {i + 1} 遍" for i in range(len(cards)))
                + " | V2 参考 | 差值 |", "|---|---|" + "---|" * (len(cards) + 2)]
        for gid in ids:
            vals = [r.get(gid) for r in per_run]
            present = [v for v in vals if v is not None]
            ref = ref_scores.get(gid)
            delta = (_mean(present) - ref) if (present and ref is not None) else None
            out.append(f"| {gid[-26:]} | {qtypes.get(gid, '')} | " + " | ".join(_fmt(v) for v in vals)
                       + f" | {_fmt(ref)} | {('+' if delta and delta > 0 else '') + _fmt(delta)} |")
        out.append("")

    fails = [(i, f) for i, c in enumerate(cards) for f in c.get("meta", {}).get("failed_items", [])]
    jf = [(i, r["entry"]["id"]) for i, c in enumerate(cards) for r in c.get("records", [])
          if (r.get("item") or {}).get("status") == "judge_failed"]
    if fails or jf:
        out += ["## 失败的题", ""]
        for i, f in fails:
            out.append(f"- 第 {i + 1} 遍 {f.get('lane')}:{f.get('id')} {f.get('status')} — {f.get('error', '')[:160]}")
        for i, gid in jf:
            out.append(f"- 第 {i + 1} 遍 {gid} 判卷失败（成绩单无效，用 regrade 只重判这些题）")
        out.append("")
    return "\n".join(out)


__all__ = ["summarize"]

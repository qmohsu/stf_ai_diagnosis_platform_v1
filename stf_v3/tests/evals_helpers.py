"""Synthetic scorecards for the PROD-10 gate / scorecard tests.

Author: Xiangzhu Yan
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from stf_v3.evals import scorecard as sc

JUDGE = "z-ai/glm-5.1"


def record(lane: str, gid: str, overall: float, *, qtype: str = "lookup", status: str = "ok",
           stopped: str = "complete", wall: float = 60.0, requests: int = 5, tokens: int = 20_000,
           requires_image: bool = False) -> Dict[str, Any]:
    """One scorecard record with the V2 shape + the V3 ``item`` block."""
    return {
        "entry": {"id": gid, "category": "dtc", "question_type": qtype, "difficulty": "easy",
                  "question": f"q {gid}", "golden_summary": "ref", "requires_image": requires_image},
        "result": {"system_label": lane, "question": f"q {gid}", "output_text": f"answer {gid}",
                   "claim_slugs": [], "read_slugs": [], "latency_ms_wall": wall * 1000, "iterations": requests,
                   "stopped_reason": stopped, "tool_trace": [], "surfaced_images": []},
        "grade": {"section_recall": None, "claim_precision": None, "exploration_cost": 0.0, "fact_recall": 1.0,
                  "fact_density": 1.0, "hallucination_penalty": 1.0, "citation_quality": 1.0,
                  "answer_quality": overall, "trajectory_efficiency": None, "value_accuracy": None,
                  "overall": overall, "reasoning": "[judge failure] x" if status == "judge_failed" else "ok"},
        "item": {"status": status, "stopped_reason": stopped, "wall_s": wall, "requests": requests,
                 "total_tokens": tokens, "tool_calls": 3, "nudged": False, "thinking_chars": 0},
    }


def card(scores: Dict[str, Dict[str, float]], *, commit: str = "c" * 40, purpose: str = "gate",
         backend: str = "local", thinking: str = "off", budget_scale: float = 1.0, judge: str = JUDGE,
         stamp: str = "20260923T000000Z", failed: Optional[List[Dict[str, Any]]] = None,
         statuses: Optional[Dict[str, str]] = None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A finalized scorecard: ``scores`` = {lane: {golden id: overall}}."""
    statuses = statuses or {}
    recs = [record(lane, gid, s, status=statuses.get(gid, "ok"))
            for lane, items in scores.items() for gid, s in items.items()]
    c = {
        "started_at": 0.0, "ended_at": 1.0, "count": len(recs),
        "meta": {"schema": sc.SCHEMA, "stamp": stamp, "base": f"{stamp}_{commit[:8]}_{purpose}",
                 "git_commit": commit,
                 "mode": {"backend": backend, "thinking": thinking, "purpose": purpose, "budget_scale": budget_scale},
                 "lanes": list(scores), "ids": None,
                 "expected": len(recs) + len(failed or []), "failed_items": failed or [],
                 "config": {"judge_model": judge, "tokenizer": "cl100k_base", "model": "Qwen/Qwen3.6-27B-FP8",
                            "vllm_version": "0.24.0", **(config or {})},
                 "pipeline": {}, "secrets_redacted": 0, "slim": False},
        "records": recs,
    }
    return sc.finalize(c)


def uniform(lane: str, n: int, value: float, prefix: str = "g") -> Dict[str, Dict[str, float]]:
    return {lane: {f"{prefix}-{i:03d}": value for i in range(n)}}

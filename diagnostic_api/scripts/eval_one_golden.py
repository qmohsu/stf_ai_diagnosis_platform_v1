#!/usr/bin/env python3
"""Run the manual sub-agent + GLM judge against a single golden entry.

One-shot eval driver for human-authored golden candidates under
``tests/harness/evals/golden/v2/candidates/``.  Used during
HARNESS-15 (rebuild golden v2) to validate that an entry is
*interpretable* — i.e. the judge can score the agent's output
against the rubric — before authoring more entries.

Usage::

    python -m scripts.eval_one_golden \\
        tests/harness/evals/golden/v2/candidates/dtc-001.json

Prints:
  - The agent's summary, citations, raw_sections preview, and
    tool_trace.
  - The judge's structured Grade with all 5 rubric dimensions
    plus the weighted overall.

Exits 0 on success regardless of pass/fail (we want the score,
not a CI gate).  Returns 1 only on infrastructure failures (API
unreachable, JSON parse, etc.).

Requires ``PREMIUM_LLM_API_KEY`` for the judge and a reachable
Ollama at ``settings.llm_endpoint`` for the agent.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.harness.deps import OpenAILLMClient
from app.harness_agents.manual_agent import (
    ManualAgentConfig,
    ManualAgentDeps,
    create_manual_agent_registry,
)
from tests.harness.evals.judge import judge_result
from tests.harness.evals.runner import run_manual_agent
from tests.harness.evals.schemas import GoldenEntry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("eval_one_golden")


def _load_entry(path: Path) -> GoldenEntry:
    """Load a single GoldenEntry from JSON, JSON-array, or JSONL.

    Supported shapes:
      - Pretty-printed single object (``.json``).
      - JSON array ``[{...}, ...]`` — first element is taken.
      - JSONL — first line is taken.
    """
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        first = json.loads(raw)[0]
    elif raw.startswith("{"):
        first = json.loads(raw)
    else:
        first = json.loads(raw.split("\n")[0])
    return GoldenEntry.model_validate(first)


def _format_grade(grade) -> str:
    return (
        f"  section_match    : {grade.section_match}\n"
        f"  fact_recall      : {grade.fact_recall:.2f}\n"
        f"  hallucination    : {grade.hallucination}\n"
        f"  citation_present : {grade.citation_present}\n"
        f"  trajectory_ok    : {grade.trajectory_ok}\n"
        f"  overall          : {grade.overall:.3f}\n"
        f"  reasoning        : {grade.reasoning}"
    )


def _format_result(result) -> str:
    cit_lines = "\n".join(
        f"    [{i+1}] manual={c.manual_id[:12]}…  slug={c.slug}"
        for i, c in enumerate(result.citations or [])
    ) or "    (none)"
    raw_lines = "\n".join(
        f"    [{i+1}] slug={s.slug}  chars={len(s.text)}  "
        f"had_images={getattr(s, 'had_images', False)}"
        for i, s in enumerate(result.raw_sections or [])
    ) or "    (none)"
    trace_lines = "\n".join(
        f"    {i+1}. {t.name}({json.dumps(t.input, ensure_ascii=False)[:120]})"
        f"  ({t.latency_ms:.0f} ms"
        f"{', error' if getattr(t, 'is_error', False) else ''})"
        for i, t in enumerate(result.tool_trace or [])
    ) or "    (none)"
    summary = result.summary or "(empty)"
    if len(summary) > 1200:
        summary = summary[:1200] + " […]"
    return (
        f"  stopped_reason    : {result.stopped_reason}\n"
        f"  iterations        : {result.iterations}\n"
        f"  tool_trace ({len(result.tool_trace or [])}):\n{trace_lines}\n"
        f"  citations ({len(result.citations or [])}):\n{cit_lines}\n"
        f"  raw_sections ({len(result.raw_sections or [])}):\n"
        f"{raw_lines}\n"
        f"  summary ({len(result.summary or '')} chars):\n"
        f"  ─────\n{summary}\n  ─────"
    )


def _build_override_deps(args: argparse.Namespace) -> Optional[ManualAgentDeps]:
    """Build alternate ``ManualAgentDeps`` when CLI overrides are set.

    Used to point the agent at a non-default LLM endpoint or model
    (e.g. OpenRouter + ``deepseek/deepseek-v4-flash``) without
    touching production code.  Returns ``None`` when no overrides
    are passed, in which case the runner's process-cached default
    (Ollama + ``qwen3.5:27b-q8_0``) is used.
    """
    if not args.llm_base_url and not args.model:
        return None

    base_url = args.llm_base_url or f"{settings.llm_endpoint}/v1"
    api_key = (
        os.getenv(args.llm_api_key_env)
        if args.llm_api_key_env
        else "ollama"
    )
    if not api_key:
        raise RuntimeError(
            f"--llm-api-key-env={args.llm_api_key_env} is empty; "
            f"export the variable or omit the flag.",
        )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(args.timeout) if args.timeout else 300.0,
        default_headers={
            "HTTP-Referer": "https://stf-diagnosis.dev",
            "X-Title": "STF eval_one_golden",
        },
    )
    cfg_kwargs = {}
    if args.model:
        cfg_kwargs["model"] = args.model
    if args.timeout:
        cfg_kwargs["timeout_seconds"] = float(args.timeout)
    if args.max_iterations:
        cfg_kwargs["max_iterations"] = int(args.max_iterations)
    return ManualAgentDeps(
        llm_client=OpenAILLMClient(client),
        tool_registry=create_manual_agent_registry(),
        config=ManualAgentConfig(**cfg_kwargs),
    )


async def _amain(args: argparse.Namespace) -> int:
    if not settings.premium_llm_api_key:
        logger.error("PREMIUM_LLM_API_KEY is not set in environment")
        return 1

    # Optional judge-model override.  Monkeypatch the module
    # constant rather than changing the function signature — this
    # is an experimentation hook, not a permanent eval-suite knob.
    if args.judge_model:
        from tests.harness.evals import judge as _judge_mod
        _judge_mod._JUDGE_MODEL = args.judge_model
        logger.info("judge model overridden to %s", args.judge_model)

    entry = _load_entry(args.entry)
    deps = _build_override_deps(args)
    agent_label = (
        f"{deps.config.model} via {args.llm_base_url}"
        if deps else "qwen3.5:27b-q8_0 via Ollama (default)"
    )

    print("=" * 78)
    print(f"GOLDEN ENTRY: {entry.id}")
    print(f"  category   : {entry.category}")
    print(f"  difficulty : {entry.difficulty}")
    print(f"  question   : {entry.question}")
    print(f"  citations  : {len(entry.golden_citations)}")
    print(f"  must_contain    : {entry.must_contain}")
    print(f"  must_not_contain: {entry.must_not_contain}")
    print()
    print("─" * 78)
    print(f"RUNNING AGENT ({agent_label})…")
    print("─" * 78)

    result = await run_manual_agent(
        question=entry.question,
        obd_context=entry.obd_context,
        deps=deps,
    )
    print(_format_result(result))

    judge_label = args.judge_model or "z-ai/glm-5.1"
    print()
    print("─" * 78)
    print(f"INVOKING JUDGE ({judge_label} via OpenRouter)…")
    print("─" * 78)
    grade = await judge_result(entry, result)
    print(_format_grade(grade))
    print()
    print("=" * 78)
    print(
        f"FINAL: overall={grade.overall:.3f}  "
        f"({'PASS' if grade.overall >= 0.7 else 'FAIL'} at 0.7 threshold)"
    )
    print("=" * 78)
    return 0


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the manual agent + judge against one golden "
            "entry and print the score."
        ),
    )
    parser.add_argument(
        "entry", type=Path,
        help="Path to a JSON or JSONL file containing a "
             "GoldenEntry (first line if JSONL).",
    )
    parser.add_argument(
        "--model", default=None,
        help="Override the agent's model (e.g. "
             "'deepseek/deepseek-v4-flash').  When omitted the "
             "runner's default (qwen3.5:27b-q8_0 on Ollama) is "
             "used.",
    )
    parser.add_argument(
        "--llm-base-url", default=None,
        help="Override the agent's LLM endpoint (e.g. "
             "'https://openrouter.ai/api/v1').  Required when "
             "--model is a hosted-API id.",
    )
    parser.add_argument(
        "--llm-api-key-env", default="PREMIUM_LLM_API_KEY",
        help="Env var holding the API key for the override "
             "endpoint.  Default 'PREMIUM_LLM_API_KEY' matches "
             "the OpenRouter key already in the server .env.",
    )
    parser.add_argument(
        "--timeout", type=float, default=None,
        help="Per-iteration LLM timeout (seconds).  Default "
             "uses the agent's built-in 120s.",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Max ReAct iterations.  Default uses the agent's "
             "built-in 8.",
    )
    parser.add_argument(
        "--judge-model", default=None,
        help="Override the judge model (default: "
             "z-ai/glm-5.1).  Useful when the default judge "
             "fails on entries with large raw_sections — "
             "DeepSeek V4 Pro (1M context) is a good alt.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

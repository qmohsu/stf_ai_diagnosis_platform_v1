#!/usr/bin/env python3
"""Run agent + RAG against a single golden entry; print scores.

The eval driver for HARNESS-15 / Issue #74's comparative
benchmark.  Loads a single ``GoldenEntry`` from JSON or JSONL,
runs both the manual sub-agent and the RAG retriever against
the same question, grades each via deterministic metrics + the
``answer_quality`` judge, and prints a side-by-side report.

Default behaviour runs ``--system both``.  Pass ``--system
manual_agent`` or ``--system rag`` to run only one.

Usage::

    python -m scripts.eval_one_golden \\
        tests/harness/evals/golden/v2/candidates/dtc-001.json

    # Override agent model + endpoint (useful when local
    # Ollama is contended)
    python -m scripts.eval_one_golden /tmp/dtc-001.json \\
        --model qwen/qwen3.6-flash \\
        --llm-base-url https://openrouter.ai/api/v1 \\
        --judge-model deepseek/deepseek-v4-pro

    # RAG only — fastest, no LLM cost
    python -m scripts.eval_one_golden /tmp/dtc-001.json \\
        --system rag

Requires ``PREMIUM_LLM_API_KEY`` for the judge and a reachable
Ollama at ``settings.llm_endpoint`` for embeddings.

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
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from app.config import settings
from app.harness.deps import LLMResponse, OpenAILLMClient, ToolCallInfo
from app.harness_agents.manual_agent import (
    ManualAgentConfig,
    ManualAgentDeps,
    create_manual_agent_registry,
)
from tests.harness.evals.judge import grade_run
from tests.harness.evals.rag_runner import run_rag
from tests.harness.evals.runner import run_manual_agent_unified
from tests.harness.evals.schemas import (
    GoldenEntry, Grade, SystemRunResult,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("eval_one_golden")


# ── Loader ────────────────────────────────────────────────────────


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


# ── Agent override builder ────────────────────────────────────────


class _NoThinkOpenAILLMClient(OpenAILLMClient):
    """OpenAILLMClient that injects ``/no_think`` into system messages.

    Workaround for the Qwen3.5 thinking-token explosion when running
    against local Ollama: the OpenAI-compat endpoint at ``/v1/chat/
    completions`` ignores top-level ``think`` parameters, but the
    Qwen-recognised ``/no_think`` directive in the system prompt
    dramatically shortens the hidden reasoning channel — observed
    drop from ~91s to ~2.5s for a tool-call response.

    Lives in the eval driver (not in production code) because we
    only need it for offline evaluation; production agents either
    use OpenRouter or accept the latency.
    """

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Inject ``/no_think`` into the first system message and forward."""
        adjusted = _inject_no_think(messages)
        return await super().chat(
            messages=adjusted,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def _inject_no_think(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a copy of ``messages`` with ``/no_think`` in the system prompt.

    Modifies only the first ``system``-role message.  If no system
    message exists, prepends one containing only the directive.
    Existing system content is preserved with the directive
    appended on a new line.
    """
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            existing = m.get("content") or ""
            if "/no_think" in existing:
                return out
            m["content"] = f"{existing}\n\n/no_think".lstrip()
            return out
    # No system message — prepend one.
    return [{"role": "system", "content": "/no_think"}] + out


def _build_override_deps(
    args: argparse.Namespace,
) -> Optional[ManualAgentDeps]:
    """Build alternate ``ManualAgentDeps`` when CLI overrides set.

    Used to point the agent at a non-default LLM endpoint or
    model (e.g. OpenRouter + ``qwen/qwen3.6-flash``) without
    touching production code.  Returns ``None`` when no
    overrides are passed; the runner's process-cached default
    (Ollama + ``qwen3.5:27b-q8_0``) is then used.
    """
    if (
        not args.llm_base_url
        and not args.model
        and not args.no_think
    ):
        return None
    base_url = args.llm_base_url or f"{settings.llm_endpoint}/v1"
    # Default api_key for Ollama is the literal string "ollama"
    # (Ollama's OpenAI-compat ignores the value but the SDK
    # requires non-empty).  For OpenRouter the env var must be
    # set or we error early.
    if args.llm_api_key_env and os.getenv(args.llm_api_key_env):
        api_key = os.getenv(args.llm_api_key_env)
    elif args.llm_base_url and "ollama" not in (
        args.llm_base_url or ""
    ).lower() and "127.0.0.1" not in (args.llm_base_url or ""):
        raise RuntimeError(
            f"--llm-api-key-env={args.llm_api_key_env} is empty; "
            "export the variable or omit the flag.",
        )
    else:
        api_key = "ollama"
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
    if args.max_tokens:
        cfg_kwargs["max_tokens"] = int(args.max_tokens)
    llm_client = (
        _NoThinkOpenAILLMClient(client)
        if args.no_think else OpenAILLMClient(client)
    )
    return ManualAgentDeps(
        llm_client=llm_client,
        tool_registry=create_manual_agent_registry(),
        config=ManualAgentConfig(**cfg_kwargs),
    )


# ── Pretty-printers ───────────────────────────────────────────────


_RULE = "─" * 78
_DRULE = "═" * 78


def _format_run_summary(run: SystemRunResult) -> str:
    """Compact human-readable summary of one system run."""
    output = run.output_text or "(empty)"
    if len(output) > 1000:
        output = output[:1000] + " […]"
    lines = [
        f"  system           : {run.system_label}",
        f"  stopped_reason   : {run.stopped_reason}",
        f"  iterations       : {run.iterations}",
        f"  claim_slugs      : {run.claim_slugs or '(none)'}",
        f"  read_slugs       : {run.read_slugs or '(none)'}",
        f"  latency_ms_wall  : {run.latency_ms_wall:.0f}",
        f"  latency_ms_llm   : {run.latency_ms_llm:.0f}",
        f"  cost_usd         : ${run.cost_usd:.5f}",
    ]
    if run.tool_trace:
        lines.append(f"  tool_trace ({len(run.tool_trace)}):")
        for i, t in enumerate(run.tool_trace, 1):
            lines.append(
                f"    {i}. {t.name}({json.dumps(t.input, ensure_ascii=False)[:100]})"
                f"  ({t.latency_ms:.0f}ms"
                f"{', error' if getattr(t, 'is_error', False) else ''})",
            )
    if run.retrieved_chunk_metadata:
        lines.append(f"  retrieved_chunks ({len(run.retrieved_chunk_metadata)}):")
        for i, c in enumerate(run.retrieved_chunk_metadata, 1):
            flags = []
            if c.has_image:
                flags.append("image")
            if c.dtc_codes:
                flags.append(f"dtc={','.join(c.dtc_codes[:3])}")
            flag_str = f"  [{' / '.join(flags)}]" if flags else ""
            lines.append(
                f"    [{i}] score={c.score:.3f}  slug={c.slug or '(empty)'}"
                f"{flag_str}",
            )
    lines.append(f"  output_text ({len(run.output_text)} chars):")
    lines.append("  ─────")
    lines.append(output)
    lines.append("  ─────")
    return "\n".join(lines)


def _format_grade_table(
    label: str, grade: Grade,
) -> List[Tuple[str, str]]:
    """Return rows for a side-by-side grade table."""
    return [
        (f"{label} section_recall", f"{grade.section_recall:.3f}"),
        (f"{label} claim_precision", f"{grade.claim_precision:.3f}"),
        (f"{label} exploration_cost", f"{grade.exploration_cost:.3f}"),
        (f"{label} fact_recall", f"{grade.fact_recall:.3f}"),
        (f"{label} fact_density", f"{grade.fact_density:.3f}"),
        (f"{label} hallucination_penalty",
         f"{grade.hallucination_penalty:.3f}"),
        (f"{label} citation_quality", f"{grade.citation_quality:.3f}"),
        (f"{label} answer_quality", f"{grade.answer_quality:.3f}"),
        (f"{label} trajectory_efficiency",
         f"{grade.trajectory_efficiency:.3f}"),
        (f"{label} OVERALL", f"{grade.overall:.3f} (× 100 = {grade.overall*100:.1f})"),
    ]


def _format_side_by_side(
    agent_run: Optional[SystemRunResult],
    agent_grade: Optional[Grade],
    rag_run: Optional[SystemRunResult],
    rag_grade: Optional[Grade],
) -> str:
    """Side-by-side comparison of agent vs RAG grades."""
    metrics_keys = [
        "section_recall", "claim_precision", "exploration_cost",
        "fact_recall", "fact_density",
        "hallucination_penalty", "citation_quality",
        "answer_quality", "trajectory_efficiency",
    ]
    if not (agent_grade and rag_grade):
        # Single-system mode — just print whichever ran.
        single = agent_grade or rag_grade
        single_run = agent_run or rag_run
        rows = [
            (k, f"{getattr(single, k):.3f}")
            for k in metrics_keys
        ]
        rows.append((
            "OVERALL",
            f"{single.overall:.3f} (× 100 = {single.overall*100:.1f})",
        ))
        label = single_run.system_label if single_run else "system"
        out = [f"{'metric':24s}  {label}"]
        for k, v in rows:
            out.append(f"{k:24s}  {v}")
        return "\n".join(out)

    # Both ran — side-by-side.
    metrics = metrics_keys
    out = [
        f"{'metric':24s}  {'AGENT':>10s}  {'RAG':>10s}  {'DELTA':>10s}",
        "─" * 60,
    ]
    for m in metrics:
        a = getattr(agent_grade, m)
        r = getattr(rag_grade, m)
        delta = a - r
        sign = "+" if delta > 0 else ""
        out.append(
            f"{m:24s}  {a:>10.3f}  {r:>10.3f}  {sign}{delta:>9.3f}"
        )
    out.append("─" * 60)
    a_pct = agent_grade.overall * 100
    r_pct = rag_grade.overall * 100
    delta_pct = a_pct - r_pct
    sign = "+" if delta_pct > 0 else ""
    out.append(
        f"{'OVERALL × 100':24s}  {a_pct:>10.1f}  {r_pct:>10.1f}  {sign}{delta_pct:>9.1f}"
    )
    out.append("")
    out.append("trade-off (lower = better, except cost which is what you pay):")
    if agent_run and rag_run:
        out.append(
            f"  latency_ms_wall      {agent_run.latency_ms_wall:>10.0f}  "
            f"{rag_run.latency_ms_wall:>10.0f}"
        )
        out.append(
            f"  latency_ms_llm       {agent_run.latency_ms_llm:>10.0f}  "
            f"{rag_run.latency_ms_llm:>10.0f}"
        )
        out.append(
            f"  cost_usd             ${agent_run.cost_usd:>9.5f}  "
            f"${rag_run.cost_usd:>9.5f}"
        )
    return "\n".join(out)


# ── Main ──────────────────────────────────────────────────────────


async def _amain(args: argparse.Namespace) -> int:
    if not settings.premium_llm_api_key:
        logger.error("PREMIUM_LLM_API_KEY is not set")
        return 1

    # Optional judge-model override.  Monkeypatch the module
    # constant — experimentation hook.
    if args.judge_model:
        from tests.harness.evals import judge as _judge_mod
        _judge_mod._JUDGE_MODEL = args.judge_model
        logger.info("judge model overridden to %s", args.judge_model)

    entry = _load_entry(args.entry)

    print(_DRULE)
    print(f"GOLDEN ENTRY: {entry.id}")
    print(f"  category       : {entry.category}")
    print(f"  question_type  : {entry.question_type}")
    print(f"  difficulty     : {entry.difficulty}")
    print(f"  question       : {entry.question}")
    print(f"  must_contain   : {entry.must_contain}")
    print(f"  must_not_contain: {entry.must_not_contain}")
    print(f"  expected_recall_slugs: {entry.expected_recall_slugs}")
    print()

    agent_run: Optional[SystemRunResult] = None
    agent_grade: Optional[Grade] = None
    rag_run: Optional[SystemRunResult] = None
    rag_grade: Optional[Grade] = None

    # ── Run AGENT ───────────────────────────────────────────────
    if args.system in ("manual_agent", "both"):
        deps = _build_override_deps(args)
        agent_label = (
            f"{deps.config.model} via {args.llm_base_url}"
            if deps else "qwen3.5:27b-q8_0 via Ollama (default)"
        )
        print(_RULE)
        print(f"RUNNING AGENT ({agent_label})…")
        print(_RULE)
        agent_run = await run_manual_agent_unified(
            question=entry.question,
            obd_context=entry.obd_context,
            deps=deps,
        )
        print(_format_run_summary(agent_run))
        print()

    # ── Run RAG ─────────────────────────────────────────────────
    if args.system in ("rag", "both"):
        # The vehicle_model column on rag_chunks currently holds
        # 'MWS150-A' (no hyphen).  Pass None for unfiltered
        # retrieval — fixing the production inconsistency is a
        # separate ticket.
        print(_RULE)
        print(f"RUNNING RAG (top_k={args.top_k}, no vehicle filter)…")
        print(_RULE)
        rag_run = await run_rag(
            question=entry.question,
            top_k=args.top_k,
            vehicle_model=None,
        )
        print(_format_run_summary(rag_run))
        print()

    # ── Grade ───────────────────────────────────────────────────
    judge_label = args.judge_model or "z-ai/glm-5.1"
    if agent_run is not None:
        print(_RULE)
        print(f"GRADING AGENT (judge: {judge_label})…")
        print(_RULE)
        agent_grade = await grade_run(entry, agent_run)
        print(f"  overall × 100 = {agent_grade.overall*100:.1f}")
        print(f"  reasoning     : {agent_grade.reasoning}")
        print()

    if rag_run is not None:
        print(_RULE)
        print(f"GRADING RAG (judge: {judge_label})…")
        print(_RULE)
        rag_grade = await grade_run(entry, rag_run)
        print(f"  overall × 100 = {rag_grade.overall*100:.1f}")
        print(f"  reasoning     : {rag_grade.reasoning}")
        print()

    # ── Side-by-side ────────────────────────────────────────────
    print(_DRULE)
    print("FINAL SCORES")
    print(_DRULE)
    print(_format_side_by_side(
        agent_run, agent_grade, rag_run, rag_grade,
    ))
    print(_DRULE)

    return 0


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run agent + RAG against one golden entry and "
            "print side-by-side scores."
        ),
    )
    parser.add_argument(
        "entry", type=Path,
        help="Path to a JSON or JSONL GoldenEntry file.",
    )
    parser.add_argument(
        "--system", choices=("manual_agent", "rag", "both"),
        default="both",
        help="Which system(s) to evaluate.  Default: both.",
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="RAG top-k (default 5).  Ignored for agent runs.",
    )
    # Agent-side overrides
    parser.add_argument(
        "--model", default=None,
        help="Override agent model (e.g. 'qwen/qwen3.6-flash').",
    )
    parser.add_argument(
        "--llm-base-url", default=None,
        help="Override agent's LLM endpoint (e.g. OpenRouter).",
    )
    parser.add_argument(
        "--llm-api-key-env", default="PREMIUM_LLM_API_KEY",
        help="Env var holding the API key for the override.",
    )
    parser.add_argument(
        "--timeout", type=float, default=None,
        help="Per-iteration LLM timeout (seconds).",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Max ReAct iterations.",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None,
        help="Per-call max_tokens for the agent's LLM "
             "(default 12288).  Lower this when OpenRouter "
             "credits are tight — error 402 means the account "
             "balance can't cover the requested ceiling.",
    )
    parser.add_argument(
        "--no-think", action="store_true",
        help="Inject the Qwen3 '/no_think' directive into the "
             "agent's system prompt.  Required when running "
             "qwen3.5:27b-q8_0 on local Ollama — otherwise the "
             "agent times out emitting hidden reasoning tokens. "
             "Drops first-token latency from ~91s to ~2.5s.  "
             "Harmless on non-Qwen models (the directive is "
             "ignored).",
    )
    parser.add_argument(
        "--judge-model", default=None,
        help="Override the judge model (default z-ai/glm-5.1).  "
             "DeepSeek V4 Pro is a good alt for heavy Chinese "
             "contexts.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

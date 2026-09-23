"""Shared tool-execution path: memoisation, truncation, trace, logging.

Every tool body is ``return await execute(ctx, name, args, impl)``.  The
helper (FM-12 / FM-52 / T-13):

* returns the cached result for a byte-identical repeat call, prefixed
  with ``[repeated call — same result as before]``;
* converts an unexpected exception into a short ``Error: …`` text so the
  model can self-correct (``ModelRetry`` passes through);
* truncates the text to the per-result budget (FM-8 in V2 terms) — the
  main agent's 2000-token cap; inside a sub-agent only a runaway guard
  (V2's sub-agents saw tool output whole; PROD-10 found the 2000 cut
  hiding half of the manual TOC and the middle of long sections);
* records a ``ToolCallTrace`` on the calling agent's deps and the
  duration under the tool call id (the runtime reads it for events);
* logs one structlog line with sizes only — never the content.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Dict

import structlog
from pydantic_ai import ModelRetry, RunContext

from stf_v3.diagnosis.agent.context import truncate_text
from stf_v3.diagnosis.agent.deps import SubAgentDeps, ToolCallTrace, core_deps

logger = structlog.get_logger(__name__)

REPEAT_PREFIX = "[repeated call — same result as before]\n"
_MAX_ERROR_CHARS = 200


def _memo_key(name: str, args: Dict[str, Any]) -> str:
    return name + ":" + json.dumps(args, sort_keys=True, default=str)


async def execute(
    ctx: RunContext[Any],
    name: str,
    args: Dict[str, Any],
    impl: Callable[[], Awaitable[str]],
) -> str:
    """Run a tool implementation through the shared path (see module doc)."""
    deps = ctx.deps
    core = core_deps(deps)
    key = _memo_key(name, args)
    started = time.monotonic()
    is_error = False
    repeated = key in deps.memo
    if repeated:
        result = REPEAT_PREFIX + deps.memo[key]
    else:
        try:
            result = await impl()
            if not isinstance(result, str):
                result = str(result)
        except ModelRetry:
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as text
            is_error = True
            msg = str(exc)
            if len(msg) > _MAX_ERROR_CHARS:
                msg = msg[:_MAX_ERROR_CHARS] + "..."
            result = f"Error: tool '{name}' failed — {type(exc).__name__}: {msg}"
        cap = (core.budgets.subagent_tool_result_max_tokens if isinstance(deps, SubAgentDeps)
               else core.budgets.tool_result_max_tokens)
        result = truncate_text(result, cap)
        if not is_error:
            deps.memo[key] = result
    elapsed_ms = (time.monotonic() - started) * 1000.0
    entry = ToolCallTrace(
        name=name,
        input={k: (v[:500] + "..." if isinstance(v, str) and len(v) > 500 else v)
               for k, v in args.items()},
        latency_ms=elapsed_ms,
        is_error=is_error,
        tool_call_id=ctx.tool_call_id,
        output_chars=len(result),
    )
    deps.trace.append(entry)
    if deps is not core:
        core.trace.append(entry)
        if not is_error and not repeated and hasattr(deps, "excerpts"):
            deps.excerpts.append((name, result))
    if ctx.tool_call_id:
        core.durations[ctx.tool_call_id] = elapsed_ms
    logger.info(
        "agent.tool",
        tool=name,
        arg_keys=sorted(args.keys()),
        duration_ms=round(elapsed_ms, 1),
        output_chars=len(result),
        is_error=is_error,
        repeated=repeated,
        tool_call_id=ctx.tool_call_id,
    )
    return result

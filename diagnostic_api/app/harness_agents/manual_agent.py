"""Manual-search sub-agent: restricted 3-tool ReAct loop.

Answers a single diagnostic inquiry by navigating vehicle service
manuals.  Uses only the 3 manual-fs navigation tools
(``list_manuals``, ``get_manual_toc``, ``read_manual_section``) —
no access to OBD data, no semantic RAG search (``search_manual``
was removed in HARNESS-15 to keep the agent's capabilities
architecturally orthogonal to the RAG comparison track), no
session-event persistence, no SSE streaming.

The sub-agent reuses ``LLMClient`` protocol and ``ToolRegistry``
from ``app.harness`` but runs its own minimal loop and returns a
single structured ``ManualAgentResult`` for evaluation scoring.

Final output contract: the LLM stops calling tools and returns a
JSON object with ``summary`` and ``citations`` fields.  The loop
parses this and merges it with the ``raw_sections`` / ``tool_trace``
data captured during execution.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.harness.deps import LLMClient
from app.harness.tool_registry import ToolRegistry
from app.harness_agents.manual_agent_prompts import (
    MANUAL_AGENT_SYSTEM_PROMPT,
    build_manual_agent_user_message,
)
from app.harness_agents.types import (
    Citation,
    ManualAgentResult,
    SectionRef,
    ToolCallTrace,
)
from app.harness_tools.manual_fs import (
    parse_heading_tree,
    slugify,
)
from app.harness_tools.manual_tools import (
    GET_MANUAL_TOC_DEF,
    LIST_MANUALS_DEF,
    READ_MANUAL_SECTION_DEF,
    _read_manual_file,
)

logger = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────

_DEFAULT_MODEL = "qwen3.5:27b-q8_0"
"""Local Ollama model served by the PolyU GPU server.  This is the
agent under evaluation — what actually ships.  Override via
``ManualAgentConfig.model`` to run a ceiling comparison (e.g.
``z-ai/glm-5.1`` or ``moonshotai/kimi-k2``)."""

_DEFAULT_MAX_ITERATIONS = 12
"""ReAct iteration cap.  Raised 8 → 12 in HARNESS-23 T1 (#143)
after the first-round eval: 6/30 runs exhausted the old 8-iter cap
mid-answer.  Multi-part ``cross-section`` questions need more
TOC-navigate / section-read cycles than the original HARNESS-14
plan assumed."""

_DEFAULT_MAX_TOKENS = 12_288
"""Per-call output token cap.  Leaves headroom for the final JSON
payload plus a few large tool_result messages in context.  Kept at
12288 in HARNESS-23 T1 (#143): the first-round budget failures were
iteration/wall-clock bound, not output-token bound (no run hit the
per-call cap), so this stays put."""

_DEFAULT_TIMEOUT = 240.0
"""Wall-clock budget for the whole sub-agent run.  Raised 120 → 240
in HARNESS-23 T1 (#143), mirroring the OBD agent's 240 s precedent.
At a stable ~10-24 s/iter (``qwen3.5:27b`` in thinking mode) the old
120 s wall cut runs off at only 5-7 iterations — 13/30 first-round
runs timed out before converging.  The cap and the wall bind
*different* entries, so both moved together."""

_DEFAULT_TEMPERATURE = 0.2
"""Low but non-zero — deterministic enough for eval, with small
exploration for tool-call decisions."""

_MAX_FINAL_SUMMARY_CHARS = 4000
"""Safety cap on the parsed ``summary`` length to keep the report
artifact bounded."""

_MAX_REDUNDANT_ITERATIONS = 2
"""No-progress backstop (HARNESS-23 T2 / #144).  An iteration is
"redundant" when every tool call it issued is byte-identical to a
call already made earlier in the run — i.e. the agent is re-reading
the same sections / re-listing the same manuals without surfacing
anything new.  After the FIRST redundant iteration the loop injects a
one-time nudge telling the agent to decline now; if it spins again
and reaches this count, the loop force-finalizes a "Not found"
decline with ``stopped_reason="complete"`` instead of grinding to the
wall-clock timeout.  The dominant first-round adversarial failure was
the agent declining in substance but never emitting the final JSON,
so all 6 adversarial runs timed out at ``answer_quality=0``."""

_DECLINE_NUDGE = (
    "You appear to be repeating tool calls you have already made, "
    "which will not surface new information.  If the answer is "
    "genuinely not present in the available manuals, or no manual "
    "matches the vehicle in the question, STOP now and return the "
    "\"Not found\" JSON shape — "
    '{"summary": "Not found: <short explanation>", "citations": []} '
    "— with no further tool calls.  Otherwise read only a section "
    "you have not read yet."
)
"""One-time forcing message injected after the first no-progress
iteration (see ``_MAX_REDUNDANT_ITERATIONS``)."""

_FORCED_DECLINE_SUMMARY = (
    "Not found: the available service manuals do not contain "
    "information answering this question."
)
"""Canned decline used when the loop force-finalizes and the agent's
own last message did not already read as a decline."""


# ── Configuration + deps ──────────────────────────────────────────


@dataclass(frozen=True)
class ManualAgentConfig:
    """Tunable knobs for the manual sub-agent loop.

    Attributes:
        model: LLM identifier.  Defaults to the local Qwen served
            by Ollama (what ships).  Set to an OpenRouter model ID
            (e.g. ``"z-ai/glm-5.1"``) for ceiling comparison.
        max_iterations: Hard cap on ReAct cycles.
        max_tokens: Per-LLM-call output token budget.
        temperature: Sampling temperature.
        timeout_seconds: Total wall-clock budget.
    """

    model: str = _DEFAULT_MODEL
    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    max_tokens: int = _DEFAULT_MAX_TOKENS
    temperature: float = _DEFAULT_TEMPERATURE
    timeout_seconds: float = _DEFAULT_TIMEOUT


@dataclass
class ManualAgentDeps:
    """Injected dependencies for the manual sub-agent.

    Attributes:
        llm_client: Any object satisfying ``LLMClient`` protocol.
        tool_registry: Must contain only the 4 manual tools.  Use
            ``create_manual_agent_registry()`` to build one.
        config: Tunable knobs.
    """

    llm_client: LLMClient
    tool_registry: ToolRegistry
    config: ManualAgentConfig


def create_manual_agent_registry() -> ToolRegistry:
    """Build a ``ToolRegistry`` with the 3 manual-fs navigation tools.

    Excludes:

    - ``read_obd_data`` — the manual sub-agent never inspects
      OBD data directly.
    - ``search_manual`` — removed for the comparative-eval
      benchmark (HARNESS-15 / Issue #74).  ``search_manual`` is
      a thin wrapper around ``app.rag.retrieve.retrieve_context``
      — the same call the RAG track uses.  Keeping it in the
      agent's toolkit muddied the comparison ("agent + RAG vs
      RAG"), and observed agent runs showed the LLM repeatedly
      called ``search_manual`` on identifier-based queries
      (DTC codes), got noise back, and pivoted to TOC navigation
      anyway.  Removing it makes the agent's capabilities
      architecturally orthogonal to RAG: agent navigates
      structurally via TOC + section reads; RAG retrieves
      semantically via pgvector.  Cleaner story for the paper,
      and faster runs (no ~150ms semantic-search calls per
      iteration).

    Callers pass this registry into ``ManualAgentDeps`` rather
    than the default harness registry.

    Returns:
        A fresh ``ToolRegistry`` with exactly 3 tools registered:
        ``list_manuals``, ``get_manual_toc``, ``read_manual_section``.
    """
    registry = ToolRegistry()
    for tool_def in (
        LIST_MANUALS_DEF,
        GET_MANUAL_TOC_DEF,
        READ_MANUAL_SECTION_DEF,
    ):
        registry.register(tool_def)
    return registry


# ── Helpers ───────────────────────────────────────────────────────


def _build_initial_messages(
    question: str,
    obd_context: Optional[str],
) -> List[Dict[str, Any]]:
    """Assemble the opening system + user messages."""
    return [
        {
            "role": "system",
            "content": MANUAL_AGENT_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": build_manual_agent_user_message(
                question, obd_context,
            ),
        },
    ]


def _parse_tool_arguments(raw: str) -> Dict[str, Any]:
    """Safely parse a JSON arguments string.

    Returns a dict with a ``_parse_error`` key on failure so the
    registry can return a validation error instead of crashing.
    """
    try:
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            return parsed
        return {
            "_parse_error": (
                f"expected object, got {type(parsed).__name__}"
            ),
        }
    except (json.JSONDecodeError, TypeError) as exc:
        return {"_parse_error": str(exc)}


def _make_assistant_message(
    content: Optional[str],
    tool_calls: List,
) -> Dict[str, Any]:
    """Build assistant message (OpenAI format) to append to history."""
    msg: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                },
            }
            for tc in tool_calls
        ]
    return msg


def _make_tool_message(
    tool_call_id: str, output: Any,
) -> Dict[str, Any]:
    """Build tool-result message for the conversation history."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": output,
    }


def _canonicalise_slug(
    candidate: str,
    known_slugs: List[str],
) -> str:
    """Resolve a free-form section reference to a canonical slug.

    LLMs frequently echo a section's display title (e.g. "故障代碼
    編號 P0117、P0118") into citation/argument fields where the
    eval suite expects the parser-produced slug ("p0117-p0118").
    This helper applies the same matching strategies the
    ``read_manual_section`` tool uses internally so both sides
    converge on the canonical form.

    Strategy order:

    1. Exact match against ``known_slugs``.
    2. Slugify the candidate and re-check for an exact match.
    3. Substring fallback — first slug that contains the
       slugified candidate.

    Args:
        candidate: Free-form section reference from the LLM.
        known_slugs: All canonical slugs from the manual's
            heading tree.

    Returns:
        The canonical slug if any strategy matches, otherwise the
        original ``candidate`` unchanged so callers can still
        serialise something readable for diagnostics.
    """
    if candidate in known_slugs:
        return candidate
    slugified = slugify(candidate)
    if slugified in known_slugs:
        return slugified
    if slugified:
        for slug in known_slugs:
            if slugified in slug:
                return slug
    return candidate


def _slugs_for_manual(manual_id: str) -> List[str]:
    """Return all canonical slugs for a manual, or an empty list.

    Helper that loads the manual markdown via the same code path
    the tool uses, parses the heading tree, and returns the flat
    slug list.  Returns ``[]`` if the manual cannot be loaded —
    callers fall back to the LLM's raw input in that case.
    """
    md_text = _read_manual_file(manual_id)
    if md_text is None:
        return []
    tree = parse_heading_tree(md_text)
    out: List[str] = []
    stack: List[Any] = list(tree)
    while stack:
        node = stack.pop()
        if node.slug:
            out.append(node.slug)
        stack.extend(node.children)
    return out


def _extract_section_ref(
    input_data: Dict[str, Any],
    output: Any,
) -> Optional[SectionRef]:
    """Extract a ``SectionRef`` from a ``read_manual_section`` result.

    Handles both plain-string outputs (text-only sections) and
    content-block lists (multimodal sections with images).  Returns
    ``None`` if the section identity cannot be determined from the
    tool input (shouldn't happen in practice — both ``manual_id``
    and ``section`` are required fields).

    The recorded ``slug`` is the *canonical* slug produced by
    ``parse_heading_tree``, not whatever free-form string the LLM
    happened to pass in.  This is a deliberate divergence from
    "input echo": the LLM frequently passes a heading title
    (because that's what ``get_manual_toc`` shows it) and we want
    the eval pipeline to grade against the parser-stable slug.

    Args:
        input_data: Arguments passed to the tool (minus the
            ``_session_id`` injection, which manual tools don't
            use).
        output: ``ToolResult.output`` — either ``str`` or
            ``List[ContentBlock]``.

    Returns:
        A ``SectionRef`` ready to append to ``raw_sections``,
        or ``None`` if inputs are malformed.
    """
    manual_id = input_data.get("manual_id")
    raw_slug = input_data.get("section")
    if not manual_id or not raw_slug:
        return None

    canonical = _canonicalise_slug(
        str(raw_slug),
        _slugs_for_manual(str(manual_id)),
    )

    had_images = False
    if isinstance(output, str):
        text = output
    elif isinstance(output, list):
        text_parts: List[str] = []
        for block in output:
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "image_url":
                had_images = True
        text = "\n".join(text_parts)
    else:
        text = str(output)

    return SectionRef(
        manual_id=str(manual_id),
        slug=canonical,
        text=text,
        had_images=had_images,
    )


_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)

_JSON_OBJECT_RE = re.compile(
    r"\{.*\}", re.DOTALL,
)


def _strip_markdown_fence(content: str) -> str:
    """Unwrap a ```json ... ``` code fence if present."""
    match = _MARKDOWN_FENCE_RE.match(content.strip())
    if match:
        return match.group(1).strip()
    return content.strip()


def _parse_final_json(
    content: Optional[str],
    raw_sections: Optional[List[SectionRef]] = None,
) -> Tuple[str, List[Citation]]:
    """Parse the LLM's final answer into (summary, citations).

    Tolerates common formatting deviations: markdown fences,
    leading/trailing prose, single quotes (not supported —
    falls through to raw-content fallback).

    Each emitted citation's ``slug`` is canonicalised against the
    set of slugs the agent already retrieved into ``raw_sections``
    when one is supplied — protecting against the common LLM
    failure mode of echoing a section's display title back into
    the citation field instead of the parser slug.  When the LLM
    cites a slug that was never retrieved, the value is left
    unchanged so the judge sees what the model actually said.

    Args:
        content: The ``content`` field of the terminal LLM
            response (when ``finish_reason == "stop"``).
        raw_sections: Sections retrieved during the run.  Their
            ``.slug`` values seed the canonicalisation table.
            Optional so existing call sites that only need
            ``(summary, citations)`` from a string still compile.

    Returns:
        ``(summary, citations)`` tuple.  If parsing fails, returns
        the raw content (truncated) as the summary and an empty
        citation list — the judge can still score must_contain
        recall against the text.
    """
    if not content:
        return (
            "The agent produced no final content.",
            [],
        )

    stripped = _strip_markdown_fence(content)

    # Attempt 1: direct JSON parse.
    payload: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract first {...} block.
    if payload is None:
        match = _JSON_OBJECT_RE.search(stripped)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                pass

    # Fallback: treat raw content as summary.
    if payload is None:
        logger.warning(
            "manual_agent_final_json_parse_failed",
            preview=stripped[:200],
        )
        truncated = stripped[:_MAX_FINAL_SUMMARY_CHARS]
        return (truncated, [])

    summary = str(
        payload.get("summary", "")
    )[:_MAX_FINAL_SUMMARY_CHARS]

    # Build a per-manual canonical-slug table from raw_sections.
    # The LLM frequently echoes a section's display title (which
    # it saw in get_manual_toc output) back into citation slugs —
    # this lookup repairs that to the parser-canonical form so
    # the judge's section_match check works.
    known_slugs_by_manual: Dict[str, List[str]] = {}
    if raw_sections:
        for sec in raw_sections:
            known_slugs_by_manual.setdefault(
                sec.manual_id, [],
            ).append(sec.slug)

    citations: List[Citation] = []
    raw_cits = payload.get("citations", [])
    if isinstance(raw_cits, list):
        for cit in raw_cits:
            if not isinstance(cit, dict):
                continue
            try:
                cit_manual = str(cit.get("manual_id", ""))
                raw_slug = str(cit.get("slug", ""))
                known = known_slugs_by_manual.get(
                    cit_manual, [],
                )
                canonical = (
                    _canonicalise_slug(raw_slug, known)
                    if known else raw_slug
                )
                citations.append(Citation(
                    manual_id=cit_manual,
                    slug=canonical,
                    quote=str(cit.get("quote", "")),
                ))
            except Exception:  # noqa: BLE001
                # Pydantic validation failure — skip this cite.
                continue

    return (summary, citations)


def _sanitize_tool_input_for_trace(
    input_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Produce a small, JSON-friendly copy of tool-call arguments.

    Strips any injected session fields (defensive — manual tools
    don't need them) and caps long string values so the tool trace
    stays report-friendly.

    Args:
        input_data: Raw argument dict passed to the tool.

    Returns:
        A new dict safe for serialising into the report artifact.
    """
    cleaned: Dict[str, Any] = {}
    for key, val in input_data.items():
        if key.startswith("_"):
            continue
        if isinstance(val, str) and len(val) > 500:
            cleaned[key] = val[:500] + "..."
        else:
            cleaned[key] = val
    return cleaned


# ── Core loop ────────────────────────────────────────────────────


async def run_manual_agent(
    question: str,
    obd_context: Optional[str],
    deps: ManualAgentDeps,
) -> ManualAgentResult:
    """Run the manual sub-agent against a diagnostic inquiry.

    Drives a restricted ReAct loop (only the 4 manual tools) until
    the LLM either stops calling tools (returning a final JSON
    answer) or the iteration/timeout budget is exhausted.

    Args:
        question: The inquiry.
        obd_context: Optional OBD context snippet.
        deps: Injected dependencies (``LLMClient``,
            ``ToolRegistry``, ``ManualAgentConfig``).

    Returns:
        A fully-populated ``ManualAgentResult`` with summary,
        citations, raw_sections captured during tool execution,
        tool_trace, iteration count, and stopped_reason.
    """
    run_id = uuid.uuid4().hex[:8]
    cfg = deps.config
    tool_schemas = deps.tool_registry.schemas

    messages = _build_initial_messages(question, obd_context)
    raw_sections: List[SectionRef] = []
    tool_trace: List[ToolCallTrace] = []
    iterations = 0
    final_summary = ""
    final_citations: List[Citation] = []
    stopped_reason: str = "max_iterations"
    # No-progress backstop state (HARNESS-23 T2 / #144).
    seen_call_signatures: set = set()
    redundant_iterations = 0
    nudge_issued = False

    logger.info(
        "manual_agent_start",
        run_id=run_id,
        model=cfg.model,
        max_iterations=cfg.max_iterations,
        has_obd_context=obd_context is not None,
    )

    try:
        async with asyncio.timeout(cfg.timeout_seconds):
            while iterations < cfg.max_iterations:
                try:
                    response = await deps.llm_client.chat(
                        messages=messages,
                        tools=tool_schemas,
                        model=cfg.model,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "manual_agent_llm_error",
                        run_id=run_id,
                        iteration=iterations,
                        exc_info=exc,
                    )
                    stopped_reason = "error"
                    break

                # Terminal response — parse final JSON and stop.
                if (
                    response.finish_reason == "stop"
                    or not response.tool_calls
                ):
                    final_summary, final_citations = (
                        _parse_final_json(
                            response.content, raw_sections,
                        )
                    )
                    stopped_reason = "complete"
                    break

                # Tool-calling response — append assistant message.
                messages.append(
                    _make_assistant_message(
                        response.content, response.tool_calls,
                    ),
                )

                for tc in response.tool_calls:
                    args = _parse_tool_arguments(tc.arguments)

                    if "_parse_error" in args:
                        # Surface a clean error to the LLM so it
                        # can self-correct on the next turn.
                        error_msg = (
                            f"Error: could not parse tool "
                            f"arguments — {args['_parse_error']}"
                        )
                        tool_trace.append(ToolCallTrace(
                            name=tc.name,
                            input={"_raw": tc.arguments[:200]},
                            latency_ms=0.0,
                            is_error=True,
                        ))
                        messages.append(
                            _make_tool_message(
                                tc.id, error_msg,
                            ),
                        )
                        continue

                    result = await (
                        deps.tool_registry.execute(
                            tc.name, args,
                        )
                    )

                    tool_trace.append(ToolCallTrace(
                        name=tc.name,
                        input=(
                            _sanitize_tool_input_for_trace(args)
                        ),
                        latency_ms=result.duration_ms,
                        is_error=result.is_error,
                    ))

                    # Capture read_manual_section output into
                    # raw_sections for later grading.
                    if (
                        tc.name == "read_manual_section"
                        and not result.is_error
                    ):
                        section_ref = _extract_section_ref(
                            args, result.output,
                        )
                        if section_ref is not None:
                            raw_sections.append(section_ref)

                    messages.append(
                        _make_tool_message(
                            tc.id, result.output,
                        ),
                    )

                # ── No-progress backstop (HARNESS-23 T2 / #144) ──
                # An iteration is "redundant" when every tool call it
                # issued is byte-identical to a call already made —
                # the agent is re-reading the same evidence rather
                # than declining.  Nudge once, then force a clean
                # "Not found" so adversarial / unanswerable questions
                # finalize with stopped_reason="complete" instead of
                # spinning to the wall-clock timeout.
                signatures = {
                    f"{tc.name}:{tc.arguments}"
                    for tc in response.tool_calls
                }
                if signatures - seen_call_signatures:
                    redundant_iterations = 0
                else:
                    redundant_iterations += 1
                seen_call_signatures |= signatures

                if redundant_iterations >= _MAX_REDUNDANT_ITERATIONS:
                    final_summary, final_citations = (
                        _force_not_found_finalize(
                            messages, raw_sections,
                        )
                    )
                    stopped_reason = "complete"
                    logger.info(
                        "manual_agent_forced_decline",
                        run_id=run_id,
                        iteration=iterations,
                        redundant_iterations=redundant_iterations,
                    )
                    break
                if redundant_iterations >= 1 and not nudge_issued:
                    messages.append({
                        "role": "user",
                        "content": _DECLINE_NUDGE,
                    })
                    nudge_issued = True

                iterations += 1

            else:
                # while-else: normal exit via max_iterations.
                stopped_reason = "max_iterations"

    except TimeoutError:
        logger.warning(
            "manual_agent_timeout",
            run_id=run_id,
            iteration=iterations,
            timeout_seconds=cfg.timeout_seconds,
        )
        stopped_reason = "timeout"

    # If we broke out with "complete", iterations reflects the
    # iteration that produced the final answer — but we never
    # bumped the counter.  Report the count including the
    # terminal step.
    reported_iterations = iterations + (
        1 if stopped_reason == "complete" else 0
    )

    if stopped_reason != "complete" and not final_summary:
        # Provide a placeholder summary so the judge can still
        # run grading; real content may be present in the last
        # assistant message.
        final_summary = _extract_last_assistant_content(messages)

    logger.info(
        "manual_agent_done",
        run_id=run_id,
        iterations=reported_iterations,
        stopped_reason=stopped_reason,
        tool_calls=len(tool_trace),
        raw_sections=len(raw_sections),
    )

    return ManualAgentResult(
        summary=final_summary,
        citations=final_citations,
        raw_sections=raw_sections,
        tool_trace=tool_trace,
        iterations=reported_iterations,
        total_tokens=0,  # Not tracked in v1 — OpenAI adapter
        # does not surface usage in LLMResponse yet.
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )


def _force_not_found_finalize(
    messages: List[Dict[str, Any]],
    raw_sections: List[SectionRef],
) -> Tuple[str, List[Citation]]:
    """Synthesize a "Not found" decline for the no-progress backstop.

    Invoked when the loop detects the agent is spinning on redundant
    tool calls (see ``_MAX_REDUNDANT_ITERATIONS``).  Prefers the
    agent's own last message when it already reads as a decline so
    its specific explanation is preserved; otherwise falls back to a
    canned decline.  Either way the returned summary is in the
    documented "Not found" shape so the judge credits a correct
    refusal (HARNESS-23 T2 / #144, pairs with T5 / #146).

    Args:
        messages: Full conversation history.
        raw_sections: Sections retrieved so far (seed the citation
            slug canonicalisation, though a decline normally cites
            nothing).

    Returns:
        ``(summary, citations)`` — the summary always begins with
        ``"Not found:"``.
    """
    last = _extract_last_assistant_content(messages)
    if last and "not found" in last.lower():
        summary, citations = _parse_final_json(last, raw_sections)
        if summary.lower().startswith("not found"):
            return (summary, citations)
    return (_FORCED_DECLINE_SUMMARY, [])


def _extract_last_assistant_content(
    messages: List[Dict[str, Any]],
) -> str:
    """Return the last non-empty assistant content, else fallback.

    Used when the loop ended via timeout or max_iterations so the
    judge has something to grade (often the agent's partial
    reasoning contains clues about must_contain terms).

    Args:
        messages: Full conversation history.

    Returns:
        The last assistant message content, truncated, or a
        canned fallback when nothing is available.
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:_MAX_FINAL_SUMMARY_CHARS]
    return (
        "The agent did not produce a final answer within the "
        "budget."
    )

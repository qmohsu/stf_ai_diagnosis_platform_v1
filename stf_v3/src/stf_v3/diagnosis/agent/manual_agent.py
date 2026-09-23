"""Manual-search sub-agent (V2 ``manual_agent.py`` on Pydantic AI).

A restricted agent with only the 4 manual tools.  The V2 loop's guards
live in ``guards.py`` and are wired here: the tools are withheld once the
guard state says ``force_final`` (a ``prepare`` hook), and the
force-final instruction is appended to what the model sees (a history
processor — sent, not stored).  The final answer is plain text parsed with
the V2 JSON extractor (FM-41), or — on the force-final turn only — a call
to the ``final_answer`` tool (PROD-10); when the run stops on a gate the
last assistant text (or a canned decline) becomes the summary.

PROD-10: on the force-final turn Qwen3.6 kept "calling tools" with none
offered — the call came back as text and the answer was lost.  Now that
turn offers exactly one tool, ``final_answer`` (summary + citation
objects), so the urge to call a tool IS the answer.  It is NOT offered on
normal turns: when it was (an output tool), the model answered after one
or two reads and the manual lane fell to 0.61.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition

from stf_v3.diagnosis.agent.context import last_assistant_text
from stf_v3.diagnosis.agent.deps import DiagDeps, SubAgentDeps
from stf_v3.diagnosis.agent.guards import (
    FORCE_FINAL_INSTRUCTION,
    FORCED_DECLINE_SUMMARY,
    NUDGE_FINAL_INSTRUCTION,
    ManualGuardState,
)
from stf_v3.diagnosis.agent.runner import RunOutcome, drive, make_limits
from stf_v3.diagnosis.agent.subagent_prompts import (
    MANUAL_AGENT_SYSTEM_PROMPT,
    build_manual_agent_user_message,
)
from stf_v3.diagnosis.agent.types import Citation, ManualAgentResult, SectionRef, ToolTraceEntry
from stf_v3.diagnosis.tools.manual_tools import MANUAL_TOOLS, resolve_section_slug

logger = structlog.get_logger(__name__)

_MAX_FINAL_SUMMARY_CHARS = 4000
_MARKDOWN_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL | re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


# ── Agent definition ─────────────────────────────────────────────


def _withhold_when_forced(ctx: RunContext[SubAgentDeps], tool_def: ToolDefinition) -> Optional[ToolDefinition]:
    state = ctx.deps.state
    if isinstance(state, ManualGuardState) and state.force_final:
        return None
    return tool_def


FINAL_ANSWER_TOOL = "final_answer"
_FINAL_ANSWER_HINT = (
    "Submit it by calling the `final_answer` tool: `summary`, and `citations` "
    "as one object per cited section (`manual_id`, `slug`, `quote`)."
)
FORCED_TURN_PROMPT = FORCE_FINAL_INSTRUCTION + "  " + _FINAL_ANSWER_HINT
MANUAL_NUDGE_PROMPT = NUDGE_FINAL_INSTRUCTION + "  " + _FINAL_ANSWER_HINT


class FinalCitation(BaseModel):
    """One cited manual section."""

    manual_id: str = Field(default="", description="The manual id you read (from list_manuals).")
    slug: str = Field(description="The section slug / node id you read.")
    quote: str = Field(default="", description="A short verbatim excerpt from that section.")


class ManualFinalAnswer(BaseModel):
    """The manual sub-agent's final answer (the ``final_answer`` tool)."""

    summary: str = Field(description="The answer (for procedures: every step, as a numbered list), "
                                     "or 'Not found: <short explanation>'.")
    citations: List[FinalCitation] = Field(default_factory=list,
                                           description="Sections the answer relies on; empty when not found.")


def _force_final_processor(ctx: RunContext[SubAgentDeps], messages: List[ModelMessage]) -> List[ModelMessage]:
    state = ctx.deps.state
    if not (isinstance(state, ManualGuardState) and state.force_final):
        return messages
    last = messages[-1] if messages else None
    if isinstance(last, ModelRequest) and any(
        isinstance(p, UserPromptPart) and p.content in (FORCED_TURN_PROMPT, MANUAL_NUDGE_PROMPT)
        for p in last.parts
    ):
        return messages
    return list(messages) + [ModelRequest(parts=[UserPromptPart(content=FORCED_TURN_PROMPT)])]


def _offer_final_answer(ctx: RunContext[SubAgentDeps], tool_def: ToolDefinition) -> Optional[ToolDefinition]:
    """``final_answer`` exists only on the force-final turn, until used."""
    state = ctx.deps.state
    if isinstance(state, ManualGuardState) and state.force_final and state.final_answer is None:
        return tool_def
    return None


async def final_answer(ctx: RunContext[SubAgentDeps], summary: str, citations: List[FinalCitation]) -> str:
    """Submit your final answer and finish.

    Args:
        summary: The answer (for procedures: every step, as a numbered list),
            or "Not found: <short explanation>".
        citations: One object per section the answer relies on (manual_id,
            slug, quote); empty when not found.
    """
    state = ctx.deps.state
    if isinstance(state, ManualGuardState):
        state.final_answer = ManualFinalAnswer(summary=summary, citations=citations)
    return "Final answer recorded. Reply with the single word DONE."


def build_manual_agent() -> Agent[SubAgentDeps, str]:
    """The manual sub-agent definition (model supplied at run time)."""
    tools = [
        Tool(fn, prepare=_withhold_when_forced, require_parameter_descriptions=True,
             docstring_format="google")
        for fn in MANUAL_TOOLS
    ]
    tools.append(Tool(final_answer, name=FINAL_ANSWER_TOOL, prepare=_offer_final_answer,
                      require_parameter_descriptions=True, docstring_format="google"))
    return Agent(
        None, deps_type=SubAgentDeps, output_type=str, instructions=MANUAL_AGENT_SYSTEM_PROMPT,
        tools=tools, retries=2, name="manual_agent",
    )


MANUAL_AGENT: Agent[SubAgentDeps, str] = build_manual_agent()
MANUAL_TOOL_NAMES = frozenset(fn.__name__ for fn in MANUAL_TOOLS)


# ── Final-answer parsing (copied from V2) ────────────────────────


def _strip_markdown_fence(content: str) -> str:
    match = _MARKDOWN_FENCE_RE.match(content.strip())
    return match.group(1).strip() if match else content.strip()


def _canonical_from_sections(manual_id: str, raw_slug: str, raw_sections: List[SectionRef]) -> str:
    known = [s.slug for s in raw_sections if s.manual_id == manual_id]
    if raw_slug in known:
        return raw_slug
    from stf_v3.knowledge.manual_fs import slugify

    slugified = slugify(raw_slug)
    if slugified in known:
        return slugified
    for slug in known:
        if slugified and slugified in slug:
            return slug
    return raw_slug


def has_final_json(text: Any) -> bool:
    """Whether the sub-agent's final output is its answer (the
    ``final_answer`` tool, or text carrying the JSON answer object)."""
    if isinstance(text, ManualFinalAnswer):
        return True
    return isinstance(text, str) and bool(text) and '"summary"' in text


def final_from_output(output: Any, raw_sections: List[SectionRef]) -> Tuple[str, List[Citation]]:
    """``(summary, citations)`` from the run output: the ``final_answer``
    tool's arguments, else the V2 text parser."""
    if isinstance(output, ManualFinalAnswer):
        cits = [citation_from_item(c.model_dump(), raw_sections) for c in output.citations]
        return output.summary[:_MAX_FINAL_SUMMARY_CHARS], [c for c in cits if c is not None]
    return parse_final_json(output if isinstance(output, str) else None, raw_sections)


def parse_final_json(content: Optional[str], raw_sections: Optional[List[SectionRef]] = None) -> Tuple[str, List[Citation]]:
    """``(summary, citations)`` from the model's final text (fence / prose tolerant)."""
    if not content:
        return "The agent produced no final content.", []
    stripped = _strip_markdown_fence(content)
    payload: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        pass
    if payload is None:
        match = _JSON_OBJECT_RE.search(stripped)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                pass
    if payload is None:
        logger.warning("manual_agent.final_json_parse_failed", chars=len(stripped))
        return stripped[:_MAX_FINAL_SUMMARY_CHARS], []
    summary = str(payload.get("summary", ""))[:_MAX_FINAL_SUMMARY_CHARS]
    citations: List[Citation] = []
    raw_cits = payload.get("citations", [])
    if isinstance(raw_cits, list):
        for cit in raw_cits:
            try:
                parsed = citation_from_item(cit, raw_sections or [])
            except Exception:  # noqa: BLE001
                parsed = None
            if parsed is not None:
                citations.append(parsed)
    return summary, citations


# PROD-10: Qwen3.6 on vLLM often writes citations as strings
# ("<slug>: <quote>") or with other keys instead of the requested
# {manual_id, slug, quote} object; V2's parser dropped those, which left
# 20 of 30 golden answers with no citation at all.
_STRING_CITE_RE = re.compile(r"^\s*(?P<slug>[^:：\n]+?)\s*[:：]\s*(?P<quote>.*)$", re.DOTALL)


def _manual_for_slug(raw_slug: str, raw_sections: List[SectionRef]) -> str:
    """The manual a cited slug belongs to, from the sections actually read."""
    ids = []
    for s in raw_sections:
        if s.manual_id not in ids:
            ids.append(s.manual_id)
    if len(ids) == 1:
        return ids[0]
    for s in raw_sections:
        if s.slug == raw_slug or (raw_slug and raw_slug in s.slug):
            return s.manual_id
    return ids[0] if ids else ""


def citation_from_item(cit: Any, raw_sections: List[SectionRef]) -> Optional[Citation]:
    """One citation from the model's JSON: an object (``manual_id`` /
    ``slug`` / ``quote``, also ``section`` / ``node_id`` / ``excerpt``) or a
    string ``"<slug>: <quote>"`` / ``"<slug>"``.  The manual id, when
    missing, comes from the sections read; the slug is canonicalised.

    A string citation is kept only when it resolves to a section that was
    actually read: strings like ``"Section '<title>': …"`` are free text,
    and keeping an unresolved one would count as a wrong claim."""
    from_string = False
    if isinstance(cit, dict):
        manual_id = str(cit.get("manual_id") or cit.get("manual") or "")
        raw_slug = str(cit.get("slug") or cit.get("section") or cit.get("node_id") or cit.get("section_slug") or "")
        quote = str(cit.get("quote") or cit.get("excerpt") or cit.get("text") or "")
    elif isinstance(cit, str) and cit.strip():
        match = _STRING_CITE_RE.match(cit)
        raw_slug, quote = (match.group("slug"), match.group("quote")) if match else (cit.strip(), "")
        raw_slug = re.sub(r"^\s*(?:section|章節)\s*", "", raw_slug, flags=re.IGNORECASE).strip(" '\"「」[]()")
        manual_id = ""
        from_string = True
    else:
        return None
    raw_slug = raw_slug.strip()
    if not raw_slug:
        return None
    if not manual_id:
        manual_id = _manual_for_slug(raw_slug, raw_sections)
    slug = _canonical_from_sections(manual_id, raw_slug, raw_sections)
    if from_string and slug not in {s.slug for s in raw_sections}:
        return None
    return Citation(manual_id=manual_id, slug=slug, quote=quote.strip())


def _force_not_found(messages: List[ModelMessage], raw_sections: List[SectionRef]) -> Tuple[str, List[Citation]]:
    last = last_assistant_text(messages)
    if last and "not found" in last.lower():
        summary, cits = parse_final_json(last, raw_sections)
        if summary.lower().startswith("not found"):
            return summary, cits
    return FORCED_DECLINE_SUMMARY, []


# ── Entry point used by the delegation tool ──────────────────────


async def run_manual_agent(
    core: DiagDeps,
    model: Model,
    inquiry: str,
    obd_context: Optional[str],
    parent_tool_call_id: Optional[str],
    usage: Any,
) -> ManualAgentResult:
    """Run the manual sub-agent for one inquiry inside a diagnosis run.

    Args:
        core: The main run's deps (tools read through it).
        model: The single model source.
        inquiry: Question from the main agent.
        obd_context: Optional OBD findings.
        parent_tool_call_id: The delegation call id (events nest under it).
        usage: The parent run's ``RunUsage`` (shared budget, FM-22).
    """
    state = ManualGuardState(inquiry_text=f"{inquiry}\n{obd_context or ''}", manuals=list(core.manuals))
    state.pin_from_vehicle(core.vehicle.manufacturer, core.vehicle.model)
    deps = SubAgentDeps(parent=core, parent_tool_call_id=parent_tool_call_id, state=state)
    prompt = build_manual_agent_user_message(inquiry, obd_context, vehicle=core.vehicle_label())
    from stf_v3.diagnosis.agent.model import model_settings as _ms
    from stf_v3.settings import settings as _settings

    nudged = False
    outcome: RunOutcome = await drive(
        MANUAL_AGENT, prompt, model=model, deps=deps, sink=core.events,
        parent_tool_call_id=parent_tool_call_id,
        usage_limits=make_limits(core.budgets.subagent_request_limit),
        model_settings=_ms(_settings, subagent=True, model=model),
        wall_clock_s=core.budgets.subagent_wall_clock_s,
        usage=usage,
        extra_capabilities=[ProcessHistory(_force_final_processor)],
    )
    if (outcome.stopped_reason == "complete" and state.final_answer is None
            and not has_final_json(outcome.output)):
        # PROD-09 (qwen on vLLM, thinking off): the model sometimes ends a
        # turn with planning prose instead of the JSON answer.  Nudge ONCE,
        # same history and budget; never loop.  PROD-10: also after the
        # force-final backstop (then ``final_answer`` is the one tool offered).
        nudged = True
        logger.info("manual_agent.nudged", tool_calls=len(deps.trace), forced=state.force_final)
        outcome = await drive(
            MANUAL_AGENT, MANUAL_NUDGE_PROMPT if state.force_final else NUDGE_FINAL_INSTRUCTION,
            model=model, deps=deps, sink=core.events,
            parent_tool_call_id=parent_tool_call_id,
            usage_limits=make_limits(core.budgets.subagent_request_limit),
            model_settings=_ms(_settings, subagent=True, model=model),
            wall_clock_s=core.budgets.subagent_wall_clock_s,
            usage=usage,
            message_history=outcome.messages,
            extra_capabilities=[ProcessHistory(_force_final_processor)],
        )
    raw_sections: List[SectionRef] = list(deps.raw_sections)
    if state.final_answer is not None:
        # Submitted on the forced turn; whatever text followed is ignored.
        summary, citations = final_from_output(state.final_answer, raw_sections)
        stopped = "complete"
    elif outcome.stopped_reason == "complete":
        summary, citations = final_from_output(outcome.output, raw_sections)
        empty = outcome.output is None or (isinstance(outcome.output, str) and not outcome.output.strip())
        if state.force_final and empty:
            summary, citations = _force_not_found(outcome.messages, raw_sections)
        stopped = "complete"
    else:
        summary = last_assistant_text(outcome.messages) or ""
        citations = []
        stopped = outcome.stopped_reason
        if not summary:
            summary = (
                "The manual sub-agent did not produce a final answer within its budget."
                if stopped in ("timeout", "budget") else FORCED_DECLINE_SUMMARY
            )
    logger.info(
        "manual_agent.done", stopped_reason=stopped, tool_calls=len(deps.trace),
        raw_sections=len(raw_sections), pinned=state.pinned.id if state.pinned else None,
        pin_source=state.pin_source, blocked_calls=state.blocked_calls, force_final=state.force_final,
    )
    return ManualAgentResult(
        summary=summary,
        citations=citations,
        raw_sections=raw_sections,
        tool_trace=[ToolTraceEntry(name=t.name, input=t.input, latency_ms=t.latency_ms, is_error=t.is_error)
                    for t in deps.trace],
        iterations=outcome.requests,
        total_tokens=outcome.usage.total_tokens,
        stopped_reason=stopped,  # type: ignore[arg-type]
        nudged=nudged,
    )


__all__ = ["FINAL_ANSWER_TOOL", "FORCED_TURN_PROMPT", "MANUAL_AGENT", "MANUAL_NUDGE_PROMPT", "MANUAL_TOOL_NAMES",
           "ManualFinalAnswer", "build_manual_agent", "citation_from_item", "final_from_output", "parse_final_json",
           "resolve_section_slug", "run_manual_agent"]

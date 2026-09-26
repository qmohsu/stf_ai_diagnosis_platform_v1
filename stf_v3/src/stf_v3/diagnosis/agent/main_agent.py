"""The main diagnosis agent (blueprint §7.2) and the run entry points.

``run_diagnosis`` executes one diagnosis for ``DiagDeps`` and returns a
``DiagnosisOutcome`` (report + events + messages + usage); every run
opens with ``session_start`` and closes with ``done`` (after
``diagnosis_done`` or ``error``).  ``stream_diagnosis`` yields the same
events as they happen — a single-use async iterator (FM-10).

The model ends with plain text (five-section markdown); the runtime packs
it into a ``DiagnosisReport`` with citations proven from the tool trace.
A gate (wall clock, requests, tool calls, tokens), a cancel, or a model
error produces a PARTIAL report instead of an exception (FM-7 / FM-14 /
FM-17).  After a wall-clock or usage gate the model gets ONE more turn
without investigation tools to write the report from the evidence it
already gathered; that turn offers exactly one tool, ``submit_report``,
because Qwen3.6 kept "calling tools" when none were offered (same lesson
as the manual sub-agent's ``final_answer``, PROD-10) and gathered
(PROD-11 server finding: the last narration line -- "Let me also check..."
-- used to be the whole "report"); if that turn fails too, the last
assistant text is used as before.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, Tool, ToolOutput
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.agent.context import last_assistant_text
from stf_v3.diagnosis.agent.deps import DiagDeps
from stf_v3.diagnosis.agent.events import AgentEvent, EventSink
from stf_v3.diagnosis.agent.model import describe, model_settings, source_label, source_of
from stf_v3.diagnosis.agent.prompts import SYSTEM_PROMPT, build_user_message
from stf_v3.diagnosis.agent.report import (
    TOOL_CALL_RESIDUE_LIMITATION,
    DiagnosisReport,
    extract_citations,
    has_tool_call_residue,
    strip_thinking_residue,
)
from stf_v3.diagnosis.agent.runner import RunOutcome, drive, make_limits
from stf_v3.diagnosis.tools.delegation import DELEGATION_TOOLS
from stf_v3.diagnosis.tools.manual_tools import MANUAL_TOOLS
from stf_v3.diagnosis.tools.obd_dtcs import OBD_DTC_TOOLS, collect_all_dtcs
from stf_v3.diagnosis.tools.obd_signals import OBD_SIGNAL_TOOLS

logger = structlog.get_logger(__name__)

MAIN_TOOLS = OBD_SIGNAL_TOOLS + OBD_DTC_TOOLS + MANUAL_TOOLS + DELEGATION_TOOLS
MAIN_TOOL_NAMES = tuple(fn.__name__ for fn in MAIN_TOOLS)

_PARTIAL_NOTE = {
    "timeout": "wall-clock budget exhausted",
    "budget": "usage budget exhausted",
    "cancelled": "run cancelled",
    "error": "model or runtime error",
}
# Gate sentence in the report's language: (kind, limit) from the runner.
_GATE_SENTENCE: Dict[str, str] = {
    "zh-TW": "診斷提前結束：已達{name}",
    "zh-CN": "诊断提前结束：已达{name}",
    "en": "run stopped early: reached the {name}",
}
_GATE_NAME: Dict[str, Dict[str, str]] = {
    "wall_clock": {"zh-TW": "時間上限 {n} 秒", "zh-CN": "时间上限 {n} 秒", "en": "wall-clock limit of {n} s"},
    "request": {"zh-TW": "模型請求次數上限 {n}", "zh-CN": "模型请求次数上限 {n}", "en": "request limit of {n}"},
    "tool_calls": {"zh-TW": "工具呼叫次數上限 {n}", "zh-CN": "工具调用次数上限 {n}", "en": "tool-call limit of {n}"},
    "total_tokens": {"zh-TW": "總 token 上限 {n}", "zh-CN": "总 token 上限 {n}", "en": "total-token limit of {n}"},
}
SUBMIT_REPORT_TOOL = "submit_report"
WRAPUP_INSTRUCTION = (
    "The investigation budget is used up: no investigation tools are available any more.  "
    "Write the final diagnosis report now, in the required format and language, using only "
    "the evidence already gathered above; say plainly what could not be checked.  Submit it "
    "by calling the `submit_report` tool with the whole report in `report_md`."
)
_WRAPUP_REASONS = ("timeout", "budget")


def gate_sentence(gate: Tuple[str, int], locale: str) -> str:
    """The gate that stopped a run, as one sentence in the report's language."""
    key = locale if locale in _GATE_SENTENCE else ("zh-TW" if locale.startswith("zh") else "en")
    kind, limit = gate
    names = _GATE_NAME.get(kind)
    name = names[key].format(n=f"{limit:,}") if names else f"{kind} {limit:,}"
    return _GATE_SENTENCE[key].format(name=name)


_TOOL_CALL_RESIDUE_HEADER = (
    "> **Partial report** — the model's tool-call text was not parsed by the "
    "endpoint, so the investigation did not run as intended. Treat the findings "
    "below as unverified.\n\n"
)


def thinking_chars(messages: Sequence[ModelMessage]) -> int:
    """Total characters of thinking parts in a run's messages (FM-18)."""
    total = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            total += sum(len(p.content or "") for p in msg.parts if isinstance(p, ThinkingPart))
    return total


def build_main_agent() -> Agent[DiagDeps, str]:
    """The main agent definition (model supplied at run time)."""
    tools = [Tool(fn, require_parameter_descriptions=True, docstring_format="google") for fn in MAIN_TOOLS]
    return Agent(None, deps_type=DiagDeps, output_type=str, instructions=SYSTEM_PROMPT,
                 tools=tools, retries=2, name="diagnosis_agent")


MAIN_AGENT: Agent[DiagDeps, str] = build_main_agent()


class WrapUpReport(BaseModel):
    """The wrap-up turn's one tool: the finished report."""

    report_md: str = Field(description="The complete diagnosis report (markdown), in the "
                                       "required format and language.")


# The wrap-up turn: same instructions, no investigation tools; the report
# comes back through ``submit_report`` (or as plain text).
WRAPUP_AGENT: Agent[DiagDeps, Any] = Agent(
    None, deps_type=DiagDeps, instructions=SYSTEM_PROMPT, retries=2, name="diagnosis_wrapup",
    output_type=[ToolOutput(WrapUpReport, name=SUBMIT_REPORT_TOOL,
                            description="Submit the finished diagnosis report."), str],
)


def _answered_history(messages: Sequence[ModelMessage]) -> List[ModelMessage]:
    """The history minus a trailing response whose tool calls never ran."""
    out = list(messages)
    while out and isinstance(out[-1], ModelResponse) and \
            any(isinstance(p, ToolCallPart) for p in out[-1].parts):
        out.pop()
    return out


def _wrapup_replies(messages: Sequence[ModelMessage]) -> List[ModelMessage]:
    """The messages after the wrap-up instruction (the wrap-up turn's own replies)."""
    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if isinstance(m, ModelRequest) and any(
                isinstance(p, UserPromptPart) and p.content == WRAPUP_INSTRUCTION for p in m.parts):
            return msgs[i + 1:]
    return []


async def _wrap_up(deps: DiagDeps, model: Model, outcome: RunOutcome,
                   settings: Any) -> Tuple[Optional[RunOutcome], str]:
    """One turn without investigation tools to write the report after a gate.

    Returns the wrap-up run (None when skipped) and the report text (empty
    when none was written).  When the turn fails, text the model wrote on
    it is still kept: on the server Qwen wrote the report AND called a
    tool in the same reply, which the runtime treats as "not finished".
    """
    wrap_s = float(getattr(settings, "agent_wrapup_s", 0) or 0)
    history = _answered_history(outcome.messages)
    if outcome.stopped_reason not in _WRAPUP_REASONS or wrap_s <= 0:
        return None, ""
    if not any(isinstance(m, ModelResponse) for m in history):
        return None, ""                   # the model never answered: nothing to summarise
    wrap = await drive(
        WRAPUP_AGENT, WRAPUP_INSTRUCTION, model=model, deps=deps, sink=deps.events,
        parent_tool_call_id=None, usage_limits=make_limits(3),
        model_settings=model_settings(settings, model=model), wall_clock_s=wrap_s,
        message_history=history, compact_threshold=deps.budgets.compact_threshold_tokens,
    )
    out = wrap.output if wrap.stopped_reason == "complete" else None
    text = out.report_md if isinstance(out, WrapUpReport) else (out if isinstance(out, str) else "")
    via = "tool" if isinstance(out, WrapUpReport) else "text"
    if not text.strip():
        text, via = last_assistant_text(_wrapup_replies(wrap.messages), max_chars=40_000), "fallback"
    logger.info("agent.wrapup", stopped_reason=wrap.stopped_reason, requests=wrap.requests,
                chars=len(text), via=via)
    return wrap, text


@dataclass
class DiagnosisOutcome:
    """Everything one run produced."""

    report: DiagnosisReport
    events: List[AgentEvent]
    messages: List[ModelMessage]
    usage: RunUsage
    stopped_reason: str
    error: Optional[str] = None
    elapsed_s: float = 0.0
    limitations: List[str] = field(default_factory=list)


def _case_context(deps: DiagDeps) -> tuple[str, str, str]:
    """(log line, time range, DTC codes) from the log row + reader."""
    log_line = f"{deps.log.format} ({deps.log.original_filename or deps.log.id})"
    if deps.log.recorded_start and deps.log.recorded_end:
        time_range = f"{deps.log.recorded_start.isoformat()} → {deps.log.recorded_end.isoformat()}"
    else:
        time_range = "unknown"
    try:
        dtcs = collect_all_dtcs(deps.load_log())
        codes = [d["code"] for d in dtcs if d["format"] == "standard"] or [d["code"] for d in dtcs]
        dtc_codes = ", ".join(dict.fromkeys(codes)) or "none"
    except Exception as exc:  # noqa: BLE001 — the log may be unreadable; the tools will say so
        logger.warning("agent.case_context_failed", error=type(exc).__name__)
        dtc_codes = "unknown (log could not be read)"
    return log_line, time_range, dtc_codes


def _dtcs_seen(deps: DiagDeps) -> List[str]:
    try:
        return [d["code"] for d in collect_all_dtcs(deps.load_log()) if d["format"] == "standard"]
    except Exception:  # noqa: BLE001
        return []


def build_report(deps: DiagDeps, outcome: RunOutcome, model: Model,
                 wrapup_text: Optional[str] = None) -> DiagnosisReport:
    """Pack a run outcome into the report object (partial when a gate hit).

    ``wrapup_text`` is the report written on the tool-less wrap-up turn
    after a gate; without it the last assistant text is used.
    """
    partial = outcome.stopped_reason != "complete"
    text = outcome.output if not partial else ""
    if partial:
        text = (wrapup_text or "").strip() or last_assistant_text(outcome.messages)
        note = _PARTIAL_NOTE.get(outcome.stopped_reason, outcome.stopped_reason)
        header = f"> **Partial report** — {note}. The findings below are what the investigation had established.\n\n"
        text = header + (text or "_No diagnosis text was produced before the run stopped._")
    # PROD-09 residue checks: thinking blocks are stripped (FM-1), unparsed
    # tool-call markup makes the report partial (FM-41).
    text, filter_hits, filter_removed = strip_thinking_residue(text or "")
    limitations = list(outcome.limitations)
    if outcome.gate is not None and limitations:
        limitations[0] = gate_sentence(outcome.gate, deps.locale)   # the runner puts it first
    if has_tool_call_residue(text):
        limitations.append(TOOL_CALL_RESIDUE_LIMITATION)
        if not partial:
            partial = True
            text = _TOOL_CALL_RESIDUE_HEADER + text
    dtc_trace = any(t.name == "list_dtcs" and not t.is_error for t in deps.trace)
    citations = extract_citations(
        text or "", deps.trace, [m.id for m in deps.manuals],
        dtcs_seen=_dtcs_seen(deps) if dtc_trace else [],
    )
    return DiagnosisReport(
        content_md=text or "",
        citations=citations,
        limitations=limitations,
        stopped_reason=outcome.stopped_reason,
        model=describe(model),
        total_tokens=outcome.usage.total_tokens,
        requests=outcome.requests,
        tool_calls=outcome.tool_calls,
        elapsed_s=outcome.elapsed_s,
        partial=partial,
        model_source=source_label(model),
        thinking_chars=thinking_chars(outcome.messages),
        filter_hits=filter_hits,
        filter_removed_chars=filter_removed,
    )


async def run_diagnosis(
    deps: DiagDeps,
    model: Model,
    *,
    message_history: Optional[Sequence[ModelMessage]] = None,
    settings: Any = None,
) -> DiagnosisOutcome:
    """Run one full diagnosis and return the outcome (never raises for gates).

    Args:
        deps: The run's dependency bundle (vehicle, log, manuals, budgets…).
        model: The single model source (``model.build_model``).
        message_history: Prior messages for a resumed conversation.
        settings: V3 settings (defaults to the process settings).
    """
    from stf_v3.settings import settings as _default_settings

    settings = settings or _default_settings
    sink = deps.events
    started = time.monotonic()
    src = source_of(model)
    sink.emit(ev.SESSION_START, {
        "vehicle": deps.vehicle_label(), "vehicle_id": str(deps.vehicle.id),
        "log_id": str(deps.log.id), "log_format": deps.log.format,
        "model": describe(model), "locale": deps.locale,
        "model_source": source_label(model),
        "profile": src.profile if src else "test",
        "endpoint": src.host if src else "test",
        "local": src.is_local if src else True,
        "manuals": len(deps.manuals), "tools": list(MAIN_TOOL_NAMES),
    })
    log_line, time_range, dtc_codes = _case_context(deps)
    prompt = build_user_message(deps.vehicle_label(), log_line, time_range, dtc_codes, deps.locale)
    outcome = await drive(
        MAIN_AGENT, prompt, model=model, deps=deps, sink=sink, parent_tool_call_id=None,
        usage_limits=make_limits(deps.budgets.request_limit, deps.budgets.tool_calls_limit,
                                 deps.budgets.total_tokens_limit),
        model_settings=model_settings(settings, model=model),
        wall_clock_s=deps.budgets.wall_clock_s,
        message_history=message_history,
        compact_threshold=deps.budgets.compact_threshold_tokens,
    )
    wrap, wrapup_text = await _wrap_up(deps, model, outcome, settings)
    if wrap is not None:
        outcome.usage = outcome.usage + wrap.usage
        outcome.requests += wrap.requests
        if wrapup_text.strip():
            outcome.messages = wrap.messages
    report = build_report(deps, outcome, model, wrapup_text)
    if outcome.stopped_reason == "complete":
        sink.emit(ev.DIAGNOSIS_DONE, {
            "chars": len(report.content_md), "citations": len(report.citations),
            "requests": outcome.requests, "tool_calls": outcome.tool_calls,
            "total_tokens": outcome.usage.total_tokens,
        })
    else:
        sink.emit(ev.ERROR, {
            "stopped_reason": outcome.stopped_reason, "message": outcome.error or outcome.stopped_reason,
            "partial_report_chars": len(report.content_md),
        })
        if report.content_md:
            sink.emit(ev.DIAGNOSIS_DONE, {
                "chars": len(report.content_md), "citations": len(report.citations),
                "partial": True, "requests": outcome.requests, "tool_calls": outcome.tool_calls,
            })
    sink.emit(ev.DONE, {"stopped_reason": outcome.stopped_reason,
                        "elapsed_s": round(time.monotonic() - started, 1),
                        "events": len(sink.events) + 1,
                        "thinking_chars": report.thinking_chars,
                        "filter_hits": report.filter_hits,
                        "filter_removed_chars": report.filter_removed_chars,
                        "partial": report.partial})
    await sink.drain()
    return DiagnosisOutcome(
        report=report, events=list(sink.events), messages=outcome.messages, usage=outcome.usage,
        stopped_reason=outcome.stopped_reason, error=outcome.error,
        elapsed_s=time.monotonic() - started, limitations=list(outcome.limitations),
    )


class DiagnosisStream:
    """Single-use async iterator of a run's events; ``outcome`` after exhaustion."""

    def __init__(self, deps: DiagDeps, model: Model, **kwargs: Any) -> None:
        if deps.events.events:
            raise ValueError("deps.events already holds events — use a fresh DiagDeps per run")
        self._deps = deps
        self._model = model
        self._kwargs = kwargs
        self._queue: "asyncio.Queue[Optional[AgentEvent]]" = asyncio.Queue()
        self._task: Optional["asyncio.Task[DiagnosisOutcome]"] = None
        self._consumed = False
        self.outcome: Optional[DiagnosisOutcome] = None

        async def _forward(event: AgentEvent) -> None:
            await self._queue.put(event)

        deps.events._callback = _forward  # noqa: SLF001 — sink is ours

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        if self._consumed:
            raise RuntimeError("a DiagnosisStream can be iterated only once")
        self._consumed = True
        return self._gen()

    async def _gen(self) -> AsyncIterator[AgentEvent]:
        async def _run() -> DiagnosisOutcome:
            try:
                return await run_diagnosis(self._deps, self._model, **self._kwargs)
            finally:
                await self._queue.put(None)

        self._task = asyncio.create_task(_run())
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
        self.outcome = await self._task


def stream_diagnosis(deps: DiagDeps, model: Model, **kwargs: Any) -> DiagnosisStream:
    """Events as they happen; read ``.outcome`` once the iterator is exhausted."""
    return DiagnosisStream(deps, model, **kwargs)


__all__ = ["MAIN_AGENT", "MAIN_TOOL_NAMES", "SUBMIT_REPORT_TOOL", "WRAPUP_AGENT", "WRAPUP_INSTRUCTION",
           "WrapUpReport", "DiagnosisOutcome",
           "DiagnosisStream", "build_main_agent", "build_report", "gate_sentence", "run_diagnosis",
           "stream_diagnosis", "EventSink"]

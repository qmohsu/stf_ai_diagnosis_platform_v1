"""OBD investigation sub-agent (V2 ``obd_agent.py`` on Pydantic AI).

Restricted to the 6 OBD tools; returns a structured ``OBDAgentResult``
parsed from the model's final JSON text (fence / prose tolerant).  Tool
outputs of the four quantitative tools are captured as data excerpts so
the main agent can quote the evidence.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import structlog
from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model

from stf_v3.diagnosis.agent.context import last_assistant_text
from stf_v3.diagnosis.agent.deps import DiagDeps, SubAgentDeps
from stf_v3.diagnosis.agent.guards import NUDGE_FINAL_INSTRUCTION
from stf_v3.diagnosis.agent.runner import RunOutcome, drive, make_limits
from stf_v3.diagnosis.agent.subagent_prompts import (
    OBD_AGENT_SYSTEM_PROMPT,
    build_obd_agent_user_message,
)
from stf_v3.diagnosis.agent.types import (
    DataExcerpt,
    DTCCitation,
    OBDAgentResult,
    SignalCitation,
    ToolTraceEntry,
)
from stf_v3.diagnosis.tools.obd_dtcs import OBD_DTC_TOOLS
from stf_v3.diagnosis.tools.obd_signals import OBD_SIGNAL_TOOLS

logger = structlog.get_logger(__name__)

_MAX_FINAL_SUMMARY_CHARS = 4000
_MAX_RAW_PAYLOAD_CHARS = 8000
_EXCERPT_TOOLS = {"get_signal_stats": "stats", "find_events": "events", "read_window": "window", "list_dtcs": "dtcs"}
_MARKDOWN_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL | re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

OBD_TOOLS = OBD_SIGNAL_TOOLS + OBD_DTC_TOOLS
OBD_TOOL_NAMES = frozenset(fn.__name__ for fn in OBD_TOOLS)


def build_obd_agent() -> Agent[SubAgentDeps, str]:
    """The OBD sub-agent definition (model supplied at run time)."""
    tools = [Tool(fn, require_parameter_descriptions=True, docstring_format="google") for fn in OBD_TOOLS]
    return Agent(None, deps_type=SubAgentDeps, output_type=str, instructions=OBD_AGENT_SYSTEM_PROMPT,
                 tools=tools, retries=2, name="obd_agent")


OBD_AGENT: Agent[SubAgentDeps, str] = build_obd_agent()


# ── Final JSON parsing (copied from V2) ──────────────────────────


def _strip_fence(content: str) -> str:
    match = _MARKDOWN_FENCE_RE.match(content.strip())
    return match.group(1).strip() if match else content.strip()


def _coerce_signal_citations(raw: Any) -> List[SignalCitation]:
    out: List[SignalCitation] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("signal"), str) or not entry["signal"]:
            continue
        tr = entry.get("time_range")
        time_range = (tr[0], tr[1]) if isinstance(tr, (list, tuple)) and len(tr) == 2 and all(isinstance(x, str) for x in tr) else None
        value = entry.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        try:
            out.append(SignalCitation(
                signal=entry["signal"], time_range=time_range, value=value,
                stat=entry["stat"] if isinstance(entry.get("stat"), str) else None,
                units=entry["units"] if isinstance(entry.get("units"), str) else None,
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_dtc_citations(raw: Any) -> List[DTCCitation]:
    out: List[DTCCitation] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        code, status = entry.get("code"), entry.get("status")
        if not isinstance(code, str) or status not in ("stored", "pending"):
            continue
        ecu = entry.get("ecu") if isinstance(entry.get("ecu"), str) else None
        try:
            out.append(DTCCitation(code=code, status=status, ecu=ecu))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_limitations(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(i) for i in raw if isinstance(i, (str, int, float))]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _coerce_extra_raw_data(raw: Any) -> List[DataExcerpt]:
    out: List[DataExcerpt] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind, payload = entry.get("kind"), entry.get("payload")
        if kind in ("stats", "events", "window", "dtcs") and isinstance(payload, dict):
            try:
                out.append(DataExcerpt(kind=kind, payload=payload))
            except Exception:  # noqa: BLE001
                continue
    return out


def has_final_json(text: Optional[str]) -> bool:
    """Whether the sub-agent's final text carries its JSON answer object."""
    return bool(text) and '"summary"' in text


def parse_final_json(content: Optional[str]) -> Tuple[str, List[SignalCitation], List[DTCCitation], List[DataExcerpt], List[str]]:
    """``(summary, signal_citations, dtc_citations, extra_raw_data, limitations)``."""
    if not content:
        return "The agent produced no final content.", [], [], [], []
    stripped = _strip_fence(content)
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
        logger.warning("obd_agent.final_json_parse_failed", chars=len(stripped))
        return stripped[:_MAX_FINAL_SUMMARY_CHARS], [], [], [], []
    return (
        str(payload.get("summary", ""))[:_MAX_FINAL_SUMMARY_CHARS],
        _coerce_signal_citations(payload.get("signal_citations")),
        _coerce_dtc_citations(payload.get("dtc_citations")),
        _coerce_extra_raw_data(payload.get("raw_data")),
        _coerce_limitations(payload.get("limitations")),
    )


def _excerpts(deps: SubAgentDeps) -> List[DataExcerpt]:
    out: List[DataExcerpt] = []
    for name, text in deps.excerpts:
        kind = _EXCERPT_TOOLS.get(name)
        if kind is None:
            continue
        if len(text) > _MAX_RAW_PAYLOAD_CHARS:
            text = text[:_MAX_RAW_PAYLOAD_CHARS] + f"\n[truncated — {len(text)} chars total]"
        out.append(DataExcerpt(kind=kind, payload={"text": text}))  # type: ignore[arg-type]
    return out


async def run_obd_agent(
    core: DiagDeps,
    model: Model,
    inquiry: str,
    parent_tool_call_id: Optional[str],
    usage: Any,
) -> OBDAgentResult:
    """Run the OBD sub-agent for one inquiry inside a diagnosis run."""
    deps = SubAgentDeps(parent=core, parent_tool_call_id=parent_tool_call_id)
    log_line = f"{core.log.format} ({core.log.original_filename or core.log.id})"
    prompt = build_obd_agent_user_message(inquiry, core.vehicle_label(), log_line)
    from stf_v3.diagnosis.agent.model import model_settings as _ms
    from stf_v3.settings import settings as _settings

    outcome: RunOutcome = await drive(
        OBD_AGENT, prompt, model=model, deps=deps, sink=core.events,
        parent_tool_call_id=parent_tool_call_id,
        usage_limits=make_limits(core.budgets.subagent_request_limit),
        model_settings=_ms(_settings, subagent=True, model=model),
        wall_clock_s=core.budgets.subagent_wall_clock_s,
        usage=usage,
    )
    if outcome.stopped_reason == "complete" and not has_final_json(outcome.output):
        # PROD-09: same one-shot nudge as the manual sub-agent (planning
        # prose returned as the final turn); tools stay available, never loops.
        logger.info("obd_agent.nudged", tool_calls=len(deps.trace))
        outcome = await drive(
            OBD_AGENT, NUDGE_FINAL_INSTRUCTION, model=model, deps=deps, sink=core.events,
            parent_tool_call_id=parent_tool_call_id,
            usage_limits=make_limits(core.budgets.subagent_request_limit),
            model_settings=_ms(_settings, subagent=True, model=model),
            wall_clock_s=core.budgets.subagent_wall_clock_s,
            usage=usage,
            message_history=outcome.messages,
        )
    if outcome.stopped_reason == "complete":
        summary, sig, dtc, extra, limitations = parse_final_json(outcome.output)
        stopped = "complete"
    else:
        summary = last_assistant_text(outcome.messages) or "The OBD sub-agent did not produce a final answer within its budget."
        sig, dtc, extra = [], [], []
        stopped = outcome.stopped_reason
        limitations = {
            "timeout": ["OBD sub-agent exceeded its time budget — investigation incomplete."],
            "budget": ["OBD sub-agent exhausted its request budget — investigation incomplete."],
        }.get(stopped, ["OBD sub-agent encountered an internal error — investigation incomplete."])
    raw_data = (_excerpts(deps) + extra)[:10]
    logger.info("obd_agent.done", stopped_reason=stopped, tool_calls=len(deps.trace),
                signal_citations=len(sig), dtc_citations=len(dtc))
    return OBDAgentResult(
        summary=summary, signal_citations=sig, dtc_citations=dtc, raw_data=raw_data,
        limitations=limitations,
        tool_trace=[ToolTraceEntry(name=t.name, input=t.input, latency_ms=t.latency_ms, is_error=t.is_error)
                    for t in deps.trace],
        iterations=outcome.requests, total_tokens=outcome.usage.total_tokens,
        stopped_reason=stopped,  # type: ignore[arg-type]
    )

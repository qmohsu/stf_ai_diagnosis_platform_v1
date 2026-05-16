"""OBD investigation sub-agent: restricted 6-tool ReAct loop.

Answers a single diagnostic inquiry against the raw OBD log by
calling the 6 OBD primitive tools.  Does NOT touch the service
manual — that's the manual sub-agent's job.  Returns a structured
``OBDAgentResult`` for the main agent (via ``delegate_to_obd_agent``)
or future eval suites.

Mirrors the established ``manual_agent.py`` template: own minimal
ReAct loop, no SSE/event-log persistence, structured JSON output
parser, timeout + max_iterations + max_tokens budgets.

Final output contract: the LLM stops calling tools and returns a
JSON object with ``summary``, ``signal_citations``,
``dtc_citations``, ``raw_data``, and ``limitations`` fields.  The
loop merges this with ``tool_trace`` data captured during
execution.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import structlog

from app.harness.deps import LLMClient
from app.harness.tool_registry import ToolRegistry
from app.harness_agents.obd_agent_prompts import (
    OBD_AGENT_SYSTEM_PROMPT,
    build_obd_agent_user_message,
)
from app.harness_agents.types import (
    DataExcerpt,
    DTCCitation,
    OBDAgentResult,
    SignalCitation,
    ToolCallTrace,
)
from app.harness_tools.obd_dtcs import LIST_DTCS_DEF, LOOKUP_DTC_DEF
from app.harness_tools.obd_signals import (
    FIND_EVENTS_DEF,
    GET_SIGNAL_STATS_DEF,
    LIST_SIGNALS_DEF,
    READ_WINDOW_DEF,
)

logger = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────


_DEFAULT_MODEL = "qwen3.5:27b-q8_0"
"""Local Ollama model served by the PolyU GPU server."""

_DEFAULT_MAX_ITERATIONS = 8
"""ReAct iteration cap, matching the design doc."""

_DEFAULT_MAX_TOKENS = 12_288
"""Per-call output token cap."""

_DEFAULT_TIMEOUT = 120.0
"""Wall-clock budget for the whole sub-agent run."""

_DEFAULT_TEMPERATURE = 0.2
"""Low but non-zero — deterministic enough for eval."""

_MAX_FINAL_SUMMARY_CHARS = 4000
"""Safety cap on the parsed ``summary`` length."""

_MAX_RAW_PAYLOAD_CHARS = 8000
"""Per-excerpt cap on ``DataExcerpt.payload`` serialised text.

Prevents the sub-agent from echoing the entire ``read_window``
output (potentially 50 KB) into the agent result.  The main agent
can still call the tools itself if it needs more data.
"""


# ── Configuration + deps ─────────────────────────────────────────


@dataclass(frozen=True)
class OBDAgentConfig:
    """Tunable knobs for the OBD sub-agent loop.

    Attributes:
        model: LLM identifier.
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
class OBDAgentDeps:
    """Injected dependencies for the OBD sub-agent.

    Attributes:
        llm_client: Any object satisfying ``LLMClient`` protocol.
        tool_registry: Must contain only the 6 OBD primitives.
            Use ``create_obd_agent_registry()`` to build one.
        config: Tunable knobs.
    """

    llm_client: LLMClient
    tool_registry: ToolRegistry
    config: OBDAgentConfig


def create_obd_agent_registry() -> ToolRegistry:
    """Build a ``ToolRegistry`` with only the 6 OBD primitives.

    Excludes manual tools and delegation tools by design — the OBD
    sub-agent's scope is investigation of the OBD log only.  No
    ``search_manual``, ``list_manuals``, ``get_manual_toc``, or
    ``read_manual_section`` access.

    The exclusion of ``delegate_to_obd_agent`` prevents recursion
    (a sub-agent cannot delegate to itself) — verified by the
    unit tests.

    Returns:
        A fresh ``ToolRegistry`` with exactly 6 tools registered.
    """
    registry = ToolRegistry()
    for tool_def in (
        LIST_SIGNALS_DEF,
        READ_WINDOW_DEF,
        GET_SIGNAL_STATS_DEF,
        FIND_EVENTS_DEF,
        LIST_DTCS_DEF,
        LOOKUP_DTC_DEF,
    ):
        registry.register(tool_def)
    return registry


# ── Helpers ──────────────────────────────────────────────────────


def _build_initial_messages(
    inquiry: str,
    session_id: str,
) -> List[Dict[str, Any]]:
    """Assemble the opening system + user messages."""
    return [
        {
            "role": "system",
            "content": OBD_AGENT_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": build_obd_agent_user_message(
                inquiry, session_id,
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


def _sanitize_tool_input_for_trace(
    input_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Produce a small, JSON-friendly copy of tool-call arguments.

    Strips any injected session fields and caps long string values
    so the tool trace stays report-friendly.
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


# ── Raw-data excerpt capture ─────────────────────────────────────


_EXCERPT_TOOLS = {
    "get_signal_stats": "stats",
    "find_events": "events",
    "read_window": "window",
    "list_dtcs": "dtcs",
}


def _truncate_payload(text: str) -> str:
    """Cap a payload text block at ``_MAX_RAW_PAYLOAD_CHARS``."""
    if len(text) <= _MAX_RAW_PAYLOAD_CHARS:
        return text
    return (
        text[:_MAX_RAW_PAYLOAD_CHARS]
        + f"\n[truncated — {len(text)} chars total]"
    )


def _build_data_excerpt(
    tool_name: str,
    output: Any,
) -> Optional[DataExcerpt]:
    """Convert a tool output into a ``DataExcerpt`` when relevant.

    Only the 4 quantitative tools produce excerpts the main agent
    might want to quote.  ``list_signals`` and ``lookup_dtc`` are
    discovery/decode aids whose output is reasoned about by the
    sub-agent but not usually re-quoted verbatim by the main
    agent — they're skipped to keep the result tight.

    Args:
        tool_name: Name of the tool that produced the output.
        output: ``ToolResult.output`` (string or block list).

    Returns:
        ``DataExcerpt`` ready to append to ``raw_data``, or
        ``None`` for tools that don't produce excerpts.
    """
    kind = _EXCERPT_TOOLS.get(tool_name)
    if kind is None:
        return None

    if isinstance(output, str):
        text = output
    elif isinstance(output, list):
        text = "\n".join(
            block.get("text", "")
            for block in output
            if block.get("type") == "text"
        )
    else:
        text = str(output)

    return DataExcerpt(
        kind=kind,  # type: ignore[arg-type]
        payload={"text": _truncate_payload(text)},
    )


# ── Final JSON parsing ───────────────────────────────────────────


_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip_markdown_fence(content: str) -> str:
    """Unwrap a ```json ... ``` code fence if present."""
    match = _MARKDOWN_FENCE_RE.match(content.strip())
    if match:
        return match.group(1).strip()
    return content.strip()


def _coerce_signal_citations(
    raw: Any,
) -> List[SignalCitation]:
    """Best-effort conversion of an LLM-output list into citations.

    Tolerates missing fields, wrong types (drops the entry), and
    extra keys (ignored).  Returns ``[]`` if ``raw`` isn't a list.
    """
    out: List[SignalCitation] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            signal = entry.get("signal")
            if not signal or not isinstance(signal, str):
                continue
            tr = entry.get("time_range")
            time_range = None
            if (
                isinstance(tr, (list, tuple))
                and len(tr) == 2
                and all(isinstance(x, str) for x in tr)
            ):
                time_range = (tr[0], tr[1])
            value = entry.get("value")
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = None
            out.append(SignalCitation(
                signal=signal,
                time_range=time_range,
                value=value,
                stat=(
                    entry["stat"]
                    if isinstance(entry.get("stat"), str)
                    else None
                ),
                units=(
                    entry["units"]
                    if isinstance(entry.get("units"), str)
                    else None
                ),
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_dtc_citations(raw: Any) -> List[DTCCitation]:
    """Best-effort conversion of an LLM-output list into DTC cites."""
    out: List[DTCCitation] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        code = entry.get("code")
        status = entry.get("status")
        if (
            not isinstance(code, str)
            or status not in ("stored", "pending")
        ):
            continue
        ecu = entry.get("ecu")
        if ecu is not None and not isinstance(ecu, str):
            ecu = None
        try:
            out.append(DTCCitation(
                code=code,
                status=status,  # type: ignore[arg-type]
                ecu=ecu,
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _coerce_limitations(raw: Any) -> List[str]:
    """Coerce an LLM-output limitations field into a list of strings."""
    if isinstance(raw, list):
        return [
            str(item) for item in raw
            if isinstance(item, (str, int, float))
        ]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _coerce_extra_raw_data(raw: Any) -> List[DataExcerpt]:
    """Honour any LLM-supplied raw_data entries (best-effort).

    The sub-agent loop builds raw_data automatically from tool
    outputs.  The LLM is also allowed to include its own
    structured excerpts — we accept those when shaped correctly.
    """
    out: List[DataExcerpt] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        payload = entry.get("payload")
        if (
            kind not in ("stats", "events", "window", "dtcs")
            or not isinstance(payload, dict)
        ):
            continue
        try:
            out.append(DataExcerpt(kind=kind, payload=payload))
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_final_json(
    content: Optional[str],
) -> Tuple[
    str,
    List[SignalCitation],
    List[DTCCitation],
    List[DataExcerpt],
    List[str],
]:
    """Parse the LLM's final answer into structured fields.

    Tolerates common formatting deviations: markdown fences,
    leading/trailing prose.  Falls back to the raw content as
    ``summary`` when parsing fails so the result is still useful.

    Args:
        content: The ``content`` field of the terminal LLM
            response.

    Returns:
        ``(summary, signal_citations, dtc_citations,
        extra_raw_data, limitations)`` tuple.
    """
    if not content:
        return (
            "The agent produced no final content.",
            [], [], [], [],
        )

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
        logger.warning(
            "obd_agent_final_json_parse_failed",
            preview=stripped[:200],
        )
        return (
            stripped[:_MAX_FINAL_SUMMARY_CHARS],
            [], [], [], [],
        )

    summary = str(
        payload.get("summary", "")
    )[:_MAX_FINAL_SUMMARY_CHARS]
    signal_cits = _coerce_signal_citations(
        payload.get("signal_citations"),
    )
    dtc_cits = _coerce_dtc_citations(
        payload.get("dtc_citations"),
    )
    extra_data = _coerce_extra_raw_data(payload.get("raw_data"))
    limitations = _coerce_limitations(payload.get("limitations"))

    return summary, signal_cits, dtc_cits, extra_data, limitations


def _extract_last_assistant_content(
    messages: List[Dict[str, Any]],
) -> str:
    """Return the last non-empty assistant content, else fallback."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:_MAX_FINAL_SUMMARY_CHARS]
    return (
        "The OBD agent did not produce a final answer within "
        "its budget."
    )


# ── Core loop ────────────────────────────────────────────────────


async def run_obd_agent(
    inquiry: str,
    session_id: str,
    deps: OBDAgentDeps,
) -> OBDAgentResult:
    """Run the OBD sub-agent against a diagnostic inquiry.

    Drives a restricted ReAct loop (6 OBD tools only) until the
    LLM returns a final JSON object or the iteration/timeout budget
    is exhausted.  Captures tool outputs as ``DataExcerpt`` entries
    so the main agent can quote the evidence.

    Args:
        inquiry: The investigation question.
        session_id: OBD analysis session UUID (auto-injected into
            every tool call as ``_session_id``).
        deps: Injected dependencies.

    Returns:
        A fully-populated ``OBDAgentResult``.
    """
    run_id = uuid.uuid4().hex[:8]
    cfg = deps.config
    tool_schemas = deps.tool_registry.schemas

    messages = _build_initial_messages(inquiry, session_id)
    tool_trace: List[ToolCallTrace] = []
    auto_raw_data: List[DataExcerpt] = []
    iterations = 0
    final_summary = ""
    final_signal_cits: List[SignalCitation] = []
    final_dtc_cits: List[DTCCitation] = []
    final_extra_data: List[DataExcerpt] = []
    final_limitations: List[str] = []
    stopped_reason: Literal[
        "complete", "max_iterations", "timeout", "error",
    ] = "max_iterations"

    logger.info(
        "obd_agent_start",
        run_id=run_id,
        model=cfg.model,
        max_iterations=cfg.max_iterations,
        session_id=session_id,
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
                        "obd_agent_llm_error",
                        run_id=run_id,
                        iteration=iterations,
                        exc_info=exc,
                    )
                    stopped_reason = "error"
                    break

                if (
                    response.finish_reason == "stop"
                    or not response.tool_calls
                ):
                    (
                        final_summary,
                        final_signal_cits,
                        final_dtc_cits,
                        final_extra_data,
                        final_limitations,
                    ) = _parse_final_json(response.content)
                    stopped_reason = "complete"
                    break

                messages.append(
                    _make_assistant_message(
                        response.content, response.tool_calls,
                    ),
                )

                for tc in response.tool_calls:
                    args = _parse_tool_arguments(tc.arguments)

                    if "_parse_error" in args:
                        error_msg = (
                            f"Error: could not parse tool "
                            f"arguments — {args['_parse_error']}"
                        )
                        tool_trace.append(ToolCallTrace(
                            name=tc.name,
                            input={
                                "_raw": tc.arguments[:200],
                            },
                            latency_ms=0.0,
                            is_error=True,
                        ))
                        messages.append(
                            _make_tool_message(
                                tc.id, error_msg,
                            ),
                        )
                        continue

                    # Inject _session_id for the OBD tools.
                    args["_session_id"] = session_id

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

                    if not result.is_error:
                        excerpt = _build_data_excerpt(
                            tc.name, result.output,
                        )
                        if excerpt is not None:
                            auto_raw_data.append(excerpt)

                    messages.append(
                        _make_tool_message(
                            tc.id, result.output,
                        ),
                    )

                iterations += 1

            else:
                stopped_reason = "max_iterations"

    except TimeoutError:
        logger.warning(
            "obd_agent_timeout",
            run_id=run_id,
            iteration=iterations,
            timeout_seconds=cfg.timeout_seconds,
        )
        stopped_reason = "timeout"

    reported_iterations = iterations + (
        1 if stopped_reason == "complete" else 0
    )

    if stopped_reason != "complete" and not final_summary:
        final_summary = _extract_last_assistant_content(messages)
        if stopped_reason == "timeout":
            final_limitations.append(
                "OBD sub-agent exceeded its time budget — "
                "investigation incomplete."
            )
        elif stopped_reason == "max_iterations":
            final_limitations.append(
                "OBD sub-agent exhausted its iteration budget "
                "— investigation incomplete."
            )
        elif stopped_reason == "error":
            final_limitations.append(
                "OBD sub-agent encountered an internal error "
                "— investigation incomplete."
            )

    # Merge auto-captured raw_data + LLM-supplied entries.  Cap
    # total size to avoid main-agent context bloat.
    combined_raw_data = auto_raw_data + final_extra_data
    if len(combined_raw_data) > 10:
        combined_raw_data = combined_raw_data[:10]

    logger.info(
        "obd_agent_done",
        run_id=run_id,
        iterations=reported_iterations,
        stopped_reason=stopped_reason,
        tool_calls=len(tool_trace),
        signal_citations=len(final_signal_cits),
        dtc_citations=len(final_dtc_cits),
    )

    return OBDAgentResult(
        summary=final_summary,
        signal_citations=final_signal_cits,
        dtc_citations=final_dtc_cits,
        raw_data=combined_raw_data,
        limitations=final_limitations,
        tool_trace=tool_trace,
        iterations=reported_iterations,
        total_tokens=0,  # Not tracked — matches manual_agent.
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )

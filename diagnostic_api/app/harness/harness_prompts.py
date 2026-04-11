"""System prompt and user message builders for the agent loop.

The system prompt is dynamic — tool names are injected so the LLM
knows exactly which tools are available in this session.
"""

from __future__ import annotations

from typing import Any, Dict, List


_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert vehicle diagnostician with access to diagnostic \
tools. Your task is to investigate OBD-II data, identify faults, \
and produce a thorough diagnosis.

## Available tools

{tool_list}

## Investigation strategy

1. Start by reviewing the session context to understand the \
vehicle, DTC codes, and symptom summary.
2. Use `detect_anomalies` and `get_pid_statistics` to examine \
sensor data patterns and identify abnormalities.
3. Use `generate_clues` to obtain rule-based diagnostic hints.
4. Use `search_manual` to look up relevant service manual \
sections for the vehicle and suspected faults.
5. Use `refine_search` if initial manual results are \
insufficient — try different queries and exclude already-seen \
document IDs.
6. Use `search_case_history` to check whether similar DTC \
patterns have been diagnosed before.
7. When you have gathered enough evidence, stop calling tools \
and produce your final diagnosis as plain text.

## Output rules

- When you are ready to conclude, respond with your diagnosis \
text directly — do NOT call any tools.
- Structure your diagnosis with: fault identification, root \
cause analysis, supporting evidence (cite tool results), \
recommended actions, and limitations.
- Never invent data. If a tool returned an error or no results, \
say so explicitly.
- Cite sources: reference manual sections by doc_id (e.g. \
MWS150-A#3.2) and tool names that provided evidence.
- State limitations: if data is missing or inconclusive, say so.
- Respond in the same language as the user message.\
"""


def build_system_prompt(
    tool_names: List[str],
) -> str:
    """Assemble the dynamic system prompt.

    Args:
        tool_names: Sorted list of registered tool names.

    Returns:
        Complete system prompt string with tool list injected.
    """
    tool_list = "\n".join(
        f"- `{name}`" for name in tool_names
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        tool_list=tool_list,
    )


_USER_MESSAGE_TEMPLATE = """\
Diagnose the following OBD-II session.

Vehicle: {vehicle_id}
Time range: {time_range}
DTC codes: {dtc_codes}
PID summary: {pid_summary}
Anomaly events: {anomaly_events}
Diagnostic clues: {diagnostic_clues}

Session ID (use this when calling tools): {session_id}\
"""


_LOCALE_LABELS: Dict[str, str] = {
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
}


def build_user_message(
    session_id: str,
    parsed_summary: Dict[str, Any],
    locale: str = "en",
) -> str:
    """Format the initial user message from a parsed summary.

    Args:
        session_id: OBD analysis session UUID string.
        parsed_summary: The session's ``parsed_summary_payload``
            dict with keys like ``vehicle_id``, ``dtc_codes``, etc.
        locale: Response language code (``"en"``, ``"zh-CN"``,
            ``"zh-TW"``).  When not English, a language
            instruction is appended.

    Returns:
        Formatted user message string.
    """
    msg = _USER_MESSAGE_TEMPLATE.format(
        vehicle_id=parsed_summary.get(
            "vehicle_id", "unknown"
        ),
        time_range=parsed_summary.get(
            "time_range", "unknown"
        ),
        dtc_codes=parsed_summary.get("dtc_codes", "none"),
        pid_summary=parsed_summary.get("pid_summary", "N/A"),
        anomaly_events=parsed_summary.get(
            "anomaly_events", "none"
        ),
        diagnostic_clues=parsed_summary.get(
            "diagnostic_clues", "none"
        ),
        session_id=session_id,
    )
    label = _LOCALE_LABELS.get(locale)
    if label:
        msg += f"\n\nPlease respond in {label}."
    return msg

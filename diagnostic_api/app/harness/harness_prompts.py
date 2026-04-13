"""System prompt and user message builders for the agent loop.

The system prompt describes the agent's role and available tools.
The user message provides only the basic case context (vehicle,
time range, DTCs) — the agent discovers everything else by
calling tools.
"""

from __future__ import annotations

from typing import Any, Dict, List


_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert vehicle diagnostician. Your task is to \
investigate OBD-II data, consult service manuals, and produce \
a thorough diagnosis.

## Your tools

- `read_obd_data` — Read OBD-II sensor data from the vehicle's \
log file. Call with no arguments first to see available signals, \
time range, and DTC codes. Then query specific signals to \
investigate patterns and anomalies.

- `search_manual` — Search vehicle service manuals for \
diagnostic procedures, specifications, and repair instructions. \
Use DTC codes, symptom descriptions, or component names as \
search queries. Returns short text chunks ranked by relevance.

- `list_manuals` — List available service manuals. Use to \
discover what manuals exist for a vehicle model before \
reading them.

- `get_manual_toc` — Get the table of contents (heading \
structure) of a specific manual. Returns section titles with \
slugs and a DTC quick-reference index. Use this to find the \
right section before calling read_manual_section.

- `read_manual_section` — Read a specific section from a \
manual by heading slug or title. Returns the full section \
text with embedded images (diagrams, wiring schematics). Use \
get_manual_toc first to find section slugs.

## How to investigate

Investigate like an expert mechanic: review the data, form \
hypotheses about what is wrong, verify against manual \
specifications, and refine your diagnosis. Call tools in \
whatever order makes sense for the case — there is no fixed \
sequence.

## When you are done

Stop calling tools and produce your final diagnosis as plain \
text. Structure it with:
- **Fault identification** — what is wrong
- **Root cause analysis** — why it happened
- **Supporting evidence** — cite specific data values and \
manual references (e.g. MWS150-A#3.2)
- **Recommended actions** — what to do
- **Limitations** — what data is missing or inconclusive\
"""


def build_system_prompt(
    tool_names: List[str],
) -> str:
    """Assemble the dynamic system prompt.

    Args:
        tool_names: Sorted list of registered tool names
            (currently unused — tool descriptions are inline
            in the template, but kept for forward compatibility).

    Returns:
        Complete system prompt string.
    """
    return _SYSTEM_PROMPT_TEMPLATE


_USER_MESSAGE_TEMPLATE = """\
Diagnose the following OBD-II case.

Vehicle: {vehicle_id}
Time range: {time_range}
DTC codes: {dtc_codes}

Use your tools to investigate the OBD data and service manuals.\
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

    Only includes basic case context (vehicle, time range, DTCs).
    The agent discovers PID patterns, anomalies, and clues by
    calling ``read_obd_data`` itself.

    Args:
        session_id: OBD analysis session UUID string (no longer
            included in the message — auto-injected into tool
            calls by the loop).
        parsed_summary: The session's ``parsed_summary_payload``
            dict with keys like ``vehicle_id``, ``dtc_codes``.
        locale: Response language code (``"en"``, ``"zh-CN"``,
            ``"zh-TW"``).

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
    )
    label = _LOCALE_LABELS.get(locale)
    if label:
        msg += f"\n\nPlease respond in {label}."
    return msg

"""OBD diagnostic tool wrappers for the harness agent loop.

Wraps V1 pipeline results (statistics, anomalies, clues) stored in
each session's ``result_payload`` JSONB column.  Returns text
summaries only — never raw sensor arrays (privacy invariant).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.db.session import SessionLocal
from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import (
    DetectAnomaliesInput,
    SessionInput,
)
import structlog

from app.models_db import OBDAnalysisSession

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Shared helper
# ------------------------------------------------------------------

def _get_session(session_id: str) -> Any:
    """Load an OBDAnalysisSession by UUID.

    Creates its own DB session (same pattern as
    ``app.rag.retrieve._sync_query``).

    Args:
        session_id: UUID string of the OBD analysis session.

    Returns:
        The ``OBDAnalysisSession`` ORM object.

    Raises:
        ValueError: If the UUID is malformed, session not found,
            or session has no result_payload.
    """
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"Invalid session_id format: {session_id}"
        ) from exc

    db = SessionLocal()
    try:
        session = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == sid)
            .first()
        )
        if session is None:
            raise ValueError(
                f"Session not found: {session_id}"
            )
        if session.result_payload is None:
            raise ValueError(
                f"Session {session_id} has no analysis results "
                f"(status={session.status})."
            )
        # Eagerly read attributes before closing the session.
        _ = session.result_payload
        _ = session.parsed_summary_payload
        _ = session.vehicle_id
        return session
    finally:
        db.close()


# ------------------------------------------------------------------
# Tool: get_pid_statistics
# ------------------------------------------------------------------

def _fmt_stat(name: str, s: dict, unit: str) -> str:
    """Format a single signal's statistics as one line.

    Args:
        name: Signal semantic name.
        s: Dict of stat fields (mean, std, min, max, p5 .. p95).
        unit: Unit string from column_units.

    Returns:
        Formatted line, e.g.
        ``"engine_rpm (rpm): mean=2100 std=340 min=780 max=4200"``.
    """
    def v(key: str) -> str:
        val = s.get(key)
        if val is None:
            return "N/A"
        if isinstance(val, float):
            return f"{val:.2f}"
        return str(val)

    parts = [
        f"mean={v('mean')}",
        f"std={v('std')}",
        f"min={v('min')}",
        f"max={v('max')}",
        f"p5={v('p5')}",
        f"p25={v('p25')}",
        f"p50={v('p50')}",
        f"p75={v('p75')}",
        f"p95={v('p95')}",
    ]
    label = f"{name} ({unit})" if unit else name
    return f"{label}: {' '.join(parts)}"


async def get_pid_statistics(
    input_data: Dict[str, Any],
) -> str:
    """Return per-signal statistics from the session's result.

    Args:
        input_data: Must contain ``session_id`` (UUID string).

    Returns:
        Multi-line text with per-signal stats.
    """
    session = _get_session(input_data["session_id"])
    payload = session.result_payload

    val_stats = payload.get("value_statistics", {})
    stats = val_stats.get("stats", {})
    units = val_stats.get("column_units", {})

    if not stats:
        return "No PID statistics available for this session."

    lines: List[str] = []
    for name, fields in sorted(stats.items()):
        unit = units.get(name, "")
        lines.append(_fmt_stat(name, fields, unit))

    return "\n".join(lines)


# ------------------------------------------------------------------
# Tool: detect_anomalies
# ------------------------------------------------------------------

def _fmt_anomaly(ev: dict) -> str:
    """Format one anomaly event as a single line.

    Args:
        ev: Anomaly event dict from result_payload.

    Returns:
        Formatted line, e.g.
        ``"[HIGH] engine_rpm: sudden drop (cruise, score=0.87)"``.
    """
    severity = ev.get("severity", "unknown").upper()
    signals = ", ".join(ev.get("signals", []))
    pattern = ev.get("pattern", "unknown pattern")
    context = ev.get("context", "unknown")
    score = ev.get("score", 0.0)
    tw = ev.get("time_window", [])
    time_str = " to ".join(tw) if tw else "unknown"

    return (
        f"[{severity}] {signals}: {pattern} "
        f"at {time_str} ({context}, score={score:.2f})"
    )


async def detect_anomalies(
    input_data: Dict[str, Any],
) -> str:
    """Return anomaly events from the session's result.

    Args:
        input_data: Must contain ``session_id``.  Optional
            ``focus_signals`` list to filter events.

    Returns:
        Multi-line text listing anomaly events, or a message
        if none were detected.
    """
    session = _get_session(input_data["session_id"])
    events: List[dict] = session.result_payload.get(
        "anomaly_events", [],
    )
    focus: Optional[List[str]] = input_data.get(
        "focus_signals",
    )

    if focus:
        focus_set = set(focus)
        events = [
            ev for ev in events
            if focus_set & set(ev.get("signals", []))
        ]

    if not events:
        return "No anomaly events detected."

    return "\n".join(_fmt_anomaly(ev) for ev in events)


# ------------------------------------------------------------------
# Tool: generate_clues
# ------------------------------------------------------------------

def _fmt_clue(c: dict) -> str:
    """Format one diagnostic clue as a single line.

    Args:
        c: Clue dict from result_payload.clue_details.

    Returns:
        Formatted line, e.g.
        ``"STAT_001 [statistical/warning] Engine RPM zero "
        ``| Evidence: engine_rpm.mean=0"``.
    """
    rule_id = c.get("rule_id", "???")
    category = c.get("category", "unknown")
    severity = c.get("severity", "info")
    clue = c.get("clue", "")
    evidence = ", ".join(c.get("evidence", []))

    return (
        f"{rule_id} [{category}/{severity}] {clue}"
        f" | Evidence: {evidence}"
    )


async def generate_clues(
    input_data: Dict[str, Any],
) -> str:
    """Return diagnostic clues from the session's result.

    Args:
        input_data: Must contain ``session_id``.

    Returns:
        Multi-line text listing clues, or a message if none.
    """
    session = _get_session(input_data["session_id"])
    clues: List[dict] = session.result_payload.get(
        "clue_details", [],
    )

    if not clues:
        return "No diagnostic clues generated."

    return "\n".join(_fmt_clue(c) for c in clues)


# ------------------------------------------------------------------
# Tool: get_session_context
# ------------------------------------------------------------------

async def get_session_context(
    input_data: Dict[str, Any],
) -> str:
    """Return the session's parsed summary as formatted text.

    Args:
        input_data: Must contain ``session_id``.

    Returns:
        Labelled text block with vehicle, time range, DTCs, etc.
    """
    session = _get_session(input_data["session_id"])
    ps = session.parsed_summary_payload

    if not ps:
        return (
            f"Session {input_data['session_id']} has no "
            f"parsed summary."
        )

    lines = [
        f"Vehicle: {ps.get('vehicle_id', 'unknown')}",
        f"Time range: {ps.get('time_range', 'unknown')}",
        f"DTC codes: {ps.get('dtc_codes', 'none')}",
        f"PID summary: {ps.get('pid_summary', 'N/A')}",
        f"Anomaly events: {ps.get('anomaly_events', 'none')}",
        f"Diagnostic clues: "
        f"{ps.get('diagnostic_clues', 'none')}",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# ToolDefinition exports
# ------------------------------------------------------------------

GET_PID_STATISTICS_DEF = ToolDefinition(
    name="get_pid_statistics",
    description=(
        "Retrieve per-signal statistics (mean, std, min, max, "
        "percentiles) for the OBD session's PID data. Returns a "
        "text summary, never raw arrays."
    ),
    input_schema=SessionInput.model_json_schema(),
    handler=get_pid_statistics,
    input_model=SessionInput,
    is_read_only=True,
)

DETECT_ANOMALIES_DEF = ToolDefinition(
    name="detect_anomalies",
    description=(
        "Run anomaly detection on the OBD session data. Returns "
        "text descriptions of detected anomaly events with "
        "severity, pattern, and time window."
    ),
    input_schema=DetectAnomaliesInput.model_json_schema(),
    handler=detect_anomalies,
    input_model=DetectAnomaliesInput,
    is_read_only=True,
)

GENERATE_CLUES_DEF = ToolDefinition(
    name="generate_clues",
    description=(
        "Generate diagnostic clues using rule-based inference on "
        "session statistics and anomalies. Returns clue text with "
        "rule ID, category, and evidence."
    ),
    input_schema=SessionInput.model_json_schema(),
    handler=generate_clues,
    input_model=SessionInput,
    is_read_only=True,
)

GET_SESSION_CONTEXT_DEF = ToolDefinition(
    name="get_session_context",
    description=(
        "Retrieve the current OBD session's parsed summary "
        "including vehicle ID, time range, DTC codes, PID "
        "summary, anomaly events, and diagnostic clues. Call "
        "this first to understand the diagnostic case."
    ),
    input_schema=SessionInput.model_json_schema(),
    handler=get_session_context,
    input_model=SessionInput,
    is_read_only=True,
)

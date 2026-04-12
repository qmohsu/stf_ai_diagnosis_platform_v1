"""OBD data reader tool for the harness agent loop.

Provides parameterized access to the raw OBD-II log file stored
on disk.  The agent calls this to investigate sensor data directly
— no pre-computed statistics or rule-based analysis.

Two modes:
- **Overview** (no ``signals``): returns available PIDs, time range,
  DTCs, and sample count so the agent knows what data exists.
- **Signal query** (with ``signals``): returns a filtered table of
  PID values, optionally windowed by time and downsampled.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from app.config import settings
from app.db.session import SessionLocal
from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import ReadOBDDataInput
from app.models_db import OBDAnalysisSession
from obd_agent.log_parser import (
    _PID_UNITS,
    _SKIP_COLUMNS,
    _parse_dtc_list,
    parse_log_file,
)
from obd_agent.time_series_normalizer import (
    _PID_SEMANTIC_NAMES,
)

logger = structlog.get_logger(__name__)

_MAX_ROWS = 50  # Default row limit for signal queries.

# Reverse map: semantic_name -> PID_NAME for flexible lookups.
_SEMANTIC_TO_PID: Dict[str, str] = {
    v: k for k, v in _PID_SEMANTIC_NAMES.items()
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _resolve_log_path(session_id: str) -> Path:
    """Resolve the raw OBD log file path from a session UUID.

    Args:
        session_id: UUID string of the OBD analysis session.

    Returns:
        Absolute path to the raw log file.

    Raises:
        ValueError: If session is not found or has no raw file.
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
        raw_path = session.raw_input_file_path
        if not raw_path:
            raise ValueError(
                f"Session {session_id} has no raw OBD log "
                f"file on disk."
            )
        return Path(settings.obd_log_storage_path) / raw_path
    finally:
        db.close()


def _normalize_signal_name(name: str) -> Optional[str]:
    """Resolve a signal name to its PID name.

    Accepts both PID names (``RPM``) and semantic names
    (``engine_rpm``).  Returns ``None`` if the name is
    unrecognized.

    Args:
        name: Signal name to resolve.

    Returns:
        Canonical PID name or None.
    """
    upper = name.upper()
    if upper in _PID_UNITS:
        return upper
    # Try semantic name → PID mapping.
    pid = _SEMANTIC_TO_PID.get(name.lower())
    if pid is not None:
        return pid
    return None


def _extract_dtcs(rows: List[Dict[str, str]]) -> List[str]:
    """Extract unique DTC codes from parsed log rows.

    Args:
        rows: Parsed row dicts from ``parse_log_file()``.

    Returns:
        Deduplicated sorted list of DTC code strings.
    """
    seen: set = set()
    for row in rows:
        for col in ("GET_DTC", "GET_CURRENT_DTC"):
            raw = row.get(col, "")
            for code, _desc in _parse_dtc_list(raw):
                seen.add(code.upper())
    return sorted(seen)


def _available_pids(
    rows: List[Dict[str, str]],
) -> List[str]:
    """Return PID names present in the data as columns.

    Args:
        rows: Parsed row dicts (at least one required).

    Returns:
        Sorted list of PID column names that exist in the data
        and are recognized numeric PIDs.
    """
    if not rows:
        return []
    return sorted(
        col for col in rows[0]
        if col not in _SKIP_COLUMNS
        and col in _PID_UNITS
    )


def _format_overview(
    rows: List[Dict[str, str]],
    log_path: Path,
) -> str:
    """Format an overview of the OBD data.

    Args:
        rows: All parsed row dicts.
        log_path: Path to the log file (for metadata).

    Returns:
        Multi-line overview text.
    """
    pids = _available_pids(rows)
    dtcs = _extract_dtcs(rows)

    # Time range from first/last row.
    ts_first = rows[0].get("Timestamp", "unknown")
    ts_last = rows[-1].get("Timestamp", "unknown")

    # Duration estimate.
    try:
        t0 = datetime.strptime(ts_first, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(ts_last, "%Y-%m-%d %H:%M:%S")
        duration = int((t1 - t0).total_seconds())
        duration_str = f"{duration}s"
    except ValueError:
        duration_str = "unknown"

    pid_lines = []
    for pid in pids:
        unit = _PID_UNITS.get(pid, "")
        pid_lines.append(f"  {pid} ({unit})")

    lines = [
        "=== OBD Data Overview ===",
        f"Time range: {ts_first} to {ts_last} "
        f"({duration_str})",
        f"Samples: {len(rows)} rows",
        f"DTC codes: {', '.join(dtcs) if dtcs else 'none'}",
        "",
        f"Available signals ({len(pids)} numeric PIDs):",
    ]
    lines.extend(pid_lines)
    return "\n".join(lines)


def _format_signal_table(
    rows: List[Dict[str, str]],
    pids: List[str],
    total_rows: int,
    truncated: bool,
) -> str:
    """Format a table of signal values.

    Args:
        rows: Filtered and limited row dicts.
        pids: PID columns to include.
        total_rows: Total rows before limit.
        truncated: Whether rows were truncated.

    Returns:
        Tab-separated table with header.
    """
    # Build header.
    header_cols = ["Timestamp"] + pids
    header = "\t".join(header_cols)

    # Build data lines.
    data_lines = []
    for row in rows:
        vals = [row.get("Timestamp", "")]
        for pid in pids:
            raw = row.get(pid, "")
            # Try to format as float for consistency.
            try:
                vals.append(f"{float(raw):.2f}")
            except (ValueError, TypeError):
                vals.append(raw)
        data_lines.append("\t".join(vals))

    result = header + "\n" + "\n".join(data_lines)
    if truncated:
        result += (
            f"\n\n[Showing {len(rows)} of {total_rows}"
            f" rows. Use start_time/end_time to narrow the"
            f" window, or every_nth to downsample.]"
        )
    return result


# ------------------------------------------------------------------
# Tool handler
# ------------------------------------------------------------------


async def read_obd_data(
    input_data: Dict[str, Any],
) -> str:
    """Read OBD data from the session's raw log file.

    If ``signals`` is omitted, returns an overview of available
    data.  If ``signals`` is provided, returns a filtered table
    of PID values.

    Args:
        input_data: Tool input with optional ``signals``,
            ``start_time``, ``end_time``, ``every_nth``.
            ``_session_id`` is injected by the loop.

    Returns:
        Formatted text: overview or signal table.
    """
    session_id = input_data["_session_id"]
    log_path = _resolve_log_path(session_id)

    if not log_path.exists():
        return (
            f"Raw OBD log file not found at {log_path}. "
            f"The data may not have been stored on disk."
        )

    rows = parse_log_file(log_path)
    if not rows:
        return "OBD log file is empty (no data rows)."

    # ---- Overview mode ----
    signals = input_data.get("signals")
    if not signals:
        return _format_overview(rows, log_path)

    # ---- Signal query mode ----
    # Resolve signal names to PID names.
    resolved_pids: List[str] = []
    unknown: List[str] = []
    for sig in signals:
        pid = _normalize_signal_name(sig)
        if pid is not None:
            resolved_pids.append(pid)
        else:
            unknown.append(sig)

    if not resolved_pids:
        avail = _available_pids(rows)
        return (
            f"None of the requested signals were recognized: "
            f"{unknown}. "
            f"Available PIDs: {', '.join(avail)}"
        )

    # Check which resolved PIDs actually exist in the data.
    data_cols = set(rows[0].keys()) if rows else set()
    present = [p for p in resolved_pids if p in data_cols]
    missing = [p for p in resolved_pids if p not in data_cols]

    if not present:
        avail = _available_pids(rows)
        return (
            f"Requested PIDs {resolved_pids} are not present "
            f"in this log. "
            f"Available: {', '.join(avail)}"
        )

    # Apply time range filter.
    start_time = input_data.get("start_time")
    end_time = input_data.get("end_time")
    filtered = rows
    if start_time or end_time:
        filtered = _filter_by_time(
            filtered, start_time, end_time,
        )

    # Apply downsampling.
    every_nth = input_data.get("every_nth")
    if every_nth and every_nth > 1:
        filtered = filtered[::every_nth]

    # Limit output rows.
    total = len(filtered)
    truncated = total > _MAX_ROWS
    limited = filtered[:_MAX_ROWS]

    result = _format_signal_table(
        limited, present, total, truncated,
    )

    # Append notes about unknown/missing signals.
    notes: List[str] = []
    if unknown:
        notes.append(
            f"Unrecognized signals (ignored): {unknown}"
        )
    if missing:
        notes.append(
            f"Signals not in this log (ignored): {missing}"
        )
    if notes:
        result += "\n\nNote: " + "; ".join(notes)

    return result


def _filter_by_time(
    rows: List[Dict[str, str]],
    start_time: Optional[str],
    end_time: Optional[str],
) -> List[Dict[str, str]]:
    """Filter rows to a time window.

    Args:
        rows: Parsed row dicts.
        start_time: ISO timestamp string (inclusive lower).
        end_time: ISO timestamp string (inclusive upper).

    Returns:
        Filtered list of row dicts.
    """
    result = []
    for row in rows:
        ts_raw = row.get("Timestamp", "")
        try:
            ts = datetime.strptime(
                ts_raw, "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            continue

        if start_time:
            try:
                t0 = datetime.fromisoformat(start_time)
                if ts < t0:
                    continue
            except ValueError:
                pass  # Ignore bad start_time, include row.

        if end_time:
            try:
                t1 = datetime.fromisoformat(end_time)
                if ts > t1:
                    continue
            except ValueError:
                pass

        result.append(row)
    return result


# ------------------------------------------------------------------
# ToolDefinition export
# ------------------------------------------------------------------


READ_OBD_DATA_DEF = ToolDefinition(
    name="read_obd_data",
    description=(
        "Read OBD-II sensor data from the vehicle's log file. "
        "Call with no arguments to get an overview of available "
        "signals, time range, and DTC codes. "
        "Call with specific signal names to read their values "
        "over time. "
        "Accepts both PID names (RPM, COOLANT_TEMP) and "
        "semantic names (engine_rpm, coolant_temperature). "
        "Use start_time/end_time to focus on a specific "
        "time window. Use every_nth to downsample long "
        "recordings."
    ),
    input_schema=ReadOBDDataInput.model_json_schema(),
    handler=read_obd_data,
    input_model=ReadOBDDataInput,
    is_read_only=True,
)

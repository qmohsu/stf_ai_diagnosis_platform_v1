"""Yamaha-aware raw OBD data loader for the agent toolset.

The existing ``obd_agent.log_parser.parse_log_file`` expects tab-separated
internal TSV format produced by the format normalizer.  That path drops
``A_YAM_*`` Yamaha-proprietary columns (see ``format_normalizer.py``
APP-53 note), which would defeat HARNESS-19's locked decision to expose
those signals to the agent.

This loader bypasses the normalizer and reads the raw uploaded bytes
directly, returning all columns (canonical K-Line ``A_KL_*`` plus
proprietary ``A_YAM_*``) so the new investigation tools can answer
questions about the full signal inventory.

Two formats are supported transparently:

1. **Yamaha dual-channel CSV** — comma-separated rows with a ``#``-prefixed
   metadata block.  Detected by a ``# Yamaha Dual`` marker in the first
   few lines.  Metadata block parsed for DTCs and channel info.
2. **Standard OBD TSV** — tab-separated rows with the multi-line header
   produced by python-OBD loggers.  Delegated to
   ``obd_agent.log_parser.parse_log_file``.

The unified return type is ``OBDLogData`` — rows + signal column names +
DTC entries pulled from metadata + format tag.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import structlog

from app.config import settings
from app.db.session import SessionLocal
from app.models_db import OBDAnalysisSession
from obd_agent.log_parser import parse_log_file

logger = structlog.get_logger(__name__)


# ── Public types ─────────────────────────────────────────────────


OBDLogFormat = Literal["yamaha_dual", "standard_tsv", "unknown"]
"""Detected raw-file format tag."""


@dataclass(frozen=True)
class MetadataDTC:
    """One DTC entry pulled from a Yamaha metadata header.

    Attributes:
        code: Raw code string (Yamaha hex format).
        status: ``"stored"`` or ``"pending"``.
        ecu: Originating ECU label (e.g. ``"K-Line"``).
    """

    code: str
    status: Literal["stored", "pending"]
    ecu: str


@dataclass
class OBDLogData:
    """Parsed OBD log with full column preservation.

    Attributes:
        format: Detected format tag.
        rows: List of column-name → raw-string-value row dicts.
            Empty list if the file has no data rows.
        columns: Ordered column names from the header (including
            ``A_YAM_*`` proprietary columns for Yamaha format).
            Excludes the ``Timestamp`` column.
        metadata_dtcs: DTC entries pulled from the ``#`` metadata
            block (Yamaha format).  Empty list for other formats.
        metadata_lines: Raw ``#``-prefixed metadata lines (Yamaha
            format), preserved verbatim for downstream tools that
            want to surface them.
        channels_present: ECU channels that have data in this log
            (e.g. ``{"engine"}``).  ``"engine"`` is set when any
            ``A_KL_*`` or ``A_YAM_*`` column is present.
            ``"abs"`` is set when CAN ABS columns are present
            (none in the current fixture).
    """

    format: OBDLogFormat
    rows: List[Dict[str, str]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    metadata_dtcs: List[MetadataDTC] = field(default_factory=list)
    metadata_lines: List[str] = field(default_factory=list)
    channels_present: set = field(default_factory=set)


# ── File-path resolution ─────────────────────────────────────────


def resolve_log_path(session_id: str) -> Path:
    """Resolve the raw OBD log file path from a session UUID.

    Looks up ``raw_input_file_path`` on the ``OBDAnalysisSession``
    row and joins it with ``settings.obd_log_storage_path``.  Mirrors
    the pattern used by the legacy ``read_obd_data`` tool so test
    fixtures and production storage paths line up identically.

    Args:
        session_id: UUID string identifying the session.

    Returns:
        Absolute filesystem path to the raw log.

    Raises:
        ValueError: If the session_id is malformed, the row doesn't
            exist, or the row has no recorded raw file path.
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


# ── Format detection ─────────────────────────────────────────────


_YAMAHA_DUAL_MARKERS = (
    "yamaha dual",
    "ch.a:",
    "kl_ecu_name",
)


def detect_format(text: str) -> OBDLogFormat:
    """Classify raw log content by format.

    Reads the first ~60 lines and looks for distinguishing markers.
    Doesn't open the file from a path so callers can detect from
    in-memory content during tests.

    Args:
        text: Raw file content as a string.

    Returns:
        Format tag.  ``"unknown"`` if no marker fires.
    """
    head = "\n".join(text.splitlines()[:60]).lower()
    if any(marker in head for marker in _YAMAHA_DUAL_MARKERS):
        return "yamaha_dual"
    # Standard OBD TSV uses "OBD Data Log" header (see log_parser).
    if "obd data log" in head and "\t" in head:
        return "standard_tsv"
    # Tab-separated with Timestamp header — assume standard TSV.
    for line in text.splitlines()[:30]:
        if line.startswith("Timestamp\t"):
            return "standard_tsv"
    return "unknown"


# ── Yamaha-dual parsing ──────────────────────────────────────────


_DTC_METADATA_RE = re.compile(
    r"^#\s*(?P<channel>[A-Za-z]+)_(?P<status>Stored|Pending)\s*:\s*"
    r"(?P<code>[0-9A-Fa-fxX]{4,})\s*$",
)
_CHANNEL_LABEL = {
    "kl": "K-Line",
    "can": "CAN",
}


def _parse_yamaha_metadata_dtcs(
    metadata_lines: List[str],
) -> List[MetadataDTC]:
    """Extract DTC entries from a Yamaha-format metadata block.

    The fixture format puts DTCs in lines like::

        # DTCs:
        #   KL_Stored: 87F11043000000000000CB
        #   KL_Pending: 87F11047000000000000CF

    Args:
        metadata_lines: Raw ``#``-prefixed lines from the header.

    Returns:
        Ordered list of parsed DTCs (stored first, pending second,
        in file order).
    """
    out: List[MetadataDTC] = []
    for raw in metadata_lines:
        match = _DTC_METADATA_RE.match(raw.strip())
        if not match:
            continue
        channel = match.group("channel").lower()
        ecu_label = _CHANNEL_LABEL.get(channel, channel.upper())
        status = match.group("status").lower()  # "stored" or "pending"
        code = match.group("code").upper()
        out.append(MetadataDTC(
            code=code,
            status=status,  # type: ignore[arg-type]
            ecu=ecu_label,
        ))
    return out


def _parse_yamaha_dual_csv(text: str) -> OBDLogData:
    """Parse a Yamaha dual-channel CSV file into ``OBDLogData``.

    Splits metadata (``#``-prefixed lines) from data, reads CSV with
    the stdlib reader (handles quoted fields, embedded commas, etc.),
    and preserves all columns including ``A_YAM_*`` proprietary
    fields.

    Args:
        text: Full file content.

    Returns:
        Parsed ``OBDLogData`` with format=``"yamaha_dual"``.

    Raises:
        ValueError: If no header row is present.
    """
    metadata_lines: List[str] = []
    data_lines: List[str] = []
    for raw in text.splitlines():
        if raw.startswith("#"):
            metadata_lines.append(raw)
            continue
        # Drop the unicode "═" separator line if present.
        if raw and raw[0] not in (",", "\t") and "═" in raw:
            continue
        if not raw:
            continue
        data_lines.append(raw)

    if not data_lines:
        raise ValueError(
            "Yamaha dual-channel CSV has no data rows."
        )

    reader = csv.reader(io.StringIO("\n".join(data_lines)))
    rows_iter = iter(reader)
    try:
        raw_headers = next(rows_iter)
    except StopIteration as exc:
        raise ValueError(
            "Yamaha dual-channel CSV missing header row."
        ) from exc

    headers = [h.strip() for h in raw_headers]
    if "Timestamp" not in headers:
        raise ValueError(
            "Yamaha dual-channel CSV header is missing the "
            "Timestamp column."
        )

    rows: List[Dict[str, str]] = []
    for raw_row in rows_iter:
        if not raw_row or all(c.strip() == "" for c in raw_row):
            continue
        row: Dict[str, str] = {}
        for i, h in enumerate(headers):
            val = raw_row[i].strip() if i < len(raw_row) else ""
            row[h] = val
        rows.append(row)

    metadata_dtcs = _parse_yamaha_metadata_dtcs(metadata_lines)

    channels: set = set()
    if any(h.startswith(("A_KL_", "A_YAM_")) for h in headers):
        channels.add("engine")
    if any(h.startswith("B_") for h in headers):
        channels.add("abs")

    # Strip Timestamp from columns inventory — it is implicit and
    # tools list signal columns only.
    signal_cols = [h for h in headers if h != "Timestamp"]

    return OBDLogData(
        format="yamaha_dual",
        rows=rows,
        columns=signal_cols,
        metadata_dtcs=metadata_dtcs,
        metadata_lines=metadata_lines,
        channels_present=channels,
    )


# ── Standard TSV adapter ─────────────────────────────────────────


def _parse_standard_tsv(path: Path, text: str) -> OBDLogData:
    """Parse a standard OBD TSV via the existing log_parser path.

    Delegates row parsing to ``parse_log_file()`` then derives the
    column inventory from the first row's keys.  No metadata DTCs
    (standard format puts DTCs inside ``GET_DTC`` / ``GET_CURRENT_DTC``
    columns, surfaced separately by ``obd_dtcs.list_dtcs``).

    Args:
        path: File path (passed through to ``parse_log_file``).
        text: File content (used only for channel detection).

    Returns:
        Parsed ``OBDLogData`` with format=``"standard_tsv"``.
    """
    rows = parse_log_file(path)
    columns: List[str] = []
    if rows:
        columns = [c for c in rows[0].keys() if c != "Timestamp"]
    channels: set = set()
    if rows:
        channels.add("engine")
    return OBDLogData(
        format="standard_tsv",
        rows=rows,
        columns=columns,
        metadata_dtcs=[],
        metadata_lines=[],
        channels_present=channels,
    )


# ── Public entry point ───────────────────────────────────────────


def load_obd_data(path: Path) -> OBDLogData:
    """Load raw OBD data from disk, preserving all columns.

    Detects the file format and dispatches to the appropriate
    parser.  Always returns the full column inventory so the agent
    toolset can expose ``A_YAM_*`` proprietary signals (HARNESS-19
    locked decision).

    Args:
        path: Absolute path to the raw log file.

    Returns:
        ``OBDLogData`` with rows, columns, and (for Yamaha format)
        metadata DTCs.

    Raises:
        FileNotFoundError: If ``path`` doesn't exist.
        ValueError: If the file format cannot be parsed.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"OBD log file not found: {path}"
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    # Defensive BOM strip — the Yamaha fixture is UTF-8 with BOM
    # and ``read_text(encoding="utf-8")`` does not consume it.
    # Without this the first metadata line leaks into the CSV
    # body and the header parser fails.
    if text.startswith("﻿"):
        text = text[1:]
    fmt = detect_format(text)
    if fmt == "yamaha_dual":
        return _parse_yamaha_dual_csv(text)
    if fmt == "standard_tsv":
        return _parse_standard_tsv(path, text)
    # Unknown format: try standard TSV (most-common upload shape)
    # and if it fails, fall back to Yamaha CSV.  This ordering keeps
    # the happy path fast for standard logs.
    try:
        return _parse_standard_tsv(path, text)
    except Exception:  # noqa: BLE001
        return _parse_yamaha_dual_csv(text)


def load_for_session(session_id: str) -> OBDLogData:
    """Resolve a session's raw file path and load it.

    Convenience wrapper combining ``resolve_log_path`` and
    ``load_obd_data`` for tools that operate on a session UUID.

    Args:
        session_id: OBD analysis session UUID string.

    Returns:
        Parsed ``OBDLogData``.

    Raises:
        ValueError: From ``resolve_log_path`` on missing session.
        FileNotFoundError: If the file path resolves but the file
            is missing on disk.
    """
    path = resolve_log_path(session_id)
    return load_obd_data(path)


# ── Time helpers shared across OBD tools ─────────────────────────


_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_timestamp(raw: str):
    """Parse an OBD timestamp string to a naive datetime.

    Tolerates the fixture's millisecond-suffix format
    (``2026-05-08 11:20:40.508``) and the standard-TSV
    second-precision format.  Returns ``None`` on failure so
    callers can skip bad rows without raising.

    Args:
        raw: Timestamp string from a row dict.

    Returns:
        Parsed ``datetime`` or ``None``.
    """
    from datetime import datetime
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # Last attempt: ISO 8601 parser.
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def try_float(raw: str) -> Optional[float]:
    """Parse a numeric cell, returning None on N/A or failure.

    Treats common missing-value tokens (``"N/A"``, empty string,
    ``"nan"``) as missing without surfacing an error.

    Args:
        raw: Raw cell string.

    Returns:
        Parsed float or ``None``.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or s.upper() == "N/A" or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

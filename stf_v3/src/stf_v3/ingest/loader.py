"""Raw OBD log reader for the agent tools (PROD-08).

Reads the bytes ``ingest`` stored — nothing is converted at upload
(design doc D1) — and returns one internal shape for the three formats
the platform accepts:

1. **Jetson native TSV** (``OBD Data Log`` banner, tab-separated).
2. **Yamaha dual-channel CSV** (``# Yamaha Dual`` metadata block; keeps
   the ``A_YAM_*`` proprietary columns, HARNESS-19 locked decision).
3. **OBD Maximum Data Log CSV** (``# OBD Maximum Data Log`` banner; the
   real Jetson logger, PROD-07 D3).  V2 only ever saw this format after
   its upload-time conversion to TSV, so the same column rules are
   applied here on the raw file: unit suffixes stripped from headers
   (``RPM (rpm)`` → ``RPM``) and the ``DTC_* / MONITOR_* / M22_*``
   status columns dropped (dev plan PROD-08 FM-37).  Metadata DTCs come
   from the ``Stored_DTCs`` / ``Pending_DTCs`` lines.

The unified return type is ``OBDLogData`` — rows + signal column names
+ DTC entries pulled from metadata + format tag.  Ported from V2
``harness_tools/obd_loader.py`` + ``obd_agent/log_parser.py`` (copied,
never imported: dev plan D3).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import ast
import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)


# ── Public types ─────────────────────────────────────────────────


OBDLogFormat = Literal["yamaha_dual", "standard_tsv", "obd_maxlog", "unknown"]
"""Detected raw-file format tag."""


class LogOwnershipError(ValueError):
    """The log row does not belong to the vehicle being diagnosed (FM-4)."""


@dataclass(frozen=True)
class MetadataDTC:
    """One DTC entry pulled from a metadata header.

    Attributes:
        code: Raw code string (Yamaha hex, standard P-code, or other).
        status: ``"stored"`` or ``"pending"``.
        ecu: Originating ECU label (e.g. ``"K-Line"``, ``"engine"``).
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
        columns: Ordered signal column names (``Timestamp`` excluded).
        metadata_dtcs: DTC entries pulled from the ``#`` metadata block.
        metadata_lines: Raw ``#``-prefixed metadata lines, verbatim.
        channels_present: ECU channels with data (``"engine"``/``"abs"``).
        limitations: Things the reader could not recover (missing
            metadata sections, unparsable rows) — surfaced to the agent
            rather than raised (FM-9).
    """

    format: OBDLogFormat
    rows: List[Dict[str, str]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    metadata_dtcs: List[MetadataDTC] = field(default_factory=list)
    metadata_lines: List[str] = field(default_factory=list)
    channels_present: set = field(default_factory=set)
    limitations: List[str] = field(default_factory=list)


# ── Standard PID units (copied from V2 obd_agent.log_parser) ─────


PID_UNITS: Dict[str, str] = {
    "RPM": "rpm",
    "SPEED": "km/h",
    "THROTTLE_POS": "percent",
    "THROTTLE_POS_B": "percent",
    "ENGINE_LOAD": "percent",
    "ABSOLUTE_LOAD": "percent",
    "RELATIVE_THROTTLE_POS": "percent",
    "THROTTLE_ACTUATOR": "percent",
    "COOLANT_TEMP": "degC",
    "INTAKE_TEMP": "degC",
    "CATALYST_TEMP_B1S1": "degC",
    "MAF": "g/s",
    "INTAKE_PRESSURE": "kPa",
    "BAROMETRIC_PRESSURE": "kPa",
    "FUEL_RAIL_PRESSURE_DIRECT": "kPa",
    "SHORT_FUEL_TRIM_1": "percent",
    "LONG_FUEL_TRIM_1": "percent",
    "TIMING_ADVANCE": "degree",
    "O2_B1S2": "volt",
    "O2_S1_WR_CURRENT": "mA",
    "EGR_ERROR": "percent",
    "COMMANDED_EGR": "percent",
    "EVAPORATIVE_PURGE": "percent",
    "RUN_TIME": "second",
    "WARMUPS_SINCE_DTC_CLEAR": "count",
    "DISTANCE_W_MIL": "km",
    "DISTANCE_SINCE_DTC_CLEAR": "km",
    "CONTROL_MODULE_VOLTAGE": "volt",
    "ELM_VOLTAGE": "volt",
    "ACCELERATOR_POS_D": "percent",
    "ACCELERATOR_POS_E": "percent",
    "COMMANDED_EQUIV_RATIO": "ratio",
}


# ── Format detection ─────────────────────────────────────────────


_YAMAHA_DUAL_MARKERS = ("yamaha dual", "ch.a:", "kl_ecu_name")
_MAXLOG_BANNER = "# obd maximum data log"


def detect_format(text: str) -> OBDLogFormat:
    """Classify raw log content by format.

    Reads the first ~60 lines and looks for distinguishing markers.

    Args:
        text: Raw file content as a string.

    Returns:
        Format tag.  ``"unknown"`` if no marker fires.
    """
    lines = text.splitlines()
    head = "\n".join(lines[:60]).lower()
    for line in lines[:5]:
        stripped = line.strip().lower()
        if stripped:
            if stripped.startswith(_MAXLOG_BANNER):
                return "obd_maxlog"
            break
    if any(marker in head for marker in _YAMAHA_DUAL_MARKERS):
        return "yamaha_dual"
    if "obd data log" in head and "\t" in head:
        return "standard_tsv"
    for line in lines[:30]:
        if line.startswith("Timestamp\t"):
            return "standard_tsv"
    return "unknown"


# ── Yamaha-dual parsing ──────────────────────────────────────────


_DTC_METADATA_RE = re.compile(
    r"^#\s*(?P<channel>[A-Za-z]+)_(?P<status>Stored|Pending)\s*:\s*"
    r"(?P<code>[0-9A-Fa-fxX]{4,})\s*$",
)
_CHANNEL_LABEL = {"kl": "K-Line", "can": "CAN"}


def _parse_yamaha_metadata_dtcs(metadata_lines: List[str]) -> List[MetadataDTC]:
    """Extract DTC entries from a Yamaha-format metadata block.

    Lines look like ``#   KL_Stored: 87F11043000000000000CB``.
    """
    out: List[MetadataDTC] = []
    for raw in metadata_lines:
        match = _DTC_METADATA_RE.match(raw.strip())
        if not match:
            continue
        channel = match.group("channel").lower()
        out.append(MetadataDTC(
            code=match.group("code").upper(),
            status=match.group("status").lower(),  # type: ignore[arg-type]
            ecu=_CHANNEL_LABEL.get(channel, channel.upper()),
        ))
    return out


def _split_hash_metadata(text: str) -> Tuple[List[str], List[str]]:
    """Split ``#`` metadata lines from data lines (unicode rules dropped)."""
    metadata_lines: List[str] = []
    data_lines: List[str] = []
    for raw in text.splitlines():
        if raw.startswith("#"):
            metadata_lines.append(raw)
            continue
        if raw and raw[0] not in (",", "\t") and ("═" in raw or "─" in raw):
            continue
        if not raw.strip():
            continue
        data_lines.append(raw)
    return metadata_lines, data_lines


def _read_csv_rows(data_lines: List[str], what: str) -> Tuple[List[str], List[List[str]]]:
    """Parse CSV lines into (headers, rows) with the stdlib reader."""
    if not data_lines:
        raise ValueError(f"{what} has no data rows.")
    reader = csv.reader(io.StringIO("\n".join(data_lines)))
    rows_iter = iter(reader)
    try:
        raw_headers = next(rows_iter)
    except StopIteration as exc:
        raise ValueError(f"{what} missing header row.") from exc
    rows = [r for r in rows_iter if r and not all(c.strip() == "" for c in r)]
    return [h.strip() for h in raw_headers], rows


def _parse_yamaha_dual_csv(text: str) -> OBDLogData:
    """Parse a Yamaha dual-channel CSV file into ``OBDLogData``."""
    metadata_lines, data_lines = _split_hash_metadata(text)
    headers, raw_rows = _read_csv_rows(data_lines, "Yamaha dual-channel CSV")
    if "Timestamp" not in headers:
        raise ValueError("Yamaha dual-channel CSV header is missing the Timestamp column.")
    rows: List[Dict[str, str]] = []
    for raw_row in raw_rows:
        rows.append({
            h: (raw_row[i].strip() if i < len(raw_row) else "")
            for i, h in enumerate(headers)
        })
    channels: set = set()
    if any(h.startswith(("A_KL_", "A_YAM_")) for h in headers):
        channels.add("engine")
    if any(h.startswith("B_") for h in headers):
        channels.add("abs")
    return OBDLogData(
        format="yamaha_dual",
        rows=rows,
        columns=[h for h in headers if h != "Timestamp"],
        metadata_dtcs=_parse_yamaha_metadata_dtcs(metadata_lines),
        metadata_lines=metadata_lines,
        channels_present=channels,
    )


# ── OBD Maximum Data Log parsing (PROD-07 D3 / PROD-08) ──────────


_UNIT_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")
_MAXLOG_SKIP_PREFIXES = ("DTC_", "MONITOR_", "M22_")
_MAXLOG_DTC_LINE_RE = re.compile(
    r"^#\s*(?P<status>Stored|Pending)_DTCs\s*:\s*(?P<codes>.*)$", re.IGNORECASE,
)
_MAXLOG_SECTION_MARKERS = {
    "mode09": "vehicle information (mode 09)",
    "mode06": "on-board monitoring tests (mode 06)",
    "dtc": "diagnostic trouble codes",
}


def _parse_maxlog_metadata_dtcs(metadata_lines: List[str]) -> List[MetadataDTC]:
    """DTCs from ``#   Stored_DTCs: P0117, P0300`` style lines.

    ``(none)`` / empty yields nothing.  Codes are kept verbatim
    (upper-cased); classification happens in the DTC tools.
    """
    out: List[MetadataDTC] = []
    for raw in metadata_lines:
        match = _MAXLOG_DTC_LINE_RE.match(raw.strip())
        if not match:
            continue
        status = match.group("status").lower()
        codes = match.group("codes").strip()
        if not codes or codes.lower().startswith("(none"):
            continue
        for token in re.split(r"[,\s;]+", codes):
            token = token.strip().upper()
            if token:
                out.append(MetadataDTC(code=token, status=status, ecu="engine"))  # type: ignore[arg-type]
    return out


def _parse_obd_maxlog_csv(text: str) -> OBDLogData:
    """Parse an OBD Maximum Data Log into ``OBDLogData``.

    Applies the V2 conversion rules on the raw file so signal names match
    what V2 produced after its upload-time conversion (FM-37).
    """
    metadata_lines, data_lines = _split_hash_metadata(text)
    headers, raw_rows = _read_csv_rows(data_lines, "OBD Maximum Data Log")
    clean_headers: List[str] = []
    keep_indices: List[int] = []
    for idx, h in enumerate(headers):
        bare = _UNIT_SUFFIX_RE.sub("", h).strip()
        if not bare or any(bare.startswith(p) for p in _MAXLOG_SKIP_PREFIXES):
            continue
        clean_headers.append(bare)
        keep_indices.append(idx)
    if "Timestamp" not in clean_headers:
        raise ValueError("OBD Maximum Data Log has no Timestamp column after header cleanup.")
    rows: List[Dict[str, str]] = []
    for raw_row in raw_rows:
        rows.append({
            clean_headers[i]: (raw_row[ci].strip() if ci < len(raw_row) else "")
            for i, ci in enumerate(keep_indices)
        })
    limitations: List[str] = []
    lowered = "\n".join(metadata_lines).lower()
    for key, marker in _MAXLOG_SECTION_MARKERS.items():
        if marker not in lowered:
            limitations.append(f"maxlog metadata has no {key} section")
    if not any(line.lower().startswith("# log end time") for line in metadata_lines):
        limitations.append("maxlog trailer (Log End Time) absent — logger may have stopped mid-trip")
    return OBDLogData(
        format="obd_maxlog",
        rows=rows,
        columns=[h for h in clean_headers if h != "Timestamp"],
        metadata_dtcs=_parse_maxlog_metadata_dtcs(metadata_lines),
        metadata_lines=metadata_lines,
        channels_present={"engine"} if rows else set(),
        limitations=limitations,
    )


# ── Standard TSV parsing (copied from V2 obd_agent.log_parser) ───


def _parse_tsv_rows(text: str) -> List[Dict[str, str]]:
    """Parse an OBD TSV log into row dicts (column name → raw string)."""
    lines = text.splitlines()
    header_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.startswith("Timestamp\t"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find the Timestamp column header in the TSV log.")
    columns = [c.strip() for c in lines[header_idx].split("\t") if c.strip()]
    rows: List[Dict[str, str]] = []
    for line in lines[header_idx + 2:]:   # skip header + separator
        line = line.rstrip("\n\r")
        if not line or line.startswith("---") or line.startswith("Log "):
            continue
        parts = line.split("\t")
        if len(parts) < len(columns):
            continue
        rows.append({columns[i]: parts[i].strip() for i in range(len(columns))})
    return rows


def _parse_standard_tsv(text: str) -> OBDLogData:
    """Parse a Jetson native TSV (DTCs live in the GET_DTC columns)."""
    rows = _parse_tsv_rows(text)
    columns = [c for c in rows[0].keys() if c != "Timestamp"] if rows else []
    return OBDLogData(
        format="standard_tsv",
        rows=rows,
        columns=columns,
        channels_present={"engine"} if rows else set(),
    )


# ── Public entry points ──────────────────────────────────────────


def load_obd_data(path: Path) -> OBDLogData:
    """Load raw OBD data from disk, preserving all columns.

    Args:
        path: Absolute path to the raw log file.

    Returns:
        ``OBDLogData`` with rows, columns and metadata DTCs.

    Raises:
        FileNotFoundError: If ``path`` doesn't exist.
        ValueError: If the file format cannot be parsed.
    """
    if not path.exists():
        raise FileNotFoundError(f"OBD log file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    return parse_obd_text(text)


def parse_obd_text(text: str) -> OBDLogData:
    """Parse already-decoded log content (used by tests and the loader)."""
    fmt = detect_format(text)
    if fmt == "obd_maxlog":
        return _parse_obd_maxlog_csv(text)
    if fmt == "yamaha_dual":
        return _parse_yamaha_dual_csv(text)
    if fmt == "standard_tsv":
        return _parse_standard_tsv(text)
    try:
        return _parse_standard_tsv(text)
    except Exception:  # noqa: BLE001
        return _parse_yamaha_dual_csv(text)


def load_for_vehicle(
    root: Path, raw_path: str, log_vehicle_id: uuid.UUID, vehicle_id: uuid.UUID,
) -> OBDLogData:
    """Load a stored log after checking it belongs to the vehicle (FM-4).

    Args:
        root: Log storage root (``settings.obd_log_storage_path``).
        raw_path: ``obd_logs.raw_path`` (``<vehicle_id>/<log_id>.<ext>``).
        log_vehicle_id: ``obd_logs.vehicle_id`` of the row.
        vehicle_id: The vehicle being diagnosed.

    Raises:
        LogOwnershipError: When the row's vehicle differs from ``vehicle_id``.
    """
    if log_vehicle_id != vehicle_id:
        raise LogOwnershipError(
            f"log belongs to vehicle {log_vehicle_id}, not {vehicle_id}"
        )
    path = (Path(root) / raw_path).resolve()
    if Path(root).resolve() not in path.parents:
        raise LogOwnershipError("raw_path escapes the log storage root")
    return load_obd_data(path)


# ── Time / value helpers shared across OBD tools ─────────────────


_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parse an OBD timestamp string to a naive datetime (None on failure)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def try_float(raw: Optional[str]) -> Optional[float]:
    """Parse a numeric cell, returning None on N/A / empty / junk."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or s.upper() == "N/A" or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


_DTC_CODE_RE = re.compile(r"\b([PCBU][0-9][0-9A-F]{3})\b")


def parse_dtc_list(raw: str) -> List[Tuple[str, str]]:
    """Parse a ``GET_DTC`` / ``GET_CURRENT_DTC`` cell.

    Formats seen in python-OBD logs: ``[]``, ``N/A``,
    ``[('P0301', 'Cylinder 1 Misfire Detected')]``.
    """
    raw = (raw or "").strip()
    if raw in ("[]", "N/A", ""):
        return []
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [(str(code), str(desc)) for code, desc in parsed]
    except (ValueError, SyntaxError, TypeError):
        pass
    return [(c.upper(), "") for c in _DTC_CODE_RE.findall(raw)]

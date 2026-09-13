"""Jetson native OBD TSV — format detection and metadata.

Copied in spirit from V2 ``obd_agent/log_parser.py`` (design doc D3: copy,
never import).  Differences: no VIN pseudonymisation, no row parsing.

File shape::

    OBD Data Log
    Start Time: 2025-07-23 14:42:16
    Log Interval: 1.0 seconds
    ----------------------------------------------------------------
    Timestamp<TAB>RPM<TAB>...<TAB>VIN<TAB>...
    ----------------------------------------------------------------
    2025-07-23 14:42:16<TAB>0.00<TAB>...<TAB>bytearray(b'JHMGK5830HX202404')...
    ...
    ----------------------------------------------------------------
    Log End Time: 2025-07-23 14:47:06

Author: Xiangzhu Yan
"""

import re
from typing import List, Optional

from stf_v3.ingest.parsers.base import LogMeta, normalise_vin, parse_timestamp

FORMAT = "tsv"
EXTENSION = "tsv"

_BANNER = "OBD Data Log"
_BYTEARRAY_RE = re.compile(r"bytearray\(b'([^']*)'\)")
_FOOTER_LINES = 8


def is_format(head: List[str]) -> bool:
    """True when the banner AND a tab-separated ``Timestamp`` header appear.

    Both are required (V2 accepted either) so that a stray tab-separated
    file with a ``Timestamp`` column is not mistaken for a Jetson log.
    """
    has_banner = any(line.strip() == _BANNER for line in head)
    has_header = any(line.startswith("Timestamp\t") for line in head)
    return has_banner and has_header


def _header_index(lines: List[str]) -> Optional[int]:
    for i, line in enumerate(lines):
        if line.startswith("Timestamp\t"):
            return i
    return None


def _first_data_row(lines: List[str], header_idx: int) -> Optional[List[str]]:
    for line in lines[header_idx + 1:]:
        stripped = line.rstrip("\r\n")
        if not stripped or stripped.startswith("---") or stripped.startswith("Log "):
            continue
        return stripped.split("\t")
    return None


def _value_after(lines: List[str], prefix: str) -> Optional[str]:
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def parse_meta(text: str) -> LogMeta:
    """Extracts VIN and recording window from a Jetson TSV.

    Args:
        text: Decoded file content (already known to be this format).

    Returns:
        ``LogMeta`` with ``vin`` taken from the first data row's ``VIN``
        column (``bytearray(b'...')`` repr or plain), ``recorded_start``
        from ``Start Time:`` and ``recorded_end`` from ``Log End Time:``
        (falling back to the last data row's timestamp).
    """
    lines = text.splitlines()
    vin: Optional[str] = None
    header_idx = _header_index(lines)
    if header_idx is not None:
        columns = [c.strip() for c in lines[header_idx].split("\t") if c.strip()]
        row = _first_data_row(lines, header_idx)
        if row is not None and "VIN" in columns:
            # Jetson pads the header with extra tabs; data rows do not.
            # Map by position after dropping empty header cells.
            vin_idx = columns.index("VIN")
            if vin_idx < len(row):
                raw = row[vin_idx]
                m = _BYTEARRAY_RE.search(raw)
                vin = normalise_vin(m.group(1) if m else raw)

    start = parse_timestamp(_value_after(lines[:16], "Start Time:"))
    end = parse_timestamp(_value_after(lines[-_FOOTER_LINES:], "Log End Time:"))
    if end is None and header_idx is not None:
        for line in reversed(lines):
            first = line.split("\t", 1)[0]
            end = parse_timestamp(first)
            if end is not None:
                break
    return LogMeta(
        format=FORMAT, extension=EXTENSION, vin=vin,
        recorded_start=start, recorded_end=end,
    )

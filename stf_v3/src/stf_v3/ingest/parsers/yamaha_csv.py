"""Yamaha dual-channel CSV — format detection and metadata.

Copied in spirit from V2 ``harness_tools/obd_loader.py`` (design doc D3).
Detection is stricter than V2: the first non-empty line must carry the
``# Yamaha Dual`` marker (V2 accepted any of three markers anywhere in the
first 60 lines, which could misfire on an unrelated CSV containing
``Ch.A:``).

File shape::

    # Yamaha Dual OBDLink EX Log
    # vehicle_id: <optional identifier; used as VIN only if 17 chars>
    # Start: 2026-05-08 11:20:39
    # Interval: 1.0s
    # ...metadata / DTC block...
    Timestamp,A_KL_BARO,...
    2026-05-08 11:20:40.508,N/A,...
    # End: 2026-05-08 11:24:57.508967
    # End reason: disconnect

Author: Xiangzhu Yan
"""

from typing import List, Optional

from stf_v3.ingest.parsers.base import LogMeta, normalise_vin, parse_timestamp

FORMAT = "yamaha"
EXTENSION = "csv"

_MARKER = "# yamaha dual"
_FOOTER_LINES = 8


def is_format(head: List[str]) -> bool:
    """True when the first non-empty line carries the Yamaha Dual marker."""
    for line in head:
        stripped = line.lstrip("﻿").strip()
        if not stripped:
            continue
        return stripped.lower().startswith(_MARKER)
    return False


def _meta_value(lines: List[str], key: str) -> Optional[str]:
    """Returns the value of a ``# key: value`` metadata line, if present."""
    prefix = f"# {key}:"
    for line in lines:
        stripped = line.lstrip("﻿")
        if stripped.lower().startswith(prefix.lower()):
            return stripped[len(prefix):].strip()
    return None


def parse_meta(text: str) -> LogMeta:
    """Extracts VIN and recording window from a Yamaha CSV.

    Args:
        text: Decoded file content (already known to be this format).

    Returns:
        ``LogMeta``; ``vin`` is set only when a ``# vehicle_id:`` line holds
        a valid 17-char VIN (blueprint §5: Yamaha files usually carry none),
        ``recorded_start`` from ``# Start:``, ``recorded_end`` from the
        ``# End:`` footer (falling back to the last data row's timestamp).
    """
    lines = text.splitlines()
    head = lines[:64]
    vin = normalise_vin(_meta_value(head, "vehicle_id"))
    start = parse_timestamp(_meta_value(head, "Start"))
    end = parse_timestamp(_meta_value(lines[-_FOOTER_LINES:], "End"))
    if end is None:
        for line in reversed(lines):
            if line.startswith("#") or not line.strip():
                continue
            end = parse_timestamp(line.split(",", 1)[0])
            if end is not None:
                break
    return LogMeta(
        format=FORMAT, extension=EXTENSION, vin=vin,
        recorded_start=start, recorded_end=end,
    )

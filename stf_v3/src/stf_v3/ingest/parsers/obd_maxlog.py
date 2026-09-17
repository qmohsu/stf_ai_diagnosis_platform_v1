"""OBD Maximum Data Log (``obd_maxlog``) — format detection and metadata.

The format the real Jetson logger writes (Perry's Python "OBD MaxLog"
script; 23 of the 68 uploads in the V2 store, every 2026 Hiace and Corolla
trip).  Added under PROD-07 decision D3 (2026-09-17) per dev plan §4: a new
format gets its own targeted parser, never a generic conversion layer.
Bytes are stored as uploaded; nothing is converted.

File shape::

    # OBD Maximum Data Log
    # vehicle_id: <VIN, present when the logger was told the car>
    # Vehicle: Toyota Hiace
    # Start Time: 2026-06-22 15:39:54
    # ...metadata, batch plan, Mode 09 block ("#   VIN: ..."), DTC block...
    Timestamp,RPM (rpm),SPEED (km/h),...
    2026-06-22 15:39:54.144,851.5000,0.0000,...
    # ══════
    # Log End Time: 2026-06-22 15:41:19        (trailer; absent when the
    # End Reason: OBD-Link EX unplugged         logger died mid-trip)

Author: Xiangzhu Yan
"""

from typing import List, Optional

from stf_v3.ingest.parsers.base import LogMeta, normalise_vin, parse_timestamp

FORMAT = "maxlog"
EXTENSION = "csv"

_BANNER = "# obd maximum data log"
_FOOTER_LINES = 12


def is_format(head: List[str]) -> bool:
    """True when the first non-empty line is the OBD Maximum banner."""
    for line in head:
        stripped = line.lstrip("﻿").strip()
        if not stripped:
            continue
        return stripped.lower().startswith(_BANNER)
    return False


def _meta_value(lines: List[str], key: str) -> Optional[str]:
    """Value of a ``# key: value`` line (leading spaces after ``#`` allowed,
    so both ``# vehicle_id:`` and the Mode 09 ``#   VIN:`` line match)."""
    wanted = key.lower() + ":"
    for line in lines:
        stripped = line.lstrip("﻿")
        if not stripped.startswith("#"):
            continue
        body = stripped[1:].strip()
        if body.lower().startswith(wanted):
            return body[len(wanted):].strip()
    return None


def parse_meta(text: str) -> LogMeta:
    """Extracts VIN and recording window from an OBD Maximum log.

    Args:
        text: Decoded file content (already known to be this format).

    Returns:
        ``LogMeta``; ``vin`` from ``# vehicle_id:`` when it is a valid
        17-char VIN, else from the Mode 09 ``#   VIN:`` line, else None
        (the Corolla logs carry neither); ``recorded_start`` from
        ``# Start Time:``; ``recorded_end`` from the ``# Log End Time:``
        trailer, falling back to the last data row's timestamp.
    """
    lines = text.splitlines()
    head = lines[:64]
    vin = normalise_vin(_meta_value(head, "vehicle_id"))
    if vin is None:
        vin = normalise_vin(_meta_value(head, "VIN"))
    start = parse_timestamp(_meta_value(head, "Start Time"))
    end = parse_timestamp(_meta_value(lines[-_FOOTER_LINES:], "Log End Time"))
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

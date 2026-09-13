"""Shared types and helpers for the log parsers.

Author: Xiangzhu Yan
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class LogMeta:
    """What ingest needs to know about an uploaded log.

    Attributes:
        format: ``"tsv"`` or ``"yamaha"`` (matches the ``obd_logs.format``
            CHECK constraint).
        extension: File extension used for the stored copy.
        vin: 17-char VIN read from the file, or None when the file carries
            none (Yamaha CSVs usually don't).
        recorded_start: First recording timestamp, if readable.
        recorded_end: Last recording timestamp, if readable.
    """

    format: str
    extension: str
    vin: Optional[str]
    recorded_start: Optional[datetime]
    recorded_end: Optional[datetime]


def normalise_vin(raw: Optional[str]) -> Optional[str]:
    """Returns the upper-cased VIN when ``raw`` is a valid 17-char VIN.

    Anything else (empty, ``N/A``, wrong length, I/O/Q) yields ``None`` so a
    garbage value never triggers a false VIN-mismatch rejection.
    """
    if raw is None:
        return None
    value = raw.strip().upper()
    return value if _VIN_RE.fullmatch(value) else None


def parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parses ``YYYY-MM-DD HH:MM:SS[.ffffff]`` into an aware UTC datetime.

    Loggers write wall-clock time without a zone; V2 treated it as UTC and
    V3 keeps that convention.  Unparseable input yields ``None`` (blueprint
    O2: never guess).
    """
    if raw is None:
        return None
    cleaned = raw.replace("\x00", "").strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

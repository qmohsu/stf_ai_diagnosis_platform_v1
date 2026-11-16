"""Shared types and helpers for the log parsers.

Author: Xiangzhu Yan
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")

# Loggers write local wall-clock time without a zone.  Every pilot fleet
# runs in Hong Kong (UTC+8, no daylight saving since 1979), so that is how
# the wall clock is read.  A fixed offset needs no tz database, which the
# Windows dev machines lack.  Issue #257: reading it as UTC (the V2
# convention, kept until 2026-11-10) put every recording 8 hours late.
LOGGER_TZ = timezone(timedelta(hours=8), "HKT")


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
    """Parses logger wall-clock ``YYYY-MM-DD HH:MM:SS[.ffffff]``.

    The digits are read as :data:`LOGGER_TZ` (Hong Kong time), so the
    result is the true instant of the recording.  Unparseable input yields
    ``None`` (blueprint O2: never guess).

    Args:
        raw: Timestamp text from a log header, footer or data row.

    Returns:
        An aware datetime in ``LOGGER_TZ``, or ``None``.
    """
    if raw is None:
        return None
    cleaned = raw.replace("\x00", "").strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=LOGGER_TZ)
        except ValueError:
            continue
    return None

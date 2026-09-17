"""Format sniffing for uploaded OBD logs (code design §3.3, PROD-05).

Three formats exist: Jetson native TSV and Yamaha dual-channel CSV (design
doc §1.5, D1) plus OBD Maximum Data Log — the format the real Jetson
logger actually writes (PROD-07 decision D3, 2026-09-17).  There is
deliberately no generic format layer — a new format gets its own parser
module and a new ``format`` CHECK value (dev plan §4), exactly as happened
for ``maxlog``.

Each parser answers exactly four questions about a file: is it mine, which
VIN does it carry, when did recording start, when did it end.  Nothing here
reads the data rows into memory as records; that is the diagnosis tools'
job (PROD-08).

Author: Xiangzhu Yan
"""

from typing import List, Optional

from stf_v3.ingest.parsers import jetson_tsv, obd_maxlog, yamaha_csv
from stf_v3.ingest.parsers.base import LogMeta

__all__ = ["LogMeta", "SNIFF_LINES", "decode", "sniff"]

#: How many leading lines a parser may look at to decide "is this mine".
SNIFF_LINES = 64


def decode(data: bytes) -> str:
    """Decodes uploaded bytes leniently (UTF-8, BOM stripped)."""
    return data.decode("utf-8", errors="replace").lstrip("﻿")


def sniff(data: bytes) -> Optional[LogMeta]:
    """Identifies the format of ``data`` and extracts its metadata.

    Args:
        data: Raw uploaded bytes.

    Returns:
        ``LogMeta`` for a recognised format, ``None`` otherwise (the caller
        turns that into 422 ``unsupported_format``).
    """
    text = decode(data)
    head: List[str] = text.splitlines()[:SNIFF_LINES]
    if yamaha_csv.is_format(head):
        return yamaha_csv.parse_meta(text)
    if obd_maxlog.is_format(head):
        return obd_maxlog.parse_meta(text)
    if jetson_tsv.is_format(head):
        return jetson_tsv.parse_meta(text)
    return None

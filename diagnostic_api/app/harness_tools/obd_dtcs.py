"""DTC-side OBD investigation primitives (HARNESS-19).

Two tools:

- ``list_dtcs``  — enumerate fault codes present in the session.
- ``lookup_dtc`` — decode one code (standard or Yamaha-proprietary)
  with manual-search pivot guidance.

The Yamaha fixture stores DTCs in two distinct shapes that this
module unifies:

1. **Metadata header** (Yamaha CSV): ``# KL_Stored: 87F1...`` lines
   in the ``#``-prefixed block — surfaced via
   ``OBDLogData.metadata_dtcs``.
2. **Column-level** (standard OBD-II TSV): ``GET_DTC`` and
   ``GET_CURRENT_DTC`` columns with python-OBD's Python-literal
   list format — parsed by ``obd_agent.log_parser._parse_dtc_list``.

For Yamaha proprietary hex codes there is no decoder. Per the
locked design decision (HARNESS-19), ``lookup_dtc`` returns honest
"no decoder available" output with a ``search_manual`` pivot
suggestion.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import (
    ListDTCsInput,
    LookupDTCInput,
)
from app.harness_tools.obd_loader import (
    MetadataDTC,
    OBDLogData,
    load_for_session,
)

logger = structlog.get_logger(__name__)


# ── Standard P/C/B/U code helpers ────────────────────────────────


_STANDARD_DTC_RE = re.compile(r"^[PCBU][0-9A-Fa-f]{4}$", re.IGNORECASE)
_YAMAHA_HEX_RE = re.compile(r"^[0-9A-Fa-f]{8,}$")


def _classify_code(code: str) -> str:
    """Tag a DTC code by format family.

    Returns one of ``"standard"`` (P/C/B/U + 4 hex digits, case
    insensitive), ``"yamaha_hex"`` (raw Yamaha byte string,
    10+ hex digits), or ``"unknown"``.
    """
    code = code.strip()
    if _STANDARD_DTC_RE.match(code):
        return "standard"
    if _YAMAHA_HEX_RE.match(code) and len(code) >= 10:
        return "yamaha_hex"
    return "unknown"


_DTC_SUBSYSTEM_MAP: Dict[str, str] = {
    "P": "engine / powertrain",
    "C": "chassis",
    "B": "body",
    "U": "network",
}


def _subsystem_from_letter(code: str) -> str:
    """Map the first character of a standard DTC to a subsystem label."""
    if not code:
        return "unknown"
    return _DTC_SUBSYSTEM_MAP.get(code[0].upper(), "unknown")


def _load_standard_dtc_table():
    """Try to load python-OBD's DTC description table.

    Imports lazily so the module loads even when ``python-obd`` is
    not in the active environment.

    Returns:
        Dict[code, description] or ``{}`` on failure.
    """
    try:
        from obd.OBDCommand import OBDCommand  # noqa: F401
        import obd
        table = getattr(obd.commands, "GET_DTC", None)
        if table is None:
            return {}
        decoder = getattr(table, "decode", None)
        # python-OBD doesn't expose a public code dict; rely on the
        # internal ``obd.UnitsAndScaling`` lookup if present.
        return getattr(obd, "_DTC", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


# Standard P-code related-PID hints.  Keep small and curated — the
# agent can always fall back to search_manual for richer guidance.
_RELATED_PIDS: Dict[str, List[str]] = {
    "P0117": ["COOLANT_TEMP", "IAT", "CTRL_VOLT"],
    "P0118": ["COOLANT_TEMP", "IAT", "CTRL_VOLT"],
    "P0171": ["SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "MAP", "RPM"],
    "P0174": ["SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "MAP", "RPM"],
    "P0300": ["RPM", "ENGINE_LOAD", "TIMING_ADVANCE"],
    "P0301": ["RPM", "ENGINE_LOAD"],
    "P0302": ["RPM", "ENGINE_LOAD"],
    "P0303": ["RPM", "ENGINE_LOAD"],
    "P0304": ["RPM", "ENGINE_LOAD"],
}


# ── DTC collection from the session ──────────────────────────────


def _column_dtcs(data: OBDLogData) -> List[Dict[str, Any]]:
    """Pull standard P-code DTCs out of GET_DTC columns.

    Uses ``obd_agent.log_parser._parse_dtc_list`` for parsing.  Both
    ``GET_DTC`` (stored) and ``GET_CURRENT_DTC`` (pending) columns
    are scanned.  Duplicate codes within the same column are
    de-duplicated; presence in ``GET_DTC`` only marks the code as
    stored, presence in ``GET_CURRENT_DTC`` only marks it as
    pending — codes appearing in both are reported as ``stored``.

    Args:
        data: Parsed log.

    Returns:
        Ordered list of dicts with keys ``code``, ``status``,
        ``ecu``, ``format``, ``description`` (if available).
    """
    from obd_agent.log_parser import _parse_dtc_list

    stored: Dict[str, str] = {}
    pending: Dict[str, str] = {}
    for r in data.rows:
        for cell, bucket in (
            (r.get("GET_DTC", ""), stored),
            (r.get("GET_CURRENT_DTC", ""), pending),
        ):
            for code, desc in _parse_dtc_list(cell):
                key = code.upper()
                bucket.setdefault(key, desc)

    out: List[Dict[str, Any]] = []
    for code, desc in stored.items():
        out.append({
            "code": code,
            "status": "stored",
            "ecu": "engine",
            "format": _classify_code(code),
            "description": desc or None,
        })
    for code, desc in pending.items():
        if code in stored:
            # Already reported as stored.
            continue
        out.append({
            "code": code,
            "status": "pending",
            "ecu": "engine",
            "format": _classify_code(code),
            "description": desc or None,
        })
    return out


def _metadata_to_dict(entry: MetadataDTC) -> Dict[str, Any]:
    """Convert a ``MetadataDTC`` to the unified dict shape."""
    return {
        "code": entry.code,
        "status": entry.status,
        "ecu": entry.ecu,
        "format": _classify_code(entry.code),
        "description": None,
    }


def _collect_all_dtcs(data: OBDLogData) -> List[Dict[str, Any]]:
    """Merge metadata-block and column-level DTCs.

    Yamaha fixture: metadata block carries the raw hex codes.
    Standard TSV: GET_DTC columns carry standard P-codes.
    Both paths run; results are de-duplicated by ``(code, status)``.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for entry in data.metadata_dtcs:
        d = _metadata_to_dict(entry)
        key = (d["code"], d["status"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    for d in _column_dtcs(data):
        key = (d["code"], d["status"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


# ── list_dtcs ────────────────────────────────────────────────────


def _ecu_filter_matches(entry: Dict[str, Any], wanted: str) -> bool:
    """Coarse ECU filter — handles 'engine' vs 'abs' tagging."""
    if wanted == "all":
        return True
    ecu = (entry.get("ecu") or "").lower()
    if wanted == "engine":
        return "k-line" in ecu or "engine" in ecu
    if wanted == "abs":
        return "can" in ecu or "abs" in ecu
    return True


async def list_dtcs(input_data: Dict[str, Any]) -> str:
    """Enumerate DTCs in the session, optionally filtered.

    Surfaces both Yamaha-hex metadata DTCs and standard-format
    column-level DTCs.  Returns a grouped table.

    Args:
        input_data: ``ListDTCsInput`` + ``_session_id``.

    Returns:
        Text DTC inventory or an informational "no DTCs" message.
    """
    session_id = input_data["_session_id"]
    status_filter = input_data.get("status", "all")
    ecu_filter = input_data.get("ecu", "all")

    data = load_for_session(session_id)
    all_dtcs = _collect_all_dtcs(data)

    # Apply filters.
    filtered = [
        d for d in all_dtcs
        if (status_filter == "all" or d["status"] == status_filter)
        and _ecu_filter_matches(d, ecu_filter)
    ]

    lines: List[str] = []
    lines.append(
        f"DTCs in session — {len(filtered)} of "
        f"{len(all_dtcs)} match filters "
        f"(status='{status_filter}', ecu='{ecu_filter}')"
    )

    if not filtered:
        if not all_dtcs:
            lines.append("")
            lines.append(
                "No DTCs found in this session. Engine is "
                "either healthy or the recorder did not capture "
                "the DTC scan."
            )
        else:
            lines.append("")
            lines.append(
                "No DTCs match these filters. Try "
                "status='all' or ecu='all'."
            )
        return "\n".join(lines)

    lines.append("")
    # Group standard vs Yamaha hex for readability.
    standard = [d for d in filtered if d["format"] == "standard"]
    yamaha = [d for d in filtered if d["format"] == "yamaha_hex"]
    unknown = [d for d in filtered if d["format"] == "unknown"]

    if standard:
        lines.append("Standard OBD-II codes:")
        for d in standard:
            desc = d["description"] or "(no description in logger)"
            lines.append(
                f"  {d['status'].upper():8s} {d['ecu']:12s} "
                f"{d['code']}   — {desc}"
            )
        lines.append("")

    if yamaha:
        lines.append("Yamaha-proprietary raw hex codes:")
        for d in yamaha:
            lines.append(
                f"  {d['status'].upper():8s} {d['ecu']:12s} "
                f"{d['code']}   [no decoder]"
            )
        lines.append("")

    if unknown:
        lines.append("Unrecognized format:")
        for d in unknown:
            lines.append(
                f"  {d['status'].upper():8s} {d['ecu']:12s} "
                f"{d['code']}"
            )
        lines.append("")

    notes: List[str] = []
    if yamaha:
        notes.append(
            "Yamaha hex codes — call `lookup_dtc(code)` for "
            "decode attempts + manual-search guidance."
        )
    if not data.rows:
        notes.append(
            "Session has no row-level data — only metadata DTCs "
            "are available."
        )
    if notes:
        lines.append("Notes:")
        for n in notes:
            lines.append(f"  • {n}")

    return "\n".join(lines).rstrip()


# ── lookup_dtc ───────────────────────────────────────────────────


def _standard_lookup(code: str) -> Tuple[Optional[str], List[str]]:
    """Return (description, related_pids) for a standard DTC code.

    Description lookup uses python-OBD's DTC table when reachable;
    falls back to ``None``.  Related-PIDs uses the curated map in
    this module.
    """
    table = _load_standard_dtc_table()
    desc = table.get(code.upper()) if table else None
    related = _RELATED_PIDS.get(code.upper(), [])
    return desc, related


def _format_standard_lookup(code: str) -> str:
    """Render a structured lookup result for a standard P/C/B/U code."""
    desc, related = _standard_lookup(code)
    sub = _subsystem_from_letter(code)
    lines: List[str] = []
    lines.append(f"DTC {code.upper()} — standard OBD-II code")
    lines.append("")
    lines.append(
        f"Subsystem: {sub}"
    )
    if desc:
        lines.append(f"Description: {desc}")
    else:
        lines.append(
            "Description: no description available in the "
            "in-process DTC table. The code is recognised as "
            "standard format; consult the vehicle's manual for "
            "the manufacturer-specific definition."
        )
    if related:
        lines.append(
            f"Related signals to investigate: {', '.join(related)}"
        )
    lines.append("")
    lines.append("Next step suggestions:")
    lines.append(
        f"  • `search_manual(query='{code}')` — pull the "
        f"manufacturer's diagnostic procedure."
    )
    if related:
        rel = ", ".join(f"'{p}'" for p in related[:3])
        lines.append(
            f"  • `get_signal_stats(signals=[{rel}])` — check "
            f"the related signals' distributions."
        )
    return "\n".join(lines)


def _format_yamaha_lookup(code: str) -> str:
    """Render the honest no-decoder response for a Yamaha hex code."""
    return "\n".join([
        f"DTC {code.upper()} — Yamaha-proprietary raw hex",
        "",
        "No decoder available in this codebase. This is a "
        "Yamaha K-Line stored-DTC byte sequence (likely contains "
        "header bytes, code body, and checksum); standard "
        "P/C/B/U lookup tables do not apply.",
        "",
        "Recommended next steps:",
        f"  • `search_manual(query='{code}')` — try the raw "
        f"hex string against the Yamaha service manual directly.",
        "  • `search_manual(query='DTC table')` — find the "
        "Yamaha DTC chart in the manual appendix and map this "
        "code by structure.",
        "  • `search_manual(query='fault code list')` — broader "
        "lookup if the DTC chart isn't named that.",
        "",
        "Once you have the Yamaha-defined fault code, return to "
        "the OBD data with `get_signal_stats` and `find_events` "
        "on the implicated signals.",
    ])


def _format_unknown_lookup(code: str) -> str:
    """Render the error response for an unrecognised code format."""
    return (
        f"DTC '{code}' is not a recognised OBD-II standard "
        f"P/C/B/U code (4 hex digits) and does not match the "
        f"Yamaha hex format (10+ hex digits). Verify the code "
        f"with the user, or call `search_manual(query='{code}')` "
        f"in case the manual defines a manufacturer-specific "
        f"chart."
    )


async def lookup_dtc(input_data: Dict[str, Any]) -> str:
    """Decode one DTC code.

    Standard P/C/B/U codes get a structured response with subsystem
    + description + related signals + next-step suggestions.
    Yamaha proprietary hex codes get the honest no-decoder
    response with manual-search pivot.  Unrecognised formats get
    a verification prompt.

    Args:
        input_data: ``LookupDTCInput`` + (optional) ``_session_id``.

    Returns:
        Structured decode text.
    """
    code = (input_data.get("code") or "").strip()
    if not code:
        return (
            "Validation error: `code` must be a non-empty "
            "string."
        )

    cls = _classify_code(code)
    if cls == "standard":
        return _format_standard_lookup(code)
    if cls == "yamaha_hex":
        return _format_yamaha_lookup(code)
    return _format_unknown_lookup(code)


# ── ToolDefinition exports ───────────────────────────────────────


_LIST_DTCS_DESC = (
    "List fault codes (DTCs) present in this session's OBD log. "
    "Surfaces both standard P/C/B/U codes (from GET_DTC columns) "
    "and Yamaha-proprietary raw hex codes (from the log metadata "
    "block). Returns a grouped table separated by status "
    "(stored vs pending) and ECU. Cheap — call freely. Filter "
    "by status ('stored'/'pending'/'all') and ecu ('engine'/"
    "'abs'/'all')."
)

_LOOKUP_DTC_DESC = (
    "Decode one fault code. Standard P/C/B/U codes return "
    "subsystem + description + related signals to investigate. "
    "Yamaha-proprietary raw hex codes return an honest "
    "'no decoder available' message with `search_manual` "
    "pivot guidance (no fabricated decodings). Use after "
    "`list_dtcs` to drill into a specific code."
)


LIST_DTCS_DEF = ToolDefinition(
    name="list_dtcs",
    description=_LIST_DTCS_DESC,
    input_schema=ListDTCsInput.model_json_schema(),
    handler=list_dtcs,
    input_model=ListDTCsInput,
    is_read_only=True,
    max_result_chars=10_000,
)


LOOKUP_DTC_DEF = ToolDefinition(
    name="lookup_dtc",
    description=_LOOKUP_DTC_DESC,
    input_schema=LookupDTCInput.model_json_schema(),
    handler=lookup_dtc,
    input_model=LookupDTCInput,
    is_read_only=True,
    max_result_chars=8_000,
)

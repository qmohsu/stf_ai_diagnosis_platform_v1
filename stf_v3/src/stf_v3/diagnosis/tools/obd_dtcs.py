"""OBD investigation primitives — DTC-side tools (copied from V2).

- ``list_dtcs``  — enumerate fault codes (standard P/C/B/U from GET_DTC
  columns, Yamaha-proprietary hex and maxlog metadata codes).
- ``lookup_dtc`` — decode one code (subsystem + curated related signals;
  never a fabricated description).

Added for the real Jetson logger (maxlog): a ``Stored_DTCs`` value such
as ``430100AF`` is a raw Mode 43 response frame; it is decoded to the
standard codes it carries (``P00AF``) and reported alongside the raw
frame, so the agent sees the code the workshop scanner would show.  An
empty frame (``4300`` / ``4700`` = no codes) is dropped, never listed.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic_ai import RunContext

from stf_v3.diagnosis.agent.deps import core_deps
from stf_v3.diagnosis.tools._common import execute
from stf_v3.ingest.loader import MetadataDTC, OBDLogData, parse_dtc_list

_STANDARD_RE = re.compile(r"^[PCBU][0-9][0-9A-F]{3}$", re.IGNORECASE)
_YAMAHA_HEX_RE = re.compile(r"^[0-9A-F]{10,}$", re.IGNORECASE)
_MODE43_RE = re.compile(r"^(?:43|47|4A)[0-9A-F]{2}(?:[0-9A-F]{4})*$", re.IGNORECASE)   # stored / pending / permanent

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
    "P00AF": ["INTAKE_PRESSURE", "ENGINE_LOAD", "RPM", "MAF"],
}

_LETTER = {0: "P", 1: "C", 2: "B", 3: "U"}


def classify_code(code: str) -> str:
    """``standard`` / ``yamaha_hex`` / ``mode43_frame`` / ``unknown``."""
    c = code.strip()
    if _STANDARD_RE.match(c):
        return "standard"
    if _MODE43_RE.match(c) and len(c) >= 4:
        return "mode43_frame"
    if _YAMAHA_HEX_RE.match(c):
        return "yamaha_hex"
    return "unknown"


def decode_mode43_frame(frame: str) -> List[str]:
    """Standard codes inside a raw Mode 43 / 47 / 4A response (``4301 00AF`` → P00AF)."""
    body = frame.strip().upper()[4:]   # drop "43" + count byte
    codes: List[str] = []
    for i in range(0, len(body) - 3, 4):
        chunk = body[i:i + 4]
        if chunk == "0000":
            continue
        first = int(chunk[0], 16)
        codes.append(f"{_LETTER[first >> 2]}{first & 0x3}{chunk[1:]}")
    return codes


def _subsystem_from_letter(code: str) -> str:
    return {"P": "powertrain", "C": "chassis", "B": "body", "U": "network"}.get(code[:1].upper(), "unknown")


def _column_dtcs(data: OBDLogData) -> List[Dict[str, Any]]:
    stored: Dict[str, str] = {}
    pending: Dict[str, str] = {}
    for r in data.rows:
        for cell, bucket in ((r.get("GET_DTC", ""), stored), (r.get("GET_CURRENT_DTC", ""), pending)):
            for code, desc in parse_dtc_list(cell):
                bucket.setdefault(code.upper(), desc)
    out: List[Dict[str, Any]] = []
    for code, desc in stored.items():
        out.append({"code": code, "status": "stored", "ecu": "engine", "format": classify_code(code), "description": desc or None})
    for code, desc in pending.items():
        if code not in stored:
            out.append({"code": code, "status": "pending", "ecu": "engine", "format": classify_code(code), "description": desc or None})
    return out


def _metadata_entries(entry: MetadataDTC) -> List[Dict[str, Any]]:
    fmt = classify_code(entry.code)
    base = {"code": entry.code, "status": entry.status, "ecu": entry.ecu, "format": fmt, "description": None}
    if fmt != "mode43_frame":
        return [base]
    decoded = decode_mode43_frame(entry.code)
    if not decoded:
        # ``4300`` / ``4700`` = "no stored / pending codes": not a DTC, so it
        # never reaches the model (the server smoke showed qwen looking it up).
        return []
    out = [{"code": c, "status": entry.status, "ecu": entry.ecu, "format": "standard",
            "description": f"decoded from raw frame {entry.code}"} for c in decoded]
    return out + [base]


def collect_all_dtcs(data: OBDLogData) -> List[Dict[str, Any]]:
    """Merge metadata-block and column-level DTCs, de-duplicated."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for entry in data.metadata_dtcs:
        for d in _metadata_entries(entry):
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


def _ecu_filter_matches(entry: Dict[str, Any], wanted: str) -> bool:
    if wanted == "all":
        return True
    ecu = (entry.get("ecu") or "").lower()
    if wanted == "engine":
        return "k-line" in ecu or "engine" in ecu
    if wanted == "abs":
        return "can" in ecu or "abs" in ecu
    return True


async def list_dtcs(
    ctx: RunContext[Any],
    status: Literal["stored", "pending", "all"] = "all",
    ecu: Literal["engine", "abs", "all"] = "all",
) -> str:
    """List fault codes (DTCs) present in this vehicle's OBD log.

    Surfaces standard P/C/B/U codes (from GET_DTC columns or the logger's
    metadata block) and Yamaha-proprietary raw hex codes. Returns a
    grouped table separated by status (stored vs pending) and ECU.
    Cheap — call freely.

    Args:
        status: Filter by DTC status. 'stored' = confirmed faults,
            'pending' = unconfirmed (not yet two-trip validated).
        ecu: Filter by originating ECU.
    """
    return await execute(ctx, "list_dtcs", {"status": status, "ecu": ecu}, lambda: _list_dtcs(ctx, status, ecu))


async def _list_dtcs(ctx: RunContext[Any], status_filter: str, ecu_filter: str) -> str:
    data = core_deps(ctx.deps).load_log()
    all_dtcs = collect_all_dtcs(data)
    filtered = [d for d in all_dtcs
                if (status_filter == "all" or d["status"] == status_filter) and _ecu_filter_matches(d, ecu_filter)]
    lines: List[str] = [f"DTCs in log — {len(filtered)} of {len(all_dtcs)} match filters "
                        f"(status='{status_filter}', ecu='{ecu_filter}')"]
    if not filtered:
        lines.append("")
        if not all_dtcs:
            lines.append("No DTCs found in this log. Engine is either healthy or the recorder did not capture the DTC scan.")
        else:
            lines.append("No DTCs match these filters. Try status='all' or ecu='all'.")
        return "\n".join(lines)
    lines.append("")
    groups = {
        "standard": ("Standard OBD-II codes:", [d for d in filtered if d["format"] == "standard"]),
        "yamaha_hex": ("Yamaha-proprietary raw hex codes:", [d for d in filtered if d["format"] == "yamaha_hex"]),
        "mode43_frame": ("Raw Mode 43 response frames (decoded codes listed above):", [d for d in filtered if d["format"] == "mode43_frame"]),
        "unknown": ("Unrecognized format:", [d for d in filtered if d["format"] == "unknown"]),
    }
    for fmt, (title, entries) in groups.items():
        if not entries:
            continue
        lines.append(title)
        for d in entries:
            if fmt == "standard":
                desc = d["description"] or "(no description in logger)"
                lines.append(f"  {d['status'].upper():8s} {d['ecu']:12s} {d['code']}   — {desc}")
            elif fmt == "yamaha_hex":
                lines.append(f"  {d['status'].upper():8s} {d['ecu']:12s} {d['code']}   [no decoder]")
            else:
                lines.append(f"  {d['status'].upper():8s} {d['ecu']:12s} {d['code']}" + (f"   — {d['description']}" if d.get("description") else ""))
        lines.append("")
    notes: List[str] = []
    if groups["yamaha_hex"][1]:
        notes.append("Yamaha hex codes — call `lookup_dtc(code)` for decode attempts + manual-search guidance.")
    if not data.rows:
        notes.append("Log has no row-level data — only metadata DTCs are available.")
    if notes:
        lines.append("Notes:")
        lines.extend(f"  • {n}" for n in notes)
    return "\n".join(lines).rstrip()


def _format_standard_lookup(code: str) -> str:
    code = code.upper()
    related = _RELATED_PIDS.get(code, [])
    lines = [f"DTC {code} — standard OBD-II code", "", f"Subsystem: {_subsystem_from_letter(code)}",
             "Description: no description available in the in-process DTC table. The code is recognised as "
             "standard format; consult the vehicle's manual for the manufacturer-specific definition."]
    if related:
        lines.append(f"Related signals to investigate: {', '.join(related)}")
    lines += ["", "Next step suggestions:",
              "  • `get_manual_toc(manual_id=...)` then `read_manual_section(...)` — pull the manufacturer's "
              "diagnostic procedure from the service manual."]
    if related:
        rel = ", ".join(f"'{p}'" for p in related[:3])
        lines.append(f"  • `get_signal_stats(signals=[{rel}])` — check the related signals' distributions.")
    return "\n".join(lines)


def _format_yamaha_lookup(code: str) -> str:
    return "\n".join([
        f"DTC {code.upper()} — Yamaha-proprietary raw hex", "",
        "No decoder available in this codebase. This is a Yamaha K-Line stored-DTC byte sequence "
        "(likely contains header bytes, code body, and checksum); standard P/C/B/U lookup tables do not apply.",
        "", "Recommended next steps:",
        "  • `get_manual_toc(manual_id=...)` — find the Yamaha DTC chart or fault code appendix section slug.",
        "  • `read_manual_section(manual_id=..., slug=...)` — read the DTC table and map this code by structure.",
        "", "Once you have the Yamaha-defined fault code, return to the OBD data with `get_signal_stats` and "
        "`find_events` on the implicated signals.",
    ])


def _format_frame_lookup(code: str) -> str:
    decoded = decode_mode43_frame(code)
    if decoded:
        return (f"'{code}' is a raw Mode 43 (stored DTC) response frame carrying: {', '.join(decoded)}. "
                f"Look up each decoded code with `lookup_dtc`.")
    return f"'{code}' is a raw Mode 43 response frame carrying no DTCs (empty response)."


def _format_unknown_lookup(code: str) -> str:
    return (f"DTC '{code}' is not a recognised OBD-II standard P/C/B/U code (4 hex digits) and does not "
            f"match the Yamaha hex format (10+ hex digits). Verify the code with the user, or use "
            f"`get_manual_toc` then `read_manual_section` to check whether the manual defines a "
            f"manufacturer-specific chart.")


async def lookup_dtc(ctx: RunContext[Any], code: str) -> str:
    """Decode one fault code.

    Standard P/C/B/U codes return subsystem + related signals to
    investigate (no fabricated descriptions). Yamaha-proprietary raw hex
    codes return an honest 'no decoder available' message with manual
    TOC navigation guidance. Use after `list_dtcs` to drill into a
    specific code.

    Args:
        code: DTC code. Standard format like 'P0117', or Yamaha proprietary
            raw hex like '87F11043000000000000CB'.
    """
    return await execute(ctx, "lookup_dtc", {"code": code}, lambda: _lookup_dtc(code))


async def _lookup_dtc(code: str) -> str:
    code = (code or "").strip()
    if not code:
        return "Validation error: `code` must be a non-empty string."
    cls = classify_code(code)
    if cls == "standard":
        return _format_standard_lookup(code)
    if cls == "yamaha_hex":
        return _format_yamaha_lookup(code)
    if cls == "mode43_frame":
        return _format_frame_lookup(code)
    return _format_unknown_lookup(code)


OBD_DTC_TOOLS = [list_dtcs, lookup_dtc]

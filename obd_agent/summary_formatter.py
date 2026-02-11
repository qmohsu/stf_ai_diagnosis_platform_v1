"""Format a LogSummaryV2 dict into flat strings for the Dify workflow.

Direct port of the inline Python code node from the Dify workflow
(``dify/OBD Log Diagnosis Expert.yml`` lines 199-329), refactored into
a tested, importable pure function.
"""

from __future__ import annotations

from typing import Dict


_EMPTY: Dict[str, str] = {
    "parse_ok": "NO",
    "vehicle_id": "",
    "time_range": "",
    "dtc_codes": "",
    "pid_summary": "",
    "anomaly_events": "",
    "diagnostic_clues": "",
    "rag_query": "",
    "debug": "",
}


def format_summary_for_dify(data: dict) -> dict:
    """Convert a ``LogSummaryV2.model_dump()`` dict into 9 flat-string fields.

    Returns a dict with keys matching :data:`_EMPTY`.  On any unexpected
    error the result has ``parse_ok="NO"`` and a ``debug`` message.
    """
    try:
        return _format_impl(data)
    except Exception as exc:
        result = dict(_EMPTY)
        result["debug"] = f"format error: {exc}"
        return result


# ------------------------------------------------------------------
# Private implementation
# ------------------------------------------------------------------


def _format_impl(data: dict) -> dict:
    vehicle_id = data.get("vehicle_id", "")

    # -- time_range --
    tr = data.get("time_range", {})
    time_range = (
        f"{tr.get('start', '?')} to {tr.get('end', '?')}, "
        f"{tr.get('duration_seconds', 0)}s, "
        f"{tr.get('sample_count', 0)} samples"
    )

    # -- dtc_codes --
    dtc_codes_list = data.get("dtc_codes", [])
    dtc_codes = ", ".join(dtc_codes_list) if dtc_codes_list else "None"

    # -- pid_summary (legacy summarizer) --
    pid_map = data.get("pid_summary", {})
    pid_lines = []
    for name, stats in pid_map.items():
        pid_lines.append(
            f"{name}: min={stats['min']} max={stats['max']} "
            f"mean={stats['mean']:.2f} latest={stats['latest']} "
            f"{stats.get('unit', '')}"
        )
    pid_summary = "\n".join(pid_lines) if pid_lines else "None"

    # -- anomaly_events (v2 structured) --
    events = data.get("anomaly_events") or []
    event_lines = []
    for ev in events:
        sigs = ", ".join(ev.get("signals", []))
        event_lines.append(
            f"[{ev.get('severity', '?').upper()}] {ev.get('pattern', '?')} "
            f"(context: {ev.get('context', '?')}, signals: {sigs}, "
            f"score: {ev.get('score', 0):.2f})"
        )
    anomaly_events = "\n".join(event_lines) if event_lines else "None"

    # -- diagnostic_clues (v2 rule-based) --
    clue_strings = data.get("diagnostic_clues") or []
    clue_details = data.get("clue_details") or []
    clue_lines = []
    for cd in clue_details:
        evidence = "; ".join(cd.get("evidence", []))
        clue_lines.append(
            f"[{cd.get('severity', '?').upper()}] {cd.get('rule_id', '?')}: "
            f"{cd.get('clue', '?')} (evidence: {evidence})"
        )
    diagnostic_clues = "\n".join(clue_lines) if clue_lines else "None"

    # -- rag_query (prioritized cascade) --
    query_parts: list[str] = []

    # Priority 1: DTC codes
    if dtc_codes_list:
        query_parts.append("DTC codes: " + " ".join(dtc_codes_list))

    # Priority 2: Diagnostic clues (most informative for RAG)
    if clue_strings:
        query_parts.append("Diagnostic clues: " + "; ".join(clue_strings[:5]))

    # Priority 3: Anomaly event patterns (only if no DTCs and no clues)
    if events and not query_parts:
        anomaly_desc = [ev.get("pattern", "") for ev in events[:5]]
        query_parts.append("Anomalies: " + "; ".join(anomaly_desc))

    # Fallback: PID names with value fluctuations
    if not query_parts and pid_map:
        fluctuating = [
            name for name, stats in pid_map.items()
            if stats["min"] != stats["max"]
        ]
        if fluctuating:
            query_parts.append("PID fluctuations: " + " ".join(fluctuating))

    # Final fallback
    rag_query = " | ".join(query_parts) if query_parts else "general OBD vehicle health check"

    return {
        "parse_ok": "YES",
        "vehicle_id": vehicle_id,
        "time_range": time_range,
        "dtc_codes": dtc_codes,
        "pid_summary": pid_summary,
        "anomaly_events": anomaly_events,
        "diagnostic_clues": diagnostic_clues,
        "rag_query": rag_query,
        "debug": f"OK, keys={list(data.keys())[:8]}",
    }

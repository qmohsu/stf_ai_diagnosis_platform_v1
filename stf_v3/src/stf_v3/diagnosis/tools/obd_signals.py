"""OBD investigation primitives — signal-side tools (copied from V2 HARNESS-19).

- ``list_signals``     — discovery (no samples returned).
- ``read_window``      — bounded, downsampled sample read (≤ 500 rows).
- ``get_signal_stats`` — summary statistics without rows.
- ``find_events``      — where does a signal meet a predicate?

All tools return plain text.  Parameter descriptions are the V2 ones,
verbatim, in the docstrings (Pydantic AI builds the schema from them —
FM-49).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic_ai import RunContext

from stf_v3.diagnosis.agent.deps import core_deps
from stf_v3.diagnosis.tools._common import execute
from stf_v3.diagnosis.tools.obd_signal_inventory import (
    SignalDescriptor,
    build_inventory,
    filter_inventory,
    fuzzy_suggestions,
    resolve_signal_name,
)
from stf_v3.ingest.loader import OBDLogData, parse_timestamp, try_float

_MAX_ROWS_CAP = 500
_MAX_SIGNALS_WINDOW = 8
_MAX_SIGNALS_STATS = 10
_MAX_EVENTS_CAP = 100


# ── Shared helpers ───────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def _session_time_range(rows: List[Dict[str, str]]) -> Tuple[Optional[datetime], Optional[datetime]]:
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    for row in rows:
        ts = parse_timestamp(row.get("Timestamp", ""))
        if ts is None:
            continue
        if first is None:
            first = ts
        last = ts
    return first, last


def _filter_rows_by_time(rows: List[Dict[str, str]], start: Optional[datetime], end: Optional[datetime]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in rows:
        ts = parse_timestamp(row.get("Timestamp", ""))
        if ts is None:
            continue
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            continue
        out.append(row)
    return out


def _unknown_signal_message(name: str, inventory: List[SignalDescriptor]) -> str:
    suggestions = fuzzy_suggestions(name, inventory)
    if suggestions:
        return (
            f"Signal '{name}' not in this log. Did you mean: {', '.join(suggestions)}? "
            f"Use `list_signals` to see all {len(inventory)} available signals."
        )
    return (
        f"Signal '{name}' not in this log and no close matches found. "
        f"Use `list_signals` to see all {len(inventory)} available signals."
    )


def _ensure_data(ctx: RunContext[Any]) -> Tuple[OBDLogData, List[SignalDescriptor]]:
    data = core_deps(ctx.deps).load_log()
    return data, build_inventory(data)


def _format_value(raw: str) -> str:
    f = try_float(raw)
    return "(N/A)" if f is None else f"{f:.2f}"


# ── list_signals ─────────────────────────────────────────────────


async def list_signals(
    ctx: RunContext[Any],
    pattern: Optional[str] = None,
    subsystem: Literal["engine", "abs", "all"] = "all",
) -> str:
    """List the signals (columns) present in this vehicle's OBD log.

    Returns time range, sampling rate, ECU channel presence, and a
    per-signal inventory with units and data density. Use FIRST to
    discover what signals exist. Cheap — call freely.

    Args:
        pattern: Glob-style filter on signal name (case-insensitive).
            Examples: '*TEMP*', 'A_YAM_*', 'RPM'. Omit to list all signals.
        subsystem: Filter by ECU subsystem. 'engine' = K-Line engine ECU
            (Channel A). 'abs' = CAN ABS ECU (Channel B). Defaults to 'all'.
    """
    return await execute(
        ctx, "list_signals", {"pattern": pattern, "subsystem": subsystem},
        lambda: _list_signals(ctx, pattern, subsystem),
    )


async def _list_signals(ctx: RunContext[Any], pattern: Optional[str], subsystem: str) -> str:
    data, inventory = _ensure_data(ctx)
    rows = data.rows
    first, last = _session_time_range(rows)
    duration = (last - first).total_seconds() if first and last else None
    interval_hz = (len(rows) - 1) / duration if duration and duration > 0 else None
    lines: List[str] = [f"Log has {len(rows)} samples in format '{data.format}'."]
    if first and last:
        lines.append(
            f"Time range: {first.isoformat()} → {last.isoformat()} "
            f"({_format_duration(duration or 0)}"
            + (f", ~{interval_hz:.2f} Hz" if interval_hz else "") + ")"
        )
    eng = "present" if "engine" in data.channels_present else "not present"
    abs_state = "present" if "abs" in data.channels_present else "not present"
    lines.append("Channels:")
    lines.append(f"  Engine ECU (K-Line / Channel A): {eng}")
    lines.append(f"  ABS ECU    (CAN / Channel B):    {abs_state}")
    filtered = filter_inventory(inventory, pattern, subsystem)  # type: ignore[arg-type]
    lines.append("")
    if pattern or subsystem != "all":
        active = []
        if pattern:
            active.append(f"pattern='{pattern}'")
        if subsystem != "all":
            active.append(f"subsystem='{subsystem}'")
        lines.append(f"Signals ({len(filtered)} match {' + '.join(active)} of {len(inventory)} total):")
    else:
        lines.append(f"Signals ({len(filtered)} total):")
    if not filtered:
        lines.append("  (no signals match the filter)")
    else:
        max_name = max(len(d.name) for d in filtered)
        max_unit = max(len(d.units) for d in filtered)
        for d in filtered:
            lines.append(f"  {d.name.ljust(max_name)}  {d.units.ljust(max_unit)}  {d.density_label}")
    if data.limitations:
        lines.append("")
        lines.append("Reader notes: " + "; ".join(data.limitations))
    return "\n".join(lines)


# ── read_window ──────────────────────────────────────────────────


async def read_window(
    ctx: RunContext[Any],
    signals: List[str],
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_rows: int = 50,
) -> str:
    """Read raw samples for one or more signals in a time window.

    Returns a tab-separated table with timestamps, values, and units.
    Auto-downsamples to max_rows (default 50, hard cap 500). Use AFTER
    list_signals to inspect specific values. Prefer get_signal_stats if
    you only need aggregates — much cheaper.

    Args:
        signals: Signal/column names to read (e.g. ['RPM', 'COOLANT_TEMP',
            'A_YAM_INJ_MS']). 1-8 signals per call. Use list_signals first
            to discover available names.
        start_time: Start of time window (ISO format, e.g.
            '2026-05-08T11:21:30'). Omit to read from session start.
        end_time: End of time window (ISO format). Omit to read to
            session end.
        max_rows: Max sample rows to return. If the window has more
            samples than this, the tool evenly downsamples. Hard cap 500
            (privacy boundary).
    """
    args = {"signals": signals, "start_time": start_time, "end_time": end_time, "max_rows": max_rows}
    return await execute(ctx, "read_window", args, lambda: _read_window(ctx, signals, start_time, end_time, max_rows))


async def _read_window(ctx: RunContext[Any], requested: List[str], start_raw: Optional[str], end_raw: Optional[str], max_rows: int) -> str:
    if not requested:
        return "Validation error: `signals` must name 1-8 signals."
    requested = requested[:_MAX_SIGNALS_WINDOW]
    max_rows = max(1, min(int(max_rows), _MAX_ROWS_CAP))
    data, inventory = _ensure_data(ctx)
    resolved: List[str] = []
    unknown: List[str] = []
    for s in requested:
        canon = resolve_signal_name(s, inventory)
        (unknown if canon is None else resolved).append(s if canon is None else canon)
    if not resolved:
        return _unknown_signal_message(unknown[0] if unknown else "", inventory)
    start = parse_timestamp(start_raw) if start_raw else None
    end = parse_timestamp(end_raw) if end_raw else None
    if start is not None and end is not None and start > end:
        return f"Invalid time window: start_time must precede end_time. Got start='{start_raw}', end='{end_raw}'."
    first, last = _session_time_range(data.rows)
    if start is not None and last is not None and start > last:
        return f"Requested start_time '{start_raw}' is after the log end ({last.isoformat()}). Window has 0 samples."
    if end is not None and first is not None and end < first:
        return f"Requested end_time '{end_raw}' is before the log start ({first.isoformat()}). Window has 0 samples."
    windowed = _filter_rows_by_time(data.rows, start, end)
    total = len(windowed)
    if total == 0:
        return (
            f"Window has 0 samples. Log spans {first.isoformat() if first else '?'} → "
            f"{last.isoformat() if last else '?'}. Adjust start_time/end_time or omit them."
        )
    truncated = total > max_rows
    if truncated:
        # Evenly spaced, always including the first and last sample, never
        # more than max_rows rows (hard cap 500 = the privacy boundary).
        if max_rows == 1:
            keep = [0]
        else:
            keep = sorted({round(i * (total - 1) / (max_rows - 1)) for i in range(max_rows)})
        rows_out = [windowed[i] for i in keep]
    else:
        rows_out = windowed
    header = ["Timestamp"] + resolved
    lines: List[str] = [f"Signal window — {', '.join(resolved)}"]
    win_first = parse_timestamp(rows_out[0].get("Timestamp", ""))
    win_last = parse_timestamp(rows_out[-1].get("Timestamp", ""))
    if win_first and win_last:
        lines.append(
            f"Window:  {win_first.isoformat()} → {win_last.isoformat()} "
            f"({_format_duration((win_last - win_first).total_seconds())}, "
            f"{len(rows_out)} of {total} samples" + (" [downsampled]" if truncated else "") + ")"
        )
    lines.append("")
    unit_lookup = {d.name: d.units for d in inventory}
    lines.append("\t".join(header))
    lines.append("\t".join(["units"] + [unit_lookup.get(s, "?") for s in resolved]))
    for r in rows_out:
        lines.append("\t".join([r.get("Timestamp", "")] + [_format_value(r.get(s, "")) for s in resolved]))
    notes: List[str] = []
    if unknown:
        notes.append(f"Ignored unrecognized signals: {unknown}")
    if truncated:
        notes.append("Auto-downsampled — set max_rows higher or narrow the time range for full resolution.")
    missing_notes = []
    for s in resolved:
        miss = sum(1 for r in rows_out if try_float(r.get(s, "")) is None)
        if miss:
            missing_notes.append(f"{s}: {miss}/{len(rows_out)} N/A")
    if missing_notes:
        notes.append("Missing samples — " + "; ".join(missing_notes))
    if notes:
        lines.append("")
        lines.append("Notes: " + " | ".join(notes))
    return "\n".join(lines)


# ── get_signal_stats ─────────────────────────────────────────────


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return math.nan
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def _compute_signal_stats(signal: str, rows: List[Dict[str, str]], include: List[str]) -> Dict[str, Any]:
    samples: List[Tuple[datetime, float]] = []
    for r in rows:
        ts = parse_timestamp(r.get("Timestamp", ""))
        v = try_float(r.get(signal, ""))
        if ts is not None and v is not None:
            samples.append((ts, v))
    out: Dict[str, Any] = {"signal": signal, "valid": len(samples), "total": len(rows)}
    if not samples:
        out["notes"] = "all N/A in window"
        return out
    values = [v for _, v in samples]
    sorted_vals = sorted(values)
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var)
    if "basic" in include:
        out.update(min=sorted_vals[0], max=sorted_vals[-1], mean=mean, std=std)
    if "percentiles" in include:
        for p in (5, 25, 50, 75, 95):
            out[f"p{p}"] = _percentile(sorted_vals, p)
    if "trend" in include and n >= 3:
        xs = list(range(n))
        x_mean = sum(xs) / n
        cov = sum((xs[i] - x_mean) * (values[i] - mean) for i in range(n))
        denom = sum((x - x_mean) ** 2 for x in xs)
        out["linreg_slope_per_sample"] = cov / denom if denom else 0.0
        if std > 0 and n > 2:
            cov1 = sum((values[i] - mean) * (values[i - 1] - mean) for i in range(1, n))
            out["autocorr_lag1"] = cov1 / ((n - 1) * var)
        else:
            out["autocorr_lag1"] = None
    elif "trend" in include:
        out["notes"] = "trend skipped — fewer than 3 samples"
    if "extrema" in include:
        max_idx = max(range(n), key=lambda i: values[i])
        min_idx = min(range(n), key=lambda i: values[i])
        out["max_at"] = samples[max_idx][0].isoformat()
        out["min_at"] = samples[min_idx][0].isoformat()
    return out


_DEFAULT_INCLUDE = ["basic", "percentiles"]
_STAT_KEYS = ("min", "max", "mean", "std", "p5", "p25", "p50", "p75", "p95",
              "linreg_slope_per_sample", "autocorr_lag1", "max_at", "min_at")


async def get_signal_stats(
    ctx: RunContext[Any],
    signals: List[str],
    time_range: Optional[Tuple[str, str]] = None,
    include: Optional[List[Literal["basic", "percentiles", "trend", "extrema"]]] = None,
) -> str:
    """Summarize 1-10 signals with descriptive statistics.

    Over a time range (or the full log). Returns min/max/mean/std/
    percentiles (p5/p25/p50/p75/p95), optionally trend (linear regression
    slope, lag-1 autocorrelation) and extrema timestamps. Use to answer
    'what's the distribution?' without pulling raw rows.

    Args:
        signals: Signal names to summarize (1-10 per call).
        time_range: Optional (start, end) ISO timestamps. Omit for full session.
        include: Which stat groups to include. Choose from 'basic'
            (min/max/mean/std/count), 'percentiles' (p5/p25/p50/p75/p95),
            'trend' (linreg_slope, autocorr_lag1), 'extrema' (timestamps of
            min and max). Default: ['basic', 'percentiles'].
    """
    args = {"signals": signals, "time_range": list(time_range) if time_range else None, "include": include}
    return await execute(ctx, "get_signal_stats", args, lambda: _get_signal_stats(ctx, signals, time_range, include))


async def _get_signal_stats(ctx: RunContext[Any], requested: List[str], time_range: Optional[Tuple[str, str]], include: Optional[List[str]]) -> str:
    if not requested:
        return "Validation error: `signals` must name 1-10 signals."
    requested = requested[:_MAX_SIGNALS_STATS]
    include = list(include) if include else _DEFAULT_INCLUDE
    data, inventory = _ensure_data(ctx)
    resolved: List[str] = []
    unknown: List[str] = []
    for s in requested:
        canon = resolve_signal_name(s, inventory)
        (unknown if canon is None else resolved).append(s if canon is None else canon)
    if not resolved:
        return _unknown_signal_message(unknown[0] if unknown else "", inventory)
    start = end = None
    if time_range:
        start, end = parse_timestamp(time_range[0]), parse_timestamp(time_range[1])
    rows = _filter_rows_by_time(data.rows, start, end)
    if not rows:
        return "No samples in the requested time range. Use `list_signals` to see the log time span."
    per_signal = [_compute_signal_stats(s, rows, include) for s in resolved]
    lines: List[str] = ["Signal statistics"]
    if start and end:
        lines.append(f"Window: {start.isoformat()} → {end.isoformat()} ({len(rows)} samples)")
    else:
        lines.append(f"Window: full session ({len(rows)} samples)")
    lines.append(f"Included: {', '.join(include)}")
    lines.append("")
    unit_lookup = {d.name: d.units for d in inventory}
    for stats in per_signal:
        sig = stats["signal"]
        lines.append(f"=== {sig} ({unit_lookup.get(sig, '?')}) — {stats['valid']}/{stats['total']} valid samples ===")
        for k in _STAT_KEYS:
            if k in stats and stats[k] is not None:
                val = stats[k]
                lines.append(f"  {k}: {val:.4f}" if isinstance(val, float) else f"  {k}: {val}")
        if "notes" in stats:
            lines.append(f"  notes: {stats['notes']}")
        lines.append("")
    if unknown:
        lines.append(f"Ignored unrecognized signals: {unknown}")
    return "\n".join(lines).rstrip()


# ── find_events ──────────────────────────────────────────────────


_UPPER = ("above_threshold", "rising_above", "rate_of_change_above")
_LOWER = ("below_threshold", "falling_below", "rate_of_change_below")


def _predicate_holds(predicate: str, threshold: Optional[float], value: Optional[float], prev_value: Optional[float], dt: Optional[float]) -> bool:
    if predicate == "missing":
        return value is None
    if value is None or threshold is None:
        return False
    if predicate == "above_threshold":
        return value > threshold
    if predicate == "below_threshold":
        return value < threshold
    if predicate == "rising_above":
        return prev_value is not None and prev_value <= threshold < value
    if predicate == "falling_below":
        return prev_value is not None and prev_value >= threshold > value
    if predicate in ("rate_of_change_above", "rate_of_change_below"):
        if prev_value is None or dt is None or dt <= 0:
            return False
        rate = (value - prev_value) / dt
        return rate > threshold if predicate == "rate_of_change_above" else rate < threshold
    return False


async def find_events(
    ctx: RunContext[Any],
    signal: str,
    predicate: Literal["above_threshold", "below_threshold", "rising_above", "falling_below",
                       "rate_of_change_above", "rate_of_change_below", "missing"],
    threshold: Optional[float] = None,
    min_duration_seconds: float = 1.0,
    merge_gap_seconds: float = 2.0,
    time_range: Optional[Tuple[str, str]] = None,
    max_events: int = 20,
) -> str:
    """Find time windows where a signal meets a condition.

    Above/below threshold, rising_above/falling_below crossings,
    rate_of_change_above/below in units-per-second, or missing N/A.
    Returns event spans with peak values. Use to locate 'when did X
    happen' without scanning the whole log. Predicates other than
    'missing' require a threshold parameter.

    Args:
        signal: Signal name to scan.
        predicate: Condition to find. 'above_threshold'/'below_threshold'
            require threshold parameter. 'rising_above'/'falling_below'
            require threshold; match only at crossings (first sample where
            signal crosses from one side to the other).
            'rate_of_change_above'/'rate_of_change_below' require threshold
            and operate on finite differences (value units per second).
            'missing' finds N/A windows; threshold ignored.
        threshold: Threshold value (required for all predicates except 'missing').
        min_duration_seconds: Drop events shorter than this. Useful for
            filtering single-sample spikes.
        merge_gap_seconds: Merge adjacent events whose gap is below this
            duration. 0 disables merging.
        time_range: Optional (start, end) ISO timestamps. Omit for full session.
        max_events: Max events to return (most recent if more match).
    """
    args = {"signal": signal, "predicate": predicate, "threshold": threshold,
            "min_duration_seconds": min_duration_seconds, "merge_gap_seconds": merge_gap_seconds,
            "time_range": list(time_range) if time_range else None, "max_events": max_events}
    return await execute(ctx, "find_events", args, lambda: _find_events(
        ctx, signal, predicate, threshold, min_duration_seconds, merge_gap_seconds, time_range, max_events))


async def _find_events(ctx: RunContext[Any], signal_req: str, predicate: str, threshold: Optional[float],
                       min_duration: float, merge_gap: float, time_range: Optional[Tuple[str, str]], max_events: int) -> str:
    max_events = max(1, min(int(max_events), _MAX_EVENTS_CAP))
    if predicate != "missing" and threshold is None:
        return (
            f"Validation error: predicate '{predicate}' requires the `threshold` parameter. "
            f"Example: find_events(signal='{signal_req}', predicate='{predicate}', threshold=3000)."
        )
    data, inventory = _ensure_data(ctx)
    signal = resolve_signal_name(signal_req, inventory)
    if signal is None:
        return _unknown_signal_message(signal_req, inventory)
    start = end = None
    if time_range:
        start, end = parse_timestamp(time_range[0]), parse_timestamp(time_range[1])
    rows = _filter_rows_by_time(data.rows, start, end)
    if not rows:
        return "No samples in the requested time range."
    sequence: List[Tuple[datetime, Optional[float]]] = []
    for r in rows:
        ts = parse_timestamp(r.get("Timestamp", ""))
        if ts is not None:
            sequence.append((ts, try_float(r.get(signal, ""))))
    if not sequence:
        return f"Signal '{signal}' has no valid timestamped rows."
    events: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    prev_val: Optional[float] = None
    prev_ts: Optional[datetime] = None
    for ts, v in sequence:
        dt = (ts - prev_ts).total_seconds() if prev_ts is not None else None
        if _predicate_holds(predicate, threshold, v, prev_val, dt):
            if cur is None:
                cur = {"start": ts, "end": ts, "peak": v}
            cur["end"] = ts
            if v is not None:
                if cur["peak"] is None:
                    cur["peak"] = v
                elif predicate in _UPPER:
                    cur["peak"] = max(cur["peak"], v)
                elif predicate in _LOWER:
                    cur["peak"] = min(cur["peak"], v)
        elif cur is not None:
            events.append(cur)
            cur = None
        prev_val, prev_ts = v, ts
    if cur is not None:
        events.append(cur)
    filtered = []
    for ev in events:
        dur = (ev["end"] - ev["start"]).total_seconds()
        if dur >= min_duration:
            ev["duration_s"] = dur
            filtered.append(ev)
    merged: List[Dict[str, Any]] = []
    for ev in filtered:
        if merged and (ev["start"] - merged[-1]["end"]).total_seconds() <= merge_gap:
            m = merged[-1]
            m["end"] = ev["end"]
            m["duration_s"] = (m["end"] - m["start"]).total_seconds()
            a, b = m["peak"], ev["peak"]
            if a is None:
                m["peak"] = b
            elif b is not None:
                m["peak"] = max(a, b) if predicate in _UPPER else (min(a, b) if predicate in _LOWER else a)
        else:
            merged.append(ev)
    truncated = len(merged) > max_events
    shown = merged[:max_events]
    unit = next((d.units for d in inventory if d.name == signal), "?")
    if predicate == "missing":
        header = f"Events — signal '{signal}' missing (N/A) — {len(shown)} of {len(merged)} found"
    else:
        header = f"Events — '{signal}' {predicate} {threshold} {unit} — {len(shown)} of {len(merged)} found"
    lines: List[str] = [header + (" [truncated]" if truncated else ""),
                        f"Filters: min_duration={min_duration}s, merge_gap={merge_gap}s", ""]
    if not shown:
        if predicate != "missing":
            numeric = [v for _, v in sequence if v is not None]
            if numeric:
                lines.append(
                    f"No events matched. Signal range in this window: min={min(numeric):.2f}, "
                    f"max={max(numeric):.2f} {unit}. Consider adjusting the threshold."
                )
            else:
                lines.append(f"No events matched — signal '{signal}' has no valid samples in the window.")
        else:
            lines.append("No events matched.")
        return "\n".join(lines)
    for i, ev in enumerate(shown, start=1):
        peak = ev["peak"]
        peak_str = f"peak={peak:.2f}" if peak is not None else "peak=N/A"
        lines.append(f"#{i}  {ev['start'].isoformat()} → {ev['end'].isoformat()}  "
                     f"({_format_duration(ev['duration_s'])})  {peak_str} {unit}")
    return "\n".join(lines)


OBD_SIGNAL_TOOLS = [list_signals, read_window, get_signal_stats, find_events]

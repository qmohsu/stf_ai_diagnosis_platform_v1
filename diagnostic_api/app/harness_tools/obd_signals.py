"""OBD investigation primitives — signal-side tools (HARNESS-19).

Four cognitive primitives the agent uses to interrogate the raw OBD
log without seeing it all at once:

- ``list_signals``  — Glob analog. Discovery.
- ``read_window``   — Read analog. Bounded windowed sample read.
- ``get_signal_stats`` — Aggregate primitive. Summary without rows.
- ``find_events``   — Grep analog. Where does signal meet predicate?

All tools return plain text. Each one tolerates per-tool quirks of
the Yamaha fixture (sparse first row, ``A_YAM_*`` proprietary
columns, comma-separated CSV).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import (
    FindEventsInput,
    GetSignalStatsInput,
    ListSignalsInput,
    ReadWindowInput,
)
from app.harness_tools.obd_loader import (
    OBDLogData,
    load_for_session,
    parse_timestamp,
    try_float,
)
from app.harness_tools.obd_signal_inventory import (
    SignalDescriptor,
    build_inventory,
    filter_inventory,
    fuzzy_suggestions,
    resolve_signal_name,
)

logger = structlog.get_logger(__name__)


# ── Shared helpers ───────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    """Render a duration in compact ``Xm Ys`` form."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s"


def _session_time_range(
    rows: List[Dict[str, str]],
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Find the (first, last) valid timestamps in row order."""
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


def _filter_rows_by_time(
    rows: List[Dict[str, str]],
    start: Optional[datetime],
    end: Optional[datetime],
) -> List[Dict[str, str]]:
    """Return rows whose timestamp is in ``[start, end]`` inclusive."""
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


def _parse_window_bound(raw: Optional[str]):
    """Parse an ISO timestamp window bound, returning None on bad input."""
    if not raw:
        return None
    return parse_timestamp(raw)


def _unknown_signal_message(
    name: str,
    inventory: List[SignalDescriptor],
) -> str:
    """Compose an actionable T2 error for an unrecognized signal."""
    suggestions = fuzzy_suggestions(name, inventory)
    if suggestions:
        sug = ", ".join(suggestions)
        return (
            f"Signal '{name}' not in this session. "
            f"Did you mean: {sug}? "
            f"Use `list_signals` to see all "
            f"{len(inventory)} available signals."
        )
    return (
        f"Signal '{name}' not in this session and no close "
        f"matches found. Use `list_signals` to see all "
        f"{len(inventory)} available signals."
    )


def _ensure_data(session_id: str) -> Tuple[OBDLogData, List[SignalDescriptor]]:
    """Load + build inventory for a session in one call."""
    data = load_for_session(session_id)
    inventory = build_inventory(data)
    return data, inventory


# ── list_signals ─────────────────────────────────────────────────


async def list_signals(input_data: Dict[str, Any]) -> str:
    """Enumerate signals available in the session, with filters.

    Returns time range, sampling rate (best-effort), channel
    presence, and per-signal name + units + density.  No samples
    returned.

    Args:
        input_data: Validated ``ListSignalsInput`` fields plus the
            loop-injected ``_session_id``.

    Returns:
        Text inventory.
    """
    session_id = input_data["_session_id"]
    pattern = input_data.get("pattern")
    subsystem = input_data.get("subsystem", "all")

    data, inventory = _ensure_data(session_id)
    rows = data.rows

    first, last = _session_time_range(rows)
    duration = (
        (last - first).total_seconds()
        if first and last else None
    )
    interval_hz = (
        (len(rows) - 1) / duration
        if duration and duration > 0 else None
    )

    lines: List[str] = []
    lines.append(
        f"Session has {len(rows)} samples in format "
        f"'{data.format}'."
    )
    if first and last:
        lines.append(
            f"Time range: {first.isoformat()} → "
            f"{last.isoformat()} "
            f"({_format_duration(duration or 0)}"
            + (f", ~{interval_hz:.2f} Hz" if interval_hz else "")
            + ")"
        )

    # Channel presence
    eng = "present" if "engine" in data.channels_present else "not present"
    abs_state = "present" if "abs" in data.channels_present else "not present"
    lines.append("Channels:")
    lines.append(f"  Engine ECU (K-Line / Channel A): {eng}")
    lines.append(f"  ABS ECU    (CAN / Channel B):    {abs_state}")

    # Filter inventory
    filtered = filter_inventory(inventory, pattern, subsystem)

    lines.append("")
    if pattern or subsystem != "all":
        active_filters = []
        if pattern:
            active_filters.append(f"pattern='{pattern}'")
        if subsystem != "all":
            active_filters.append(f"subsystem='{subsystem}'")
        lines.append(
            f"Signals ({len(filtered)} match "
            f"{' + '.join(active_filters)} "
            f"of {len(inventory)} total):"
        )
    else:
        lines.append(f"Signals ({len(filtered)} total):")

    if not filtered:
        lines.append(
            "  (no signals match the filter)"
        )
    else:
        # Column-align name and units
        max_name = max(len(d.name) for d in filtered)
        max_unit = max(len(d.units) for d in filtered)
        for d in filtered:
            lines.append(
                f"  {d.name.ljust(max_name)}  "
                f"{d.units.ljust(max_unit)}  "
                f"{d.density_label}"
            )

    return "\n".join(lines)


# ── read_window ──────────────────────────────────────────────────


def _format_value(raw: str) -> str:
    """Render a cell as ``f"{x:.2f}"`` when numeric, else echo."""
    f = try_float(raw)
    if f is None:
        return "(N/A)"
    return f"{f:.2f}"


async def read_window(input_data: Dict[str, Any]) -> str:
    """Read samples for one or more signals in a time window.

    Auto-downsamples to ``max_rows`` by even spacing when the window
    has more samples.  Always preserves the first and last row of
    the window so the agent sees boundary behavior.

    Args:
        input_data: Validated ``ReadWindowInput`` + injected
            ``_session_id``.

    Returns:
        Tabular text or an actionable error string.
    """
    session_id = input_data["_session_id"]
    requested_signals: List[str] = input_data["signals"]
    start_raw: Optional[str] = input_data.get("start_time")
    end_raw: Optional[str] = input_data.get("end_time")
    max_rows: int = int(input_data.get("max_rows", 50))

    data, inventory = _ensure_data(session_id)

    # Validate / resolve signal names.
    resolved: List[str] = []
    unknown: List[str] = []
    for s in requested_signals:
        canon = resolve_signal_name(s, inventory)
        if canon is None:
            unknown.append(s)
        else:
            resolved.append(canon)

    if not resolved:
        return _unknown_signal_message(
            unknown[0] if unknown else "",
            inventory,
        )

    # Parse window bounds.
    start = _parse_window_bound(start_raw)
    end = _parse_window_bound(end_raw)
    if start is not None and end is not None and start > end:
        return (
            "Invalid time window: start_time must precede "
            "end_time. Got "
            f"start='{start_raw}', end='{end_raw}'."
        )

    first, last = _session_time_range(data.rows)
    if start is not None and last is not None and start > last:
        return (
            f"Requested start_time '{start_raw}' is after the "
            f"session end ({last.isoformat()}). Window has 0 "
            f"samples."
        )
    if end is not None and first is not None and end < first:
        return (
            f"Requested end_time '{end_raw}' is before the "
            f"session start ({first.isoformat()}). Window has "
            f"0 samples."
        )

    windowed = _filter_rows_by_time(data.rows, start, end)
    total = len(windowed)

    if total == 0:
        return (
            f"Window has 0 samples. Session spans "
            f"{first.isoformat() if first else '?'} → "
            f"{last.isoformat() if last else '?'}. "
            f"Adjust start_time/end_time or omit them."
        )

    # Downsample evenly.
    truncated = total > max_rows
    if truncated:
        # Always include first + last; evenly sample the middle.
        step = total / max_rows
        keep_indices = sorted({
            min(int(i * step), total - 1)
            for i in range(max_rows)
        } | {0, total - 1})
        rows_out = [windowed[i] for i in keep_indices]
    else:
        rows_out = windowed

    # Build table.
    header = ["Timestamp"] + resolved
    lines: List[str] = []
    lines.append(
        f"Signal window — {', '.join(resolved)}"
    )
    if first and last:
        win_first = parse_timestamp(rows_out[0].get("Timestamp", ""))
        win_last = parse_timestamp(rows_out[-1].get("Timestamp", ""))
        if win_first and win_last:
            lines.append(
                f"Window:  {win_first.isoformat()} → "
                f"{win_last.isoformat()} "
                f"({_format_duration((win_last - win_first).total_seconds())}, "
                f"{len(rows_out)} of {total} samples"
                + (" [downsampled]" if truncated else "")
                + ")"
            )
    lines.append("")

    # Units row.
    unit_lookup = {d.name: d.units for d in inventory}
    unit_row = ["units"] + [
        unit_lookup.get(s, "?") for s in resolved
    ]
    lines.append("\t".join(header))
    lines.append("\t".join(unit_row))
    for r in rows_out:
        cells = [r.get("Timestamp", "")] + [
            _format_value(r.get(s, "")) for s in resolved
        ]
        lines.append("\t".join(cells))

    notes: List[str] = []
    if unknown:
        notes.append(
            f"Ignored unrecognized signals: {unknown}"
        )
    if truncated:
        notes.append(
            f"Auto-downsampled — set max_rows higher or narrow "
            f"the time range for full resolution."
        )
    # Missing-data per signal.
    missing_notes: List[str] = []
    for s in resolved:
        miss = sum(
            1 for r in rows_out
            if try_float(r.get(s, "")) is None
        )
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
    """Inclusive linear-interpolation percentile.

    Mirrors numpy's default (``linear`` method).  Assumes
    ``sorted_vals`` is non-empty and sorted ascending.
    """
    if not sorted_vals:
        return math.nan
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return (
        sorted_vals[lo] * (hi - k)
        + sorted_vals[hi] * (k - lo)
    )


def _compute_signal_stats(
    signal: str,
    rows: List[Dict[str, str]],
    include: List[str],
) -> Dict[str, Any]:
    """Compute requested stat groups for a single signal."""
    samples: List[Tuple[datetime, float]] = []
    for r in rows:
        ts = parse_timestamp(r.get("Timestamp", ""))
        v = try_float(r.get(signal, ""))
        if ts is not None and v is not None:
            samples.append((ts, v))

    out: Dict[str, Any] = {
        "signal": signal,
        "valid": len(samples),
        "total": len(rows),
    }

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
        out["min"] = sorted_vals[0]
        out["max"] = sorted_vals[-1]
        out["mean"] = mean
        out["std"] = std

    if "percentiles" in include:
        out["p5"] = _percentile(sorted_vals, 5)
        out["p25"] = _percentile(sorted_vals, 25)
        out["p50"] = _percentile(sorted_vals, 50)
        out["p75"] = _percentile(sorted_vals, 75)
        out["p95"] = _percentile(sorted_vals, 95)

    if "trend" in include and n >= 3:
        # Simple linear regression slope vs. sample index (per-sec
        # gradient would need uniform spacing — index slope is
        # safer and still tells the agent if it's rising/falling).
        xs = list(range(n))
        x_mean = sum(xs) / n
        cov = sum(
            (xs[i] - x_mean) * (values[i] - mean)
            for i in range(n)
        )
        denom = sum((x - x_mean) ** 2 for x in xs)
        slope = cov / denom if denom else 0.0
        out["linreg_slope_per_sample"] = slope
        # Autocorr lag-1
        if std > 0 and n > 2:
            cov1 = sum(
                (values[i] - mean) * (values[i - 1] - mean)
                for i in range(1, n)
            )
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


async def get_signal_stats(input_data: Dict[str, Any]) -> str:
    """Summarize signals with descriptive statistics.

    Returns a per-signal stats block.  Reuses the in-house stat
    machinery (kept local rather than depending on
    ``statistics_extractor`` to avoid pulling in the
    NormalizedTimeSeries pipeline — which strips Yamaha proprietary
    columns).  Output values match numpy reference within
    floating-point tolerance.

    Args:
        input_data: ``GetSignalStatsInput`` + ``_session_id``.

    Returns:
        Tabular text.
    """
    session_id = input_data["_session_id"]
    requested_signals: List[str] = input_data["signals"]
    time_range = input_data.get("time_range")
    include = input_data.get("include") or _DEFAULT_INCLUDE

    data, inventory = _ensure_data(session_id)

    resolved: List[str] = []
    unknown: List[str] = []
    for s in requested_signals:
        canon = resolve_signal_name(s, inventory)
        if canon is None:
            unknown.append(s)
        else:
            resolved.append(canon)

    if not resolved:
        return _unknown_signal_message(
            unknown[0] if unknown else "",
            inventory,
        )

    # Apply time-range filter.
    start = end = None
    if time_range:
        start_raw, end_raw = time_range
        start = _parse_window_bound(start_raw)
        end = _parse_window_bound(end_raw)
    rows = _filter_rows_by_time(data.rows, start, end)

    if not rows:
        return (
            "No samples in the requested time range. Use "
            "`list_signals` to see the session time span."
        )

    # Compute stats per signal.
    per_signal = [
        _compute_signal_stats(s, rows, include)
        for s in resolved
    ]

    # Render
    lines: List[str] = []
    lines.append("Signal statistics")
    if start and end:
        lines.append(
            f"Window: {start.isoformat()} → {end.isoformat()} "
            f"({len(rows)} samples)"
        )
    else:
        lines.append(f"Window: full session ({len(rows)} samples)")
    lines.append(f"Included: {', '.join(include)}")
    lines.append("")

    unit_lookup = {d.name: d.units for d in inventory}

    for stats in per_signal:
        sig = stats["signal"]
        unit = unit_lookup.get(sig, "?")
        lines.append(
            f"=== {sig} ({unit}) — "
            f"{stats['valid']}/{stats['total']} valid samples ==="
        )
        for k in (
            "min", "max", "mean", "std",
            "p5", "p25", "p50", "p75", "p95",
            "linreg_slope_per_sample", "autocorr_lag1",
            "max_at", "min_at",
        ):
            if k in stats and stats[k] is not None:
                val = stats[k]
                if isinstance(val, float):
                    lines.append(f"  {k}: {val:.4f}")
                else:
                    lines.append(f"  {k}: {val}")
        if "notes" in stats:
            lines.append(f"  notes: {stats['notes']}")
        lines.append("")

    if unknown:
        lines.append(
            f"Ignored unrecognized signals: {unknown}"
        )

    return "\n".join(lines).rstrip()


# ── find_events ──────────────────────────────────────────────────


def _predicate_holds(
    predicate: str,
    threshold: Optional[float],
    value: Optional[float],
    prev_value: Optional[float],
    dt_seconds: Optional[float],
) -> bool:
    """Evaluate a predicate at one sample.

    For ``rising_above`` / ``falling_below`` the event is the
    *crossing* — defined as ``prev`` on the wrong side and current
    on the correct side.  For ``rate_of_change_*`` we use the
    finite difference ``(value - prev) / dt`` in units per second.
    """
    if predicate == "missing":
        return value is None
    if value is None:
        return False
    if threshold is None:
        return False

    if predicate == "above_threshold":
        return value > threshold
    if predicate == "below_threshold":
        return value < threshold
    if predicate == "rising_above":
        if prev_value is None:
            return False
        return prev_value <= threshold < value
    if predicate == "falling_below":
        if prev_value is None:
            return False
        return prev_value >= threshold > value
    if predicate == "rate_of_change_above":
        if prev_value is None or dt_seconds is None or dt_seconds <= 0:
            return False
        return (value - prev_value) / dt_seconds > threshold
    if predicate == "rate_of_change_below":
        if prev_value is None or dt_seconds is None or dt_seconds <= 0:
            return False
        return (value - prev_value) / dt_seconds < threshold
    return False


async def find_events(input_data: Dict[str, Any]) -> str:
    """Find time windows where a signal meets a predicate.

    Returns a list of ``(start, end, duration, peak)`` events
    sorted by start time, with short events filtered out and
    adjacent events merged per the input options.

    Args:
        input_data: ``FindEventsInput`` + ``_session_id``.

    Returns:
        Event-list text.
    """
    session_id = input_data["_session_id"]
    signal_req: str = input_data["signal"]
    predicate: str = input_data["predicate"]
    threshold: Optional[float] = input_data.get("threshold")
    min_duration: float = float(
        input_data.get("min_duration_seconds", 1.0),
    )
    merge_gap: float = float(
        input_data.get("merge_gap_seconds", 2.0),
    )
    max_events: int = int(input_data.get("max_events", 20))
    time_range = input_data.get("time_range")

    if predicate != "missing" and threshold is None:
        return (
            f"Validation error: predicate '{predicate}' requires "
            f"the `threshold` parameter. Example: "
            f"find_events(signal='{signal_req}', predicate="
            f"'{predicate}', threshold=3000)."
        )

    data, inventory = _ensure_data(session_id)
    signal = resolve_signal_name(signal_req, inventory)
    if signal is None:
        return _unknown_signal_message(signal_req, inventory)

    start = end = None
    if time_range:
        start_raw, end_raw = time_range
        start = _parse_window_bound(start_raw)
        end = _parse_window_bound(end_raw)
    rows = _filter_rows_by_time(data.rows, start, end)

    if not rows:
        return (
            "No samples in the requested time range."
        )

    # Walk rows.  Build (timestamp, value) sequence including
    # missing values for the 'missing' predicate.
    sequence: List[Tuple[datetime, Optional[float]]] = []
    for r in rows:
        ts = parse_timestamp(r.get("Timestamp", ""))
        if ts is None:
            continue
        sequence.append((ts, try_float(r.get(signal, ""))))

    if not sequence:
        return (
            f"Signal '{signal}' has no valid timestamped rows."
        )

    # Collect raw event spans.
    events: List[Dict[str, Any]] = []
    in_event = False
    cur_start: Optional[datetime] = None
    cur_end: Optional[datetime] = None
    cur_peak: Optional[float] = None
    prev_val: Optional[float] = None
    prev_ts: Optional[datetime] = None

    def _close_event():
        nonlocal cur_start, cur_end, cur_peak, in_event
        if cur_start is not None and cur_end is not None:
            events.append({
                "start": cur_start,
                "end": cur_end,
                "peak": cur_peak,
            })
        cur_start = cur_end = cur_peak = None
        in_event = False

    for ts, v in sequence:
        dt = (
            (ts - prev_ts).total_seconds()
            if prev_ts is not None else None
        )
        hit = _predicate_holds(
            predicate, threshold, v, prev_val, dt,
        )
        if hit:
            if not in_event:
                cur_start = ts
                cur_peak = v
                in_event = True
            cur_end = ts
            if v is not None:
                if cur_peak is None:
                    cur_peak = v
                elif predicate in (
                    "above_threshold", "rising_above",
                    "rate_of_change_above",
                ):
                    cur_peak = max(cur_peak, v)
                elif predicate in (
                    "below_threshold", "falling_below",
                    "rate_of_change_below",
                ):
                    cur_peak = min(cur_peak, v)
        else:
            if in_event:
                _close_event()
        prev_val = v
        prev_ts = ts
    if in_event:
        _close_event()

    # Filter by min_duration.
    filtered = []
    for ev in events:
        dur = (ev["end"] - ev["start"]).total_seconds()
        if dur >= min_duration:
            ev["duration_s"] = dur
            filtered.append(ev)

    # Merge adjacent events whose gap < merge_gap.
    merged: List[Dict[str, Any]] = []
    for ev in filtered:
        if not merged:
            merged.append(ev)
            continue
        gap = (ev["start"] - merged[-1]["end"]).total_seconds()
        if gap <= merge_gap:
            merged[-1]["end"] = ev["end"]
            merged[-1]["duration_s"] = (
                merged[-1]["end"] - merged[-1]["start"]
            ).total_seconds()
            # Update peak.
            a = merged[-1]["peak"]
            b = ev["peak"]
            if a is None:
                merged[-1]["peak"] = b
            elif b is None:
                pass
            elif predicate in (
                "above_threshold", "rising_above",
                "rate_of_change_above",
            ):
                merged[-1]["peak"] = max(a, b)
            elif predicate in (
                "below_threshold", "falling_below",
                "rate_of_change_below",
            ):
                merged[-1]["peak"] = min(a, b)
        else:
            merged.append(ev)

    truncated = len(merged) > max_events
    shown = merged[:max_events]

    unit = next(
        (d.units for d in inventory if d.name == signal), "?",
    )
    lines: List[str] = []
    if predicate == "missing":
        header = (
            f"Events — signal '{signal}' missing (N/A) — "
            f"{len(shown)} of {len(merged)} found"
            + (" [truncated]" if truncated else "")
        )
    else:
        header = (
            f"Events — '{signal}' {predicate} {threshold} {unit} — "
            f"{len(shown)} of {len(merged)} found"
            + (" [truncated]" if truncated else "")
        )
    lines.append(header)
    lines.append(
        f"Filters: min_duration={min_duration}s, "
        f"merge_gap={merge_gap}s"
    )
    lines.append("")

    if not shown:
        # Provide a helpful "max in range" anchor when no events
        # match a value predicate.
        if predicate in (
            "above_threshold", "rising_above",
            "rate_of_change_above",
            "below_threshold", "falling_below",
            "rate_of_change_below",
        ):
            numeric_vals = [
                v for _, v in sequence if v is not None
            ]
            if numeric_vals:
                lines.append(
                    f"No events matched. Signal range in this "
                    f"window: min={min(numeric_vals):.2f}, "
                    f"max={max(numeric_vals):.2f} {unit}. "
                    f"Consider adjusting the threshold."
                )
            else:
                lines.append(
                    f"No events matched — signal '{signal}' has "
                    f"no valid samples in the window."
                )
        else:
            lines.append("No events matched.")
        return "\n".join(lines)

    for i, ev in enumerate(shown, start=1):
        peak = ev["peak"]
        peak_str = (
            f"peak={peak:.2f}" if peak is not None else "peak=N/A"
        )
        lines.append(
            f"#{i}  {ev['start'].isoformat()} → "
            f"{ev['end'].isoformat()}  "
            f"({_format_duration(ev['duration_s'])})  "
            f"{peak_str} {unit}"
        )

    return "\n".join(lines)


# ── ToolDefinition exports ───────────────────────────────────────


_LIST_SIGNALS_DESC = (
    "List the signals (columns) present in this session's OBD "
    "log. Returns time range, sampling rate, ECU channel "
    "presence, and a per-signal inventory with units and data "
    "density. Use FIRST to discover what signals exist; "
    "filter with pattern (glob, e.g. '*TEMP*', 'A_YAM_*') and "
    "subsystem ('engine'/'abs'/'all'). Cheap — call freely."
)

_READ_WINDOW_DESC = (
    "Read raw samples for one or more signals in a time window. "
    "Returns a tab-separated table with timestamps, values, and "
    "units. Auto-downsamples to max_rows (default 50, hard cap "
    "500). Use AFTER list_signals to inspect specific values. "
    "Prefer get_signal_stats if you only need aggregates — much "
    "cheaper."
)

_GET_SIGNAL_STATS_DESC = (
    "Summarize 1-10 signals with descriptive statistics over a "
    "time range (or full session). Returns min/max/mean/std/"
    "percentiles (p5/p25/p50/p75/p95), optionally trend (linear "
    "regression slope, lag-1 autocorrelation) and extrema "
    "timestamps. Use to answer 'what's the distribution?' "
    "without pulling raw rows."
)

_FIND_EVENTS_DESC = (
    "Find time windows where a signal meets a condition "
    "(above/below threshold, rising_above/falling_below "
    "crossings, rate_of_change_above/below in units-per-second, "
    "or missing N/A). Returns event spans with peak values. "
    "Use to locate 'when did X happen' without scanning the "
    "whole log. Predicates other than 'missing' require a "
    "threshold parameter."
)


LIST_SIGNALS_DEF = ToolDefinition(
    name="list_signals",
    description=_LIST_SIGNALS_DESC,
    input_schema=ListSignalsInput.model_json_schema(),
    handler=list_signals,
    input_model=ListSignalsInput,
    is_read_only=True,
    max_result_chars=10_000,
)


READ_WINDOW_DEF = ToolDefinition(
    name="read_window",
    description=_READ_WINDOW_DESC,
    input_schema=ReadWindowInput.model_json_schema(),
    handler=read_window,
    input_model=ReadWindowInput,
    is_read_only=True,
    max_result_chars=50_000,
)


GET_SIGNAL_STATS_DEF = ToolDefinition(
    name="get_signal_stats",
    description=_GET_SIGNAL_STATS_DESC,
    input_schema=GetSignalStatsInput.model_json_schema(),
    handler=get_signal_stats,
    input_model=GetSignalStatsInput,
    is_read_only=True,
    max_result_chars=20_000,
)


FIND_EVENTS_DEF = ToolDefinition(
    name="find_events",
    description=_FIND_EVENTS_DESC,
    input_schema=FindEventsInput.model_json_schema(),
    handler=find_events,
    input_model=FindEventsInput,
    is_read_only=True,
    max_result_chars=20_000,
)

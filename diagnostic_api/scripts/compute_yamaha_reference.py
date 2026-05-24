"""Developer aid: print deterministic reference stats for the
Yamaha road-test fixture.

Used during HARNESS-21 PR [2/3] when hand-authoring golden entries
for ``v1/yamaha_road_test.jsonl`` — the human writes the question,
summary, and pitfall directives; the deterministic numerical fields
(``expected_signal_citations[i].value``) are copied from this
script's output rather than eyeballed from the CSV.

This script is intentionally NOT the LLM-driven
``generate_golden_candidates.py``-equivalent for OBD; that tool was
deferred (see issue #97 § "Phasing").  This is a simple,
deterministic stats dump — no LLM, no I/O beyond reading the fixture
and writing to stdout.

Usage:
    python -m diagnostic_api.scripts.compute_yamaha_reference

Author: Li-Ta Hsu
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.harness_tools.obd_loader import (
    OBDLogData,
    load_obd_data,
    try_float,
)


# ── Constants ────────────────────────────────────────────────────


_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "obd_agent" / "fixtures"
    / "yamaha_dual_road_test_20260508.csv"
)
"""Path to the canonical road-test fixture (committed to git)."""


_EVENT_THRESHOLDS: List[Tuple[str, str, float]] = [
    # (column_name, operator_label, threshold)
    # Column names use the raw Yamaha-dual prefix; K-Line columns
    # carry the canonical engine data on this fixture.
    ("A_KL_RPM", ">",  3000.0),
    ("A_KL_RPM", ">",  3500.0),
    ("A_KL_SPEED", ">", 5.0),
    ("A_KL_SPEED", ">", 10.0),
    ("A_KL_COOLANT_TEMP", ">",  75.0),
    ("A_KL_COOLANT_TEMP", ">",  80.0),
]
"""Common thresholds the goldens are likely to reference.

Goldens can ask "when does the vehicle first exceed 10 km/h?" and
the script's output makes it easy to copy the right time window.
Adjust as the fixture or golden set evolves.
"""


# ── Stats computation ────────────────────────────────────────────


def _extract_numeric_column(
    rows: List[Dict[str, str]], column: str,
) -> List[float]:
    """Pull a numeric column from rows, dropping non-numeric cells."""
    out: List[float] = []
    for row in rows:
        raw = row.get(column)
        if raw is None:
            continue
        val = try_float(raw)
        if val is None:
            continue
        out.append(val)
    return out


def _percentile(values: List[float], pct: float) -> float:
    """Linear-interpolation percentile (mirrors numpy's default).

    Avoids a numpy dep — the script should run with stdlib only.
    """
    if not values:
        return math.nan
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return (
        sorted_vals[lo] * (hi - k)
        + sorted_vals[hi] * (k - lo)
    )


def _format_stats_table(log: OBDLogData) -> str:
    """Render one stats row per numeric column."""
    if not log.rows:
        return "(no data rows in fixture)\n"

    lines = [
        f"{'signal':<24} {'n':>5} {'min':>10} {'max':>10} "
        f"{'mean':>10} {'p50':>10} {'p95':>10} {'std':>10}",
        "-" * 100,
    ]
    for col in log.columns:
        if col == "Timestamp":
            continue
        values = _extract_numeric_column(log.rows, col)
        if not values:
            continue
        mean = statistics.fmean(values)
        std = statistics.pstdev(values) if len(values) > 1 else 0.0
        lines.append(
            f"{col:<24} {len(values):>5d} "
            f"{min(values):>10.3f} {max(values):>10.3f} "
            f"{mean:>10.3f} {_percentile(values, 50):>10.3f} "
            f"{_percentile(values, 95):>10.3f} {std:>10.3f}",
        )
    return "\n".join(lines) + "\n"


# ── Event-window detection ───────────────────────────────────────


def _detect_event_windows(
    log: OBDLogData,
    signal: str,
    op: str,
    threshold: float,
) -> List[Tuple[str, str]]:
    """Return contiguous-true windows for ``signal op threshold``.

    Each window is ``(start_timestamp, end_timestamp)`` based on
    the ``Timestamp`` column.  Operator currently only ``">"``
    (the only one the threshold list uses).
    """
    windows: List[Tuple[str, str]] = []
    in_window = False
    win_start: Optional[str] = None
    win_end: Optional[str] = None

    for row in log.rows:
        ts = row.get("Timestamp")
        val = try_float(row.get(signal, ""))
        is_true = (
            val is not None
            and op == ">"
            and val > threshold
        )
        if is_true:
            if not in_window:
                in_window = True
                win_start = ts
            win_end = ts
        else:
            if in_window and win_start and win_end:
                windows.append((win_start, win_end))
            in_window = False
            win_start = None
            win_end = None

    # Trailing window — fixture ends while condition still true.
    if in_window and win_start and win_end:
        windows.append((win_start, win_end))

    return windows


def _format_event_windows(log: OBDLogData) -> str:
    """Render event windows for the threshold list."""
    lines = ["Event windows (signal op threshold → contiguous ranges)"]
    for signal, op, threshold in _EVENT_THRESHOLDS:
        windows = _detect_event_windows(log, signal, op, threshold)
        header = f"  {signal} {op} {threshold}:"
        if not windows:
            lines.append(f"{header} (no windows)")
            continue
        lines.append(header)
        for start, end in windows:
            lines.append(f"    [{start}, {end}]")
    return "\n".join(lines) + "\n"


# ── DTC list ─────────────────────────────────────────────────────


def _format_metadata_dtcs(log: OBDLogData) -> str:
    """Render the Yamaha-format metadata DTC list."""
    if not log.metadata_dtcs:
        return "(no metadata DTCs)\n"
    lines = ["Metadata DTCs"]
    for d in log.metadata_dtcs:
        lines.append(f"  {d.code:<28} {d.status:<8} {d.ecu}")
    return "\n".join(lines) + "\n"


# ── Structured-data builders (HARNESS-21 [2a/4]) ─────────────────


def _signal_stats_record(
    values: List[float],
    rows: List[Dict[str, str]],
    column: str,
) -> Dict[str, Any]:
    """Compute the per-signal stats payload for one column.

    Includes a ``max_at`` timestamp so event_finding-style goldens
    can cite the exact moment the maximum was observed without
    re-scanning the fixture.
    """
    max_val = max(values)
    min_val = min(values)
    max_at = None
    min_at = None
    for row in rows:
        v = try_float(row.get(column, ""))
        if v is None:
            continue
        if max_at is None and v == max_val:
            max_at = row.get("Timestamp")
        if min_at is None and v == min_val:
            min_at = row.get("Timestamp")
        if max_at is not None and min_at is not None:
            break
    return {
        "samples_valid": len(values),
        "min": min_val,
        "min_at": min_at,
        "max": max_val,
        "max_at": max_at,
        "mean": statistics.fmean(values),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "std": (
            statistics.pstdev(values) if len(values) > 1 else 0.0
        ),
    }


def compute_reference_data(log: OBDLogData) -> Dict[str, Any]:
    """Build the structured reference payload for the fixture.

    Returned dict is the source of truth committed as
    ``tests/harness/evals/golden/v1/yamaha_road_test_reference.json``
    (HARNESS-21 commit 5).  Consumed by:

    - Golden authors when picking expected numerical values
      (commit 6 references this file directly).
    - PR [2b/4]'s `/goldens/obd` detail page sparkline rendering.

    Shape (versioned via ``schema_version`` so future expansions
    don't break consumers):

    .. code-block:: text

        {
          "schema_version": 1,
          "fixture": {
            "name": "yamaha_dual_road_test_20260508.csv",
            "sha256": "<hex>",
            "rows": 257,
            "columns": 26,
            "channels_present": ["engine"],
            "format": "yamaha_dual"
          },
          "signal_stats": {
            "<column_name>": {
              "samples_valid": int,
              "min": float, "min_at": "ISO8601",
              "max": float, "max_at": "ISO8601",
              "mean": float, "p50": float, "p95": float,
              "std": float
            }, ...
          },
          "event_windows": [
            {"signal": "...", "op": ">", "threshold": 3000.0,
             "ranges": [[start_iso, end_iso], ...]}, ...
          ],
          "metadata_dtcs": [
            {"code": "...", "status": "stored|pending",
             "ecu": "..."}, ...
          ]
        }
    """
    fixture_bytes = _FIXTURE_PATH.read_bytes()
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()

    signal_stats: Dict[str, Dict[str, Any]] = {}
    for col in log.columns:
        if col == "Timestamp":
            continue
        values = _extract_numeric_column(log.rows, col)
        if not values:
            continue
        signal_stats[col] = _signal_stats_record(
            values, log.rows, col,
        )

    event_windows: List[Dict[str, Any]] = []
    for signal, op, threshold in _EVENT_THRESHOLDS:
        windows = _detect_event_windows(log, signal, op, threshold)
        event_windows.append({
            "signal": signal,
            "op": op,
            "threshold": threshold,
            "ranges": [list(w) for w in windows],
        })

    return {
        "schema_version": 1,
        "fixture": {
            "name": _FIXTURE_PATH.name,
            "sha256": fixture_sha,
            "rows": len(log.rows),
            "columns": len(log.columns),
            "channels_present": sorted(log.channels_present),
            "format": str(log.format),
        },
        "signal_stats": signal_stats,
        "event_windows": event_windows,
        "metadata_dtcs": [
            {"code": d.code, "status": d.status, "ecu": d.ecu}
            for d in log.metadata_dtcs
        ],
    }


# ── Entry point ──────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """Print fixture stats to stdout, or emit JSON when ``--json``.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Exit code: 0 on success, 2 if the fixture is missing.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Compute deterministic reference stats for the "
            "Yamaha road-test fixture."
        ),
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Emit structured JSON to PATH (use '-' for stdout) "
            "instead of the human-readable text table.  Use "
            "tests/harness/evals/golden/v1/"
            "yamaha_road_test_reference.json to refresh the "
            "sidecar artifact."
        ),
    )
    args = parser.parse_args(argv)

    if not _FIXTURE_PATH.is_file():
        print(
            f"FIXTURE NOT FOUND: {_FIXTURE_PATH}",
            file=sys.stderr,
        )
        return 2

    log = load_obd_data(_FIXTURE_PATH)

    if args.json is not None:
        payload = compute_reference_data(log)
        text = json.dumps(payload, indent=2, default=str)
        if args.json == "-":
            print(text)
        else:
            Path(args.json).write_text(text, encoding="utf-8")
            print(
                f"Wrote {len(text):,} chars to {args.json}",
                file=sys.stderr,
            )
        return 0

    print(f"Fixture: {_FIXTURE_PATH.name}")
    print(f"Format:  {log.format}")
    print(f"Rows:    {len(log.rows)}")
    print(f"Columns: {len(log.columns)}")
    print(f"Channels: {sorted(log.channels_present)}")
    print()
    print("Per-signal statistics (whole-trip aggregates)")
    print(_format_stats_table(log))
    print()
    print(_format_event_windows(log))
    print()
    print(_format_metadata_dtcs(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())

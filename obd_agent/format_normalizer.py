"""Auto-detect and normalize OBD log file formats.

Preprocessing layer that converts various OBD log formats into the
internal TSV format expected by :func:`log_parser.parse_log_file`.

Supported input formats:

* **Native TSV** — the platform's own tab-separated format (pass-through).
* **CSVLog (OBDWIZ)** — comma-separated, Chinese column headers,
  imperial units, localised timestamps with Chinese AM/PM markers.
* **obd_maxlog (Python script)** — comma-separated, ``#``-prefixed
  metadata, English column headers with unit suffixes like ``RPM (rpm)``.
* **Generic CSV** — comma-separated with a recognisable ``Timestamp``
  column and bare PID names (delimiter conversion only).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import (
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
)

# ── Unit conversion helpers ──────────────────────────────────────────

def _fahrenheit_to_celsius(f: float) -> float:
    return round((f - 32) * 5 / 9, 2)


def _mph_to_kmh(mph: float) -> float:
    return round(mph * 1.60934, 2)


def _inhg_to_kpa(inhg: float) -> float:
    return round(inhg * 3.38639, 2)


def _psi_to_kpa(psi: float) -> float:
    return round(psi * 6.89476, 2)


def _lbmin_to_gs(lbmin: float) -> float:
    return round(lbmin * 7.55987, 2)


def _miles_to_km(mi: float) -> float:
    return round(mi * 1.60934, 2)


# ── OBDWIZ CSVLog column mapping ─────────────────────────────────────
# Chinese header → (standard PID name, optional unit converter)

_CSVLOG_COLUMN_MAP: Dict[str, Tuple[str, Optional[Callable[[float], float]]]] = {
    "Time": ("Timestamp", None),
    "车速 (MPH)": ("SPEED", _mph_to_kmh),
    "EGR 误差 (%)": ("EGR_ERROR", None),
    "自发动机启动以来的时间 (sec)": ("RUN_TIME", None),
    "自 DTC 清除以来的预热次数": ("WARMUPS_SINCE_DTC_CLEAR", None),
    "质量空气流量 (lb/min)": ("MAF", _lbmin_to_gs),
    "油门踏板位置 E (%)": ("ACCELERATOR_POS_E", None),
    "油门踏板位置 D (%)": ("ACCELERATOR_POS_D", None),
    "氧传感器的位置": ("O2_SENSORS", None),
    "相对节气门位置 (%)": ("RELATIVE_THROTTLE_POS", None),
    "节气门位置 B (%)": ("THROTTLE_POS_B", None),
    "当距离行程 (以MIL亮为准) (miles)": (
        "DISTANCE_W_MIL", _miles_to_km,
    ),
    "自 DTC 清除以来的距离 (miles)": (
        "DISTANCE_SINCE_DTC_CLEAR", _miles_to_km,
    ),
    "发动机转速 (RPM)": ("RPM", None),
    "发动机冷却液温度 (°F)": ("COOLANT_TEMP", _fahrenheit_to_celsius),
    "计算负荷值 (%)": ("ENGINE_LOAD", None),
    "正时提前 (°)": ("TIMING_ADVANCE", None),
    "进气温度 (°F)": ("INTAKE_TEMP", _fahrenheit_to_celsius),
    "进气歧管绝对压力 (inHg)": ("INTAKE_PRESSURE", _inhg_to_kpa),
    "大气压力 (inHg)": ("BAROMETRIC_PRESSURE", _inhg_to_kpa),
    "节气门位置 (%)": ("THROTTLE_POS", None),
    "控制模块电压 (V)": ("CONTROL_MODULE_VOLTAGE", None),
    "绝对负荷值 (%)": ("ABSOLUTE_LOAD", None),
    "指令等效比 ()": ("COMMANDED_EQUIV_RATIO", None),
    "短时燃油修正 - 缸列 1 (%)": ("SHORT_FUEL_TRIM_1", None),
    "长时燃油修正 - 缸列 1 (%)": ("LONG_FUEL_TRIM_1", None),
    "指令蒸发清洗 (%)": ("EVAPORATIVE_PURGE", None),
    "指令 EGR (%)": ("COMMANDED_EGR", None),
    "催化转化器温度: 缸列 1, 传感器 1 (°F)": (
        "CATALYST_TEMP_B1S1", _fahrenheit_to_celsius,
    ),
    "O2 传感器 2 电压 (缸列 1) (V)": ("O2_B1S2", None),
    "O2 传感器 1 电流 (缸列 1) (mA)": ("O2_S1_WR_CURRENT", None),
    "燃油系统状态": ("FUEL_STATUS", None),
    "燃油类型": ("FUEL_TYPE", None),
    "燃油轨压力（直喷） (psi)": (
        "FUEL_RAIL_PRESSURE_DIRECT", _psi_to_kpa,
    ),
    "OBD 标准": ("OBD_COMPLIANCE", None),
    "节气门执行器 (%)": ("THROTTLE_ACTUATOR", None),
    "ELM 电压 (V)": ("ELM_VOLTAGE", None),
}

# Columns from maxlog to filter out (non-standard diagnostic columns).
_MAXLOG_SKIP_PREFIXES = ("DTC_", "MONITOR_", "M22_")

# Regex for Chinese AM/PM markers in OBDWIZ timestamps.
_CN_AMPM_RE = re.compile(r"\s*(上午|下午)\s*$")

# Regex to detect Chinese characters.
_CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

# Regex for unit suffix in parentheses: e.g. " (rpm)", " (%)", " (°C)"
_UNIT_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


# ── Timestamp normalisation ──────────────────────────────────────────

def _normalise_csvlog_timestamp(raw: str) -> str:
    """Convert OBDWIZ timestamp to ``YYYY-MM-DD HH:MM:SS``.

    Input format: ``MM/DD/YYYY HH:MM:SS.SSSS 上午/下午``
    The 上午/下午 markers correspond to AM/PM in Chinese locale.

    Args:
        raw: Raw timestamp string from OBDWIZ CSV.

    Returns:
        Normalised timestamp string, or original if parsing fails.
    """
    raw = raw.strip()
    is_pm = "下午" in raw
    raw = _CN_AMPM_RE.sub("", raw).strip()

    # Strip sub-second precision.
    dot_idx = raw.rfind(".")
    if dot_idx != -1:
        raw = raw[:dot_idx]

    try:
        dt = datetime.strptime(raw, "%m/%d/%Y %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return raw

    # Handle 12-hour clock with Chinese AM/PM.
    if is_pm and dt.hour < 12:
        dt = dt.replace(hour=dt.hour + 12)
    elif not is_pm and dt.hour == 12:
        dt = dt.replace(hour=0)

    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _normalise_timestamp_generic(raw: str) -> str:
    """Strip sub-second precision and ISO ``T`` separator.

    Args:
        raw: Raw timestamp string.

    Returns:
        Normalised ``YYYY-MM-DD HH:MM:SS`` string.
    """
    raw = raw.strip().replace("T", " ")
    dot_idx = raw.rfind(".")
    if dot_idx != -1:
        raw = raw[:dot_idx]
    return raw


# ── Format detection ─────────────────────────────────────────────────

FormatType = Literal[
    "native_tsv", "csvlog_obdwiz", "obd_maxlog", "generic_csv",
]


def _detect_format(lines: List[str]) -> FormatType:
    """Determine the OBD log format from file content.

    Examines the first ~60 lines to identify the format.

    Args:
        lines: All lines of the file (with newlines).

    Returns:
        One of ``native_tsv``, ``csvlog_obdwiz``, ``obd_maxlog``,
        or ``generic_csv``.
    """
    has_metadata = False
    header_line: Optional[str] = None

    for line in lines[:60]:
        stripped = line.rstrip("\n\r")

        # Native TSV: header row starts with "Timestamp\t".
        if stripped.startswith("Timestamp\t"):
            return "native_tsv"

        if stripped.startswith("#"):
            has_metadata = True
            continue

        if not stripped:
            continue

        # First non-comment, non-empty line is the candidate header.
        if header_line is None:
            header_line = stripped
            break

    if header_line is None:
        return "native_tsv"  # fallback

    # Check for Chinese characters → OBDWIZ CSVLog.
    if _CHINESE_CHAR_RE.search(header_line):
        return "csvlog_obdwiz"

    # Check for unit-suffixed headers → obd_maxlog.
    # Look for "WORD (unit)" patterns anywhere in the header, not just
    # at the end — the last column may lack a suffix.
    has_unit_suffix = bool(re.search(r"\w\s+\([^)]+\)", header_line))

    if has_metadata and has_unit_suffix:
        return "obd_maxlog"

    # Unit-suffixed CSV headers without # metadata (still maxlog).
    if has_unit_suffix and "," in header_line:
        return "obd_maxlog"

    # Any comma-separated file with "Timestamp" in header.
    if "," in header_line and "Timestamp" in header_line:
        return "generic_csv"

    return "native_tsv"


# ── Normalisation functions ──────────────────────────────────────────

def _try_convert(
    raw: str,
    converter: Optional[Callable[[float], float]],
) -> str:
    """Apply a unit converter to a raw string value.

    Args:
        raw: Raw numeric string.
        converter: Optional conversion function.

    Returns:
        Converted value as string, or original if conversion fails
        or no converter is specified.
    """
    if converter is None:
        return raw
    raw = raw.strip()
    if not raw or raw == "N/A":
        return raw
    try:
        return str(converter(float(raw)))
    except (ValueError, TypeError):
        return raw


def _normalize_csvlog(
    lines: List[str],
    out_path: Path,
) -> Path:
    """Convert OBDWIZ CSVLog format to internal TSV.

    Args:
        lines: Raw file lines.
        out_path: Destination path for the normalised TSV.

    Returns:
        Path to the written TSV file.
    """
    # Parse CSV content.
    text = "".join(lines)
    reader = csv.reader(io.StringIO(text))

    rows_iter = iter(reader)
    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        raise ValueError("OBDWIZ CSVLog file contains no header row.")
    raw_headers = [h.strip() for h in raw_headers]

    # Build column mapping: index → (pid_name, converter).
    col_map: List[Optional[Tuple[str, Optional[Callable]]]] = []
    for h in raw_headers:
        entry = _CSVLOG_COLUMN_MAP.get(h)
        col_map.append(entry)

    # Determine which mapped columns exist.
    pid_names: List[str] = []
    col_indices: List[int] = []
    converters: List[Optional[Callable[[float], float]]] = []
    for idx, entry in enumerate(col_map):
        if entry is not None:
            pid_names.append(entry[0])
            col_indices.append(idx)
            converters.append(entry[1])

    if "Timestamp" not in pid_names:
        raise ValueError(
            "OBDWIZ CSVLog has no recognisable Timestamp column."
        )

    ts_pos = pid_names.index("Timestamp")

    # Process data rows.
    output_rows: List[List[str]] = []
    prev_values: Optional[List[str]] = None

    for raw_row in rows_iter:
        if not raw_row or all(c.strip() == "" for c in raw_row):
            continue

        values: List[str] = []
        for i, ci in enumerate(col_indices):
            raw_val = raw_row[ci].strip() if ci < len(raw_row) else ""
            if i == ts_pos:
                raw_val = _normalise_csvlog_timestamp(raw_val)
            else:
                raw_val = _try_convert(raw_val, converters[i])
            values.append(raw_val)

        # Dedup: skip if non-timestamp values identical to previous.
        data_values = [
            v for j, v in enumerate(values) if j != ts_pos
        ]
        if prev_values is not None and data_values == prev_values:
            continue
        prev_values = data_values
        output_rows.append(values)

    # Derive metadata from data.
    ts_values = [r[ts_pos] for r in output_rows if r[ts_pos]]
    first_ts = ts_values[0] if ts_values else "unknown"
    last_ts = ts_values[-1] if ts_values else "unknown"

    # Write TSV.
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# Source: OBDWIZ CSVLog (auto-converted)\n")
        fh.write(f"# Records: {len(output_rows)} (de-duplicated)\n")
        fh.write(
            f"# Time Range: {first_ts} ~ {last_ts}\n"
        )
        fh.write(
            "# " + "=" * 72 + "\n"
        )
        # 4-line header block for native parser compatibility.
        fh.write("OBD Data Log (converted from OBDWIZ CSVLog)\n")
        fh.write(f"Start Time: {first_ts}\n")
        fh.write("Log Interval: variable\n")
        fh.write("-" * 80 + "\n")
        fh.write("\t".join(pid_names) + "\n")
        fh.write("-" * 80 + "\n")
        for row in output_rows:
            fh.write("\t".join(row) + "\n")

    return out_path


def _normalize_maxlog(
    lines: List[str],
    out_path: Path,
) -> Path:
    """Convert obd_maxlog CSV format to internal TSV.

    Preserves ``#``-prefixed metadata as comment lines, strips unit
    suffixes from column headers, filters non-standard columns, and
    truncates sub-second timestamp precision.

    Args:
        lines: Raw file lines.
        out_path: Destination path for the normalised TSV.

    Returns:
        Path to the written TSV file.
    """
    metadata_lines: List[str] = []
    data_lines: List[str] = []
    header_found = False

    for line in lines:
        stripped = line.rstrip("\n\r")
        if stripped.startswith("#"):
            metadata_lines.append(stripped)
        elif not header_found and stripped.startswith("Timestamp"):
            data_lines.append(stripped)
            header_found = True
        elif header_found:
            data_lines.append(stripped)
        elif not stripped:
            continue
        elif not header_found and "," in stripped:
            # Could be a wrapped header line; accumulate.
            if data_lines:
                data_lines[-1] += stripped
            else:
                data_lines.append(stripped)

    if not data_lines:
        raise ValueError(
            "obd_maxlog file has no recognisable header row."
        )

    # Parse CSV data section.
    text = "\n".join(data_lines)
    reader = csv.reader(io.StringIO(text))
    rows_iter = iter(reader)
    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        raise ValueError("obd_maxlog file contains no header row.")
    raw_headers = [h.strip() for h in raw_headers if h.strip()]

    # Strip unit suffixes and build column map.
    clean_headers: List[str] = []
    keep_indices: List[int] = []
    for idx, h in enumerate(raw_headers):
        bare = _UNIT_SUFFIX_RE.sub("", h).strip()
        # Filter out non-standard columns.
        if any(bare.startswith(p) for p in _MAXLOG_SKIP_PREFIXES):
            continue
        clean_headers.append(bare)
        keep_indices.append(idx)

    if "Timestamp" not in clean_headers:
        raise ValueError(
            "obd_maxlog has no Timestamp column after header cleanup."
        )

    ts_col = clean_headers.index("Timestamp")

    # Process data rows.
    output_rows: List[List[str]] = []
    for raw_row in rows_iter:
        if not raw_row or all(c.strip() == "" for c in raw_row):
            continue
        values: List[str] = []
        for i, ci in enumerate(keep_indices):
            raw_val = raw_row[ci].strip() if ci < len(raw_row) else ""
            if i == ts_col:
                raw_val = _normalise_timestamp_generic(raw_val)
            values.append(raw_val)
        output_rows.append(values)

    # Derive time range.
    ts_values = [r[ts_col] for r in output_rows if r[ts_col]]
    first_ts = ts_values[0] if ts_values else "unknown"
    last_ts = ts_values[-1] if ts_values else "unknown"

    # Write TSV.
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        for ml in metadata_lines:
            fh.write(ml + "\n")
        # 4-line header block for native parser compatibility.
        fh.write("OBD Data Log (converted from obd_maxlog)\n")
        fh.write(f"Start Time: {first_ts}\n")
        fh.write("Log Interval: variable\n")
        fh.write("-" * 80 + "\n")
        fh.write("\t".join(clean_headers) + "\n")
        fh.write("-" * 80 + "\n")
        for row in output_rows:
            fh.write("\t".join(row) + "\n")

    return out_path


def _normalize_generic_csv(
    lines: List[str],
    out_path: Path,
) -> Path:
    """Convert a generic CSV with bare PID headers to internal TSV.

    Handles delimiter conversion and timestamp normalisation only.

    Args:
        lines: Raw file lines.
        out_path: Destination path for the normalised TSV.

    Returns:
        Path to the written TSV file.
    """
    comment_lines: List[str] = []
    data_text_lines: List[str] = []

    for line in lines:
        stripped = line.rstrip("\n\r")
        if stripped.startswith("#"):
            comment_lines.append(stripped)
        elif stripped:
            data_text_lines.append(stripped)

    text = "\n".join(data_text_lines)
    reader = csv.reader(io.StringIO(text))
    rows_iter = iter(reader)
    try:
        headers = [h.strip() for h in next(rows_iter) if h.strip()]
    except StopIteration:
        raise ValueError("CSV file contains no header row.")

    ts_col = headers.index("Timestamp") if "Timestamp" in headers else -1

    output_rows: List[List[str]] = []
    for raw_row in rows_iter:
        if not raw_row or all(c.strip() == "" for c in raw_row):
            continue
        values: List[str] = []
        for i, val in enumerate(raw_row[:len(headers)]):
            val = val.strip()
            if i == ts_col:
                val = _normalise_timestamp_generic(val)
            values.append(val)
        output_rows.append(values)

    ts_values = (
        [r[ts_col] for r in output_rows if r[ts_col]]
        if ts_col >= 0
        else []
    )
    first_ts = ts_values[0] if ts_values else "unknown"

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        for cl in comment_lines:
            fh.write(cl + "\n")
        fh.write("OBD Data Log (converted from CSV)\n")
        fh.write(f"Start Time: {first_ts}\n")
        fh.write("Log Interval: variable\n")
        fh.write("-" * 80 + "\n")
        fh.write("\t".join(headers) + "\n")
        fh.write("-" * 80 + "\n")
        for row in output_rows:
            fh.write("\t".join(row) + "\n")

    return out_path


# ── Public API ───────────────────────────────────────────────────────

def normalize_obd_file(path: str | Path) -> Path:
    """Auto-detect OBD log format and convert to internal TSV.

    If the file is already in native TSV format, returns the original
    path unchanged.  Otherwise writes a ``.tsv`` file alongside the
    original and returns that path.

    Args:
        path: Path to the uploaded OBD log file.

    Returns:
        Path to a file in internal TSV format (may be the same as
        *path* if no conversion was needed).

    Raises:
        ValueError: If the file format cannot be detected or converted.
    """
    path = Path(path)
    with open(path, encoding="utf-8-sig") as fh:
        lines = fh.readlines()

    fmt = _detect_format(lines)

    if fmt == "native_tsv":
        return path

    out_path = path.with_suffix(".normalized.tsv")

    if fmt == "csvlog_obdwiz":
        return _normalize_csvlog(lines, out_path)
    elif fmt == "obd_maxlog":
        return _normalize_maxlog(lines, out_path)
    elif fmt == "generic_csv":
        return _normalize_generic_csv(lines, out_path)

    return path  # unreachable, but satisfies type checker

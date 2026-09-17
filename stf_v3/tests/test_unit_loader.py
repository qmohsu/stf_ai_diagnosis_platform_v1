"""PROD-08 T-1 / T-2: the three-format log reader and the ownership check.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest

from stf_v3.ingest.loader import (
    LogOwnershipError,
    detect_format,
    load_for_vehicle,
    load_obd_data,
    parse_dtc_list,
    parse_obd_text,
    parse_timestamp,
    try_float,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TSV = FIXTURES / "jetson_tsv_ok.tsv"
YAMAHA = FIXTURES / "yamaha_dual.csv"
MAXLOG = FIXTURES / "obd_maxlog_hiace.csv"
MAXLOG_NO_VIN = FIXTURES / "obd_maxlog_no_vin.csv"
MAXLOG_SAMPLE = FIXTURES / "obd_maxlog_sample.csv"


def test_three_formats_share_one_shape() -> None:
    """TSV, Yamaha CSV and OBD Maximum CSV all yield rows + signal columns +
    a Timestamp per row, so the tools above never see the format."""
    for path, fmt in ((TSV, "standard_tsv"), (YAMAHA, "yamaha_dual"), (MAXLOG, "obd_maxlog")):
        data = load_obd_data(path)
        assert data.format == fmt
        assert data.rows and data.columns
        assert "Timestamp" not in data.columns
        assert all("Timestamp" in r for r in data.rows)
        assert all(parse_timestamp(r["Timestamp"]) is not None for r in data.rows)


def test_maxlog_columns_follow_v2_conversion_rules() -> None:
    """FM-37: unit suffixes are stripped and DTC_/MONITOR_ status columns
    dropped, exactly as V2's upload-time conversion did — so signal names
    match the V2 goldens."""
    data = load_obd_data(MAXLOG_SAMPLE)
    assert data.format == "obd_maxlog"
    assert not any("(" in c for c in data.columns)
    assert not any(c.startswith(("DTC_", "MONITOR_", "M22_")) for c in data.columns)
    assert data.columns[:6] == ["RPM", "SPEED", "THROTTLE_POS", "ENGINE_LOAD", "COOLANT_TEMP", "INTAKE_TEMP"]
    # the same rule applied to the raw header must give the same list
    header = next(l for l in MAXLOG_SAMPLE.read_text(encoding="utf-8").splitlines() if l.startswith("Timestamp"))
    expected = [re.sub(r"\s*\([^)]*\)\s*$", "", h).strip() for h in header.split(",")]
    expected = [h for h in expected if h != "Timestamp" and not h.startswith(("DTC_", "MONITOR_", "M22_"))]
    assert data.columns == expected
    assert try_float(data.rows[0]["RPM"]) == 745.0


def test_maxlog_metadata_dtcs_and_missing_sections_are_limitations() -> None:
    """FM-9: metadata DTC lines are read; a missing Mode 06 / Mode 09 section
    or trailer is reported as a limitation, never raised."""
    hiace = load_obd_data(MAXLOG)
    assert [(d.code, d.status) for d in hiace.metadata_dtcs] == [("430100AF", "stored"), ("4300", "stored")]
    assert any("mode06" in l for l in hiace.limitations)
    no_vin = load_obd_data(MAXLOG_NO_VIN)
    assert [(d.code, d.status) for d in no_vin.metadata_dtcs] == [("4300", "stored")]
    assert any("trailer" in l for l in no_vin.limitations)
    text = MAXLOG.read_text(encoding="utf-8")
    stripped = "\n".join(l for l in text.splitlines() if "Mode 09" not in l and not l.startswith("#   VIN"))
    data = parse_obd_text(stripped.lstrip("﻿"))
    assert data.rows and any("mode09" in l for l in data.limitations)


def test_yamaha_metadata_dtcs_and_proprietary_columns_survive() -> None:
    """FM-38: the Yamaha hex DTCs from the ``#`` block and the ``A_YAM_*``
    columns are all there (HARNESS-19 locked decision)."""
    data = load_obd_data(YAMAHA)
    codes = [(d.code, d.status, d.ecu) for d in data.metadata_dtcs]
    assert ("87F11043000000000000CB", "stored", "K-Line") in codes
    assert ("87F11047000000000000CF", "pending", "K-Line") in codes
    assert any(c.startswith("A_YAM_") for c in data.columns)
    assert "engine" in data.channels_present


def test_tsv_rows_and_dtc_cell_parsing() -> None:
    """The TSV path keeps every column; GET_DTC cells parse in all shapes."""
    data = load_obd_data(TSV)
    assert data.format == "standard_tsv" and len(data.rows) == 20
    assert parse_dtc_list("[]") == [] and parse_dtc_list("N/A") == []
    assert parse_dtc_list("[('P0301', 'Cylinder 1 Misfire Detected')]") == [("P0301", "Cylinder 1 Misfire Detected")]
    assert parse_dtc_list("garbage P0117 more") == [("P0117", "")]


def test_detect_format_and_unknown_input() -> None:
    """Detection is by banner / markers; junk raises a ValueError, never a crash."""
    assert detect_format("# OBD Maximum Data Log\nTimestamp,RPM (rpm)\n") == "obd_maxlog"
    assert detect_format("# Yamaha Dual\nTimestamp,A_KL_RPM\n") == "yamaha_dual"
    assert detect_format("OBD Data Log\nTimestamp\tRPM\n") == "standard_tsv"
    assert detect_format("hello") == "unknown"
    with pytest.raises(ValueError):
        parse_obd_text("hello world\nno data here\n")


def test_load_for_vehicle_refuses_foreign_log(tmp_path: pathlib.Path) -> None:
    """T-2 / FM-4: a log row belonging to another vehicle is refused before
    the file is opened; the matching vehicle reads normally."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(LogOwnershipError):
        load_for_vehicle(FIXTURES, "obd_maxlog_hiace.csv", log_vehicle_id=b, vehicle_id=a)
    data = load_for_vehicle(FIXTURES, "obd_maxlog_hiace.csv", log_vehicle_id=a, vehicle_id=a)
    assert data.rows
    with pytest.raises(LogOwnershipError):
        load_for_vehicle(tmp_path, "../../etc/passwd", log_vehicle_id=a, vehicle_id=a)

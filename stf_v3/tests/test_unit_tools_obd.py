"""PROD-08 T-3: the six OBD tools across the three log formats (offline).

Every tool returns a string; ``read_window`` never exceeds 500 rows.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pytest

from stf_v3.diagnosis.tools import obd_dtcs, obd_signals
from tests.agent_helpers import make_deps, stub_ctx

FORMATS = ["tsv", "yamaha", "maxlog"]


@pytest.mark.parametrize("log", FORMATS)
async def test_list_signals_inventory_and_filters(log: str) -> None:
    """Inventory names the format, time range and every signal; glob and
    subsystem filters narrow it; output is text."""
    ctx = stub_ctx(make_deps(log))
    out = await obd_signals.list_signals(ctx)
    assert isinstance(out, str) and "Time range:" in out and "Signals (" in out
    filtered = await obd_signals.list_signals(ctx, pattern="*TEMP*")
    assert "match pattern='*TEMP*'" in filtered
    none = await obd_signals.list_signals(ctx, pattern="ZZZ*")
    assert "(no signals match the filter)" in none


@pytest.mark.parametrize("log", FORMATS)
async def test_read_window_downsamples_and_validates(log: str) -> None:
    """A window is a tab table capped at max_rows (hard cap 500); unknown
    signals get suggestions; an inverted window is refused."""
    ctx = stub_ctx(make_deps(log))
    data = ctx.deps.load_log()
    first = data.columns[0]
    out = await obd_signals.read_window(ctx, signals=[first], max_rows=3)
    assert isinstance(out, str) and "Signal window" in out
    body = [l for l in out.splitlines() if l[:4].isdigit()]
    assert 1 <= len(body) <= 3
    big = await obd_signals.read_window(ctx, signals=[first], max_rows=9999)
    assert len([l for l in big.splitlines() if l[:4].isdigit()]) <= 500
    unknown = await obd_signals.read_window(ctx, signals=["NOPE_SIGNAL"])
    assert "not in this log" in unknown and "list_signals" in unknown
    bad = await obd_signals.read_window(ctx, signals=[first], start_time="2030-01-01T00:00:00",
                                        end_time="2020-01-01T00:00:00")
    assert "start_time must precede end_time" in bad


@pytest.mark.parametrize("log", FORMATS)
async def test_get_signal_stats_groups(log: str) -> None:
    """Basic + percentiles by default; trend and extrema on request; the
    output is a text block per signal."""
    ctx = stub_ctx(make_deps(log))
    first = ctx.deps.load_log().columns[0]
    out = await obd_signals.get_signal_stats(ctx, signals=[first])
    assert "Signal statistics" in out and "p50:" in out and "mean:" in out
    trend = await obd_signals.get_signal_stats(ctx, signals=[first], include=["trend", "extrema"])
    assert "max_at:" in trend
    unknown = await obd_signals.get_signal_stats(ctx, signals=["NOPE"])
    assert "not in this log" in unknown


@pytest.mark.parametrize("log", FORMATS)
async def test_find_events_predicates(log: str) -> None:
    """Threshold predicates need a threshold; matches list spans with peaks;
    'missing' works without one."""
    ctx = stub_ctx(make_deps(log))
    first = ctx.deps.load_log().columns[0]
    missing = await obd_signals.find_events(ctx, signal=first, predicate="above_threshold")
    assert "requires the `threshold` parameter" in missing
    out = await obd_signals.find_events(ctx, signal=first, predicate="above_threshold", threshold=-1e9,
                                        min_duration_seconds=0.0)
    assert "Events —" in out and ("peak=" in out or "No events matched" in out)
    gaps = await obd_signals.find_events(ctx, signal=first, predicate="missing", min_duration_seconds=0.0)
    assert "missing (N/A)" in gaps


async def test_list_dtcs_per_format() -> None:
    """Yamaha hex codes, maxlog raw frames (decoded to P00AF) and TSV GET_DTC
    columns all surface through the same tool."""
    yam = await obd_dtcs.list_dtcs(stub_ctx(make_deps("yamaha")))
    assert "Yamaha-proprietary raw hex codes" in yam and "87F11043000000000000CB" in yam
    maxlog = await obd_dtcs.list_dtcs(stub_ctx(make_deps("maxlog")))
    assert "P00AF" in maxlog and "decoded from raw frame 430100AF" in maxlog
    assert "Raw Mode 43 response frames" in maxlog
    tsv = await obd_dtcs.list_dtcs(stub_ctx(make_deps("tsv")))
    assert "DTCs in log" in tsv
    stored_only = await obd_dtcs.list_dtcs(stub_ctx(make_deps("yamaha")), status="stored")
    assert "PENDING" not in stored_only


async def test_lookup_dtc_never_fabricates() -> None:
    """Standard codes: subsystem + related signals, no invented description;
    Yamaha hex: honest no-decoder; frames decode; junk is called out."""
    ctx = stub_ctx(make_deps("maxlog"))
    std = await obd_dtcs.lookup_dtc(ctx, code="P0117")
    assert "standard OBD-II code" in std and "COOLANT_TEMP" in std and "Description: Coolant" not in std
    yam = await obd_dtcs.lookup_dtc(ctx, code="87F11043000000000000CB")
    assert "No decoder available" in yam
    frame = await obd_dtcs.lookup_dtc(ctx, code="430100AF")
    assert "P00AF" in frame
    junk = await obd_dtcs.lookup_dtc(ctx, code="hello")
    assert "not a recognised" in junk
    empty = await obd_dtcs.lookup_dtc(ctx, code="")
    assert "Validation error" in empty


def test_mode43_frame_decoding() -> None:
    """``4301 00AF`` → P00AF; ``4300`` carries nothing; letters follow the
    2-bit class."""
    assert obd_dtcs.decode_mode43_frame("430100AF") == ["P00AF"]
    assert obd_dtcs.decode_mode43_frame("4300") == []
    assert obd_dtcs.decode_mode43_frame("43020117C123") == ["P0117", "U0123"]
    assert obd_dtcs.classify_code("P0117") == "standard"
    assert obd_dtcs.classify_code("430100AF") == "mode43_frame"
    assert obd_dtcs.classify_code("87F11043000000000000CB") == "yamaha_hex"


async def test_every_tool_output_is_text_and_errors_are_text() -> None:
    """A reader failure inside a tool becomes an ``Error:`` string (the model
    can self-correct), never an exception."""
    deps = make_deps("maxlog")
    deps.log = deps.log.__class__(id=deps.log.id, vehicle_id=deps.vehicle.id, raw_path="missing.csv", format="maxlog")
    out = await obd_signals.list_signals(stub_ctx(deps))
    assert out.startswith("Error: tool 'list_signals' failed")
    assert deps.trace[-1].is_error

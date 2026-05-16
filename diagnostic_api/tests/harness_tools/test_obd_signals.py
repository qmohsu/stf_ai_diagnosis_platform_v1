"""Unit tests for the OBD signal tools (HARNESS-19).

Exercises ``list_signals``, ``read_window``, ``get_signal_stats``,
and ``find_events`` against the real Yamaha road-test fixture.

All tests mock ``load_for_session`` to short-circuit the DB lookup
so they run hermetically without a Postgres instance.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.harness_tools.obd_loader import OBDLogData, load_obd_data
from app.harness_tools.obd_signal_inventory import (
    build_inventory,
    classify_subsystem,
    filter_inventory,
    fuzzy_suggestions,
    resolve_signal_name,
    units_for,
)
from app.harness_tools.obd_signals import (
    find_events,
    get_signal_stats,
    list_signals,
    read_window,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_YAMAHA_FIXTURE = (
    _REPO_ROOT
    / "obd_agent"
    / "fixtures"
    / "yamaha_dual_road_test_20260508.csv"
)

FAKE_SESSION_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def yamaha_data() -> OBDLogData:
    return load_obd_data(_YAMAHA_FIXTURE)


@pytest.fixture(autouse=True)
def _mock_load(yamaha_data: OBDLogData):
    """Patch the DB-backed loader to return the fixture directly."""
    with patch(
        "app.harness_tools.obd_signals.load_for_session",
        return_value=yamaha_data,
    ):
        yield


# ── Inventory helpers ────────────────────────────────────────────


class TestSignalInventory:
    """Unit tests for the inventory helpers."""

    def test_classify_subsystem_engine_for_a_kl(self) -> None:
        assert classify_subsystem("A_KL_RPM") == "engine"
        assert classify_subsystem("A_YAM_INJ_MS") == "engine"

    def test_classify_subsystem_abs_for_b_prefix(self) -> None:
        assert classify_subsystem("B_ABS_PRESSURE") == "abs"

    def test_classify_subsystem_engine_for_standard_pid(self) -> None:
        """Standard OBD PIDs (no prefix) classify as engine."""
        assert classify_subsystem("RPM") == "engine"
        assert classify_subsystem("COOLANT_TEMP") == "engine"

    def test_units_for_kl_canonical(self) -> None:
        assert units_for("A_KL_RPM") == "rpm"
        assert units_for("A_KL_COOLANT_TEMP") == "°C"

    def test_units_for_yamaha_proprietary(self) -> None:
        """HARNESS-19 unit map covers known A_YAM_* fields."""
        assert units_for("A_YAM_INJ_MS") == "ms"
        assert units_for("A_YAM_BATT_V") == "V"

    def test_units_for_yamaha_raw_marked_raw(self) -> None:
        """Provisional A_YAM_*_RAW fields are tagged as raw bytes."""
        assert units_for("A_YAM_BATT_RAW") == "raw"

    def test_units_for_unknown_falls_back(self) -> None:
        assert units_for("NEVER_HEARD_OF") == "unknown"

    def test_resolve_signal_name_exact_match(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        assert resolve_signal_name("A_KL_RPM", inv) == "A_KL_RPM"

    def test_resolve_signal_name_case_insensitive(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        assert resolve_signal_name("a_kl_rpm", inv) == "A_KL_RPM"

    def test_resolve_signal_name_suffix_prefers_kl(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """RPM should resolve to A_KL_RPM (shorter) over A_YAM_RPM."""
        inv = build_inventory(yamaha_data)
        assert resolve_signal_name("RPM", inv) == "A_KL_RPM"

    def test_resolve_signal_name_unknown_returns_none(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        assert resolve_signal_name("EGT_BANK_4", inv) is None

    def test_filter_inventory_by_pattern(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        out = filter_inventory(inv, "*temp*", "all")
        names = [d.name for d in out]
        assert "A_KL_COOLANT_TEMP" in names
        assert "A_KL_IAT" not in names  # No 'TEMP' in name

    def test_filter_inventory_by_a_yam_prefix(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        out = filter_inventory(inv, "a_yam_*", "all")
        for d in out:
            assert d.name.startswith("A_YAM_")
        # We expect 16 A_YAM_* columns in the fixture.
        assert len(out) >= 10

    def test_filter_inventory_engine_subsystem(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        out = filter_inventory(inv, None, "engine")
        for d in out:
            assert d.subsystem == "engine"

    def test_filter_inventory_abs_subsystem_empty(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """Fixture has no ABS-side columns — abs filter is empty."""
        inv = build_inventory(yamaha_data)
        out = filter_inventory(inv, None, "abs")
        assert out == []

    def test_fuzzy_suggestions_substring_match(
        self, yamaha_data: OBDLogData,
    ) -> None:
        inv = build_inventory(yamaha_data)
        out = fuzzy_suggestions("temp", inv)
        # Should suggest at least one TEMP-containing signal.
        assert any("TEMP" in s.upper() for s in out)


# ── list_signals ─────────────────────────────────────────────────


class TestListSignals:
    """Tests for ``list_signals``."""

    @pytest.mark.asyncio
    async def test_lists_all_signals(self) -> None:
        result = await list_signals(
            {"_session_id": FAKE_SESSION_ID, "subsystem": "all"},
        )
        # Channel labels present
        assert "Engine ECU" in result
        assert "ABS ECU" in result
        # Major K-Line signals present
        assert "A_KL_RPM" in result
        assert "A_KL_COOLANT_TEMP" in result

    @pytest.mark.asyncio
    async def test_a_yam_signals_listed_under_original_names(
        self,
    ) -> None:
        """HARNESS-19 locked decision: A_YAM_* exposed verbatim."""
        result = await list_signals(
            {"_session_id": FAKE_SESSION_ID},
        )
        # At least a few well-known Yamaha proprietary fields:
        assert "A_YAM_INJ_MS" in result
        assert "A_YAM_BATT_V" in result
        assert "A_YAM_CHT" in result

    @pytest.mark.asyncio
    async def test_pattern_filter_narrows_inventory(self) -> None:
        result = await list_signals({
            "_session_id": FAKE_SESSION_ID,
            "pattern": "*temp*",
        })
        assert "A_KL_COOLANT_TEMP" in result
        assert "A_KL_RPM" not in result

    @pytest.mark.asyncio
    async def test_abs_subsystem_shows_no_matches(self) -> None:
        """Fixture has no ABS columns — list shows zero matches."""
        result = await list_signals({
            "_session_id": FAKE_SESSION_ID,
            "subsystem": "abs",
        })
        assert "no signals match" in result.lower() or "0 match" in result

    @pytest.mark.asyncio
    async def test_units_rendered(self) -> None:
        result = await list_signals(
            {"_session_id": FAKE_SESSION_ID},
        )
        # Yamaha-proprietary units come from the curated map.
        assert " ms " in result or " ms\n" in result
        # K-Line canonical RPM unit
        assert "rpm" in result.lower()


# ── read_window ──────────────────────────────────────────────────


class TestReadWindow:
    """Tests for ``read_window``."""

    @pytest.mark.asyncio
    async def test_returns_table_with_units_row(self) -> None:
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
            "max_rows": 5,
        })
        # Header row + units row + sample rows.
        assert "Timestamp" in result
        assert "units" in result
        assert "rpm" in result.lower()

    @pytest.mark.asyncio
    async def test_downsamples_when_window_exceeds_max_rows(
        self,
    ) -> None:
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
            "max_rows": 10,
        })
        # 257 samples downsampled to 10 — should mention downsample.
        assert (
            "downsampled" in result.lower()
            or "Auto-downsampled" in result
        )

    @pytest.mark.asyncio
    async def test_unknown_signal_fuzzy_suggests(self) -> None:
        """T2 validation: unknown signal returns a helpful pivot."""
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["EGT"],
        })
        # Should not raise. Should hint at known signals.
        assert (
            "not in this session" in result.lower()
            or "did you mean" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_inverted_window_returns_clear_error(self) -> None:
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
            "start_time": "2026-05-08T12:00:00",
            "end_time": "2026-05-08T11:00:00",
        })
        assert "Invalid time window" in result

    @pytest.mark.asyncio
    async def test_window_after_session_end_returns_zero_samples(
        self,
    ) -> None:
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
            "start_time": "2030-01-01T00:00:00",
        })
        assert (
            "0 samples" in result.lower()
            or "after the session end" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_multiple_signals_in_one_call(self) -> None:
        result = await read_window({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM", "A_KL_COOLANT_TEMP"],
            "max_rows": 5,
        })
        assert "A_KL_RPM" in result
        assert "A_KL_COOLANT_TEMP" in result


# ── get_signal_stats ─────────────────────────────────────────────


class TestGetSignalStats:
    """Tests for ``get_signal_stats``."""

    @pytest.mark.asyncio
    async def test_basic_stats_include_min_max_mean_std(self) -> None:
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
        })
        # Defaults: basic + percentiles
        assert "min" in result
        assert "max" in result
        assert "mean" in result
        assert "std" in result

    @pytest.mark.asyncio
    async def test_percentiles_default_included(self) -> None:
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
        })
        for k in ("p5", "p25", "p50", "p75", "p95"):
            assert k in result, (
                f"Default include should produce {k}, got: {result}"
            )

    @pytest.mark.asyncio
    async def test_trend_and_extrema_when_requested(self) -> None:
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
            "include": ["trend", "extrema"],
        })
        assert "max_at" in result
        assert "min_at" in result
        # Trend slope or autocorr — at least one must show.
        assert (
            "linreg_slope" in result
            or "autocorr_lag1" in result
        )

    @pytest.mark.asyncio
    async def test_yamaha_proprietary_stats_computed(self) -> None:
        """A_YAM_* signals get stats just like canonical ones."""
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_YAM_INJ_MS"],
        })
        assert "A_YAM_INJ_MS" in result
        assert "mean" in result

    @pytest.mark.asyncio
    async def test_unknown_signal_returns_fuzzy_message(self) -> None:
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["DEFINITELY_NOT_A_SIGNAL"],
        })
        assert (
            "not in this session" in result.lower()
            or "did you mean" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_basic_stats_match_reference(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """Mean of A_KL_RPM matches direct computation.

        Sanity check that the percentile/mean machinery isn't off
        by a wide margin.
        """
        result = await get_signal_stats({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["A_KL_RPM"],
        })
        # Compute reference mean from the fixture directly.
        from app.harness_tools.obd_loader import try_float
        values = [
            try_float(r.get("A_KL_RPM", ""))
            for r in yamaha_data.rows
        ]
        values = [v for v in values if v is not None]
        ref_mean = sum(values) / len(values)
        # Extract the mean line from the result text.
        for line in result.splitlines():
            line = line.strip()
            if line.startswith("mean:"):
                got = float(line.split(":", 1)[1].strip())
                assert got == pytest.approx(
                    ref_mean, rel=1e-3,
                )
                return
        pytest.fail("mean line not found in get_signal_stats output")


# ── find_events ──────────────────────────────────────────────────


class TestFindEvents:
    """Tests for ``find_events``."""

    @pytest.mark.asyncio
    async def test_rpm_above_zero_finds_engine_running_window(
        self,
    ) -> None:
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "A_KL_RPM",
            "predicate": "above_threshold",
            "threshold": 0.0,
            "min_duration_seconds": 1.0,
        })
        # Bike was actively revving — at least one event must
        # match RPM > 0.
        assert "Events" in result
        assert "found" in result
        # Should not say "0 of 0" — engine was definitely on.
        assert "0 of 0 found" not in result

    @pytest.mark.asyncio
    async def test_threshold_required_for_value_predicate(
        self,
    ) -> None:
        """T1 validation: missing threshold returns actionable error."""
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "A_KL_RPM",
            "predicate": "above_threshold",
        })
        assert (
            "requires" in result.lower()
            and "threshold" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_unknown_signal_returns_fuzzy_message(self) -> None:
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "EGT_BANK_4",
            "predicate": "above_threshold",
            "threshold": 1.0,
        })
        assert (
            "not in this session" in result.lower()
            or "did you mean" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_no_events_match_provides_range_anchor(
        self,
    ) -> None:
        """When no events match a value predicate, give max in range."""
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "A_KL_RPM",
            "predicate": "above_threshold",
            "threshold": 100_000.0,
        })
        assert "No events matched" in result
        # Should mention max value or signal range.
        assert "max" in result.lower() or "range" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_predicate_finds_warmup_na(self) -> None:
        """The first row of the fixture is N/A — 'missing' must find it."""
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "A_KL_RPM",
            "predicate": "missing",
            "min_duration_seconds": 0.0,
        })
        # Warm-up N/A is a 1-sample event at session start.
        assert "Events" in result
        # Either the count is non-zero or "0 of 0" — assert NOT
        # zero of zero (would mean detector missed the warm-up).
        assert "0 of 0 found" not in result

    @pytest.mark.asyncio
    async def test_min_duration_filters_short_events(self) -> None:
        """min_duration=999s drops every event in a 4-min fixture."""
        result = await find_events({
            "_session_id": FAKE_SESSION_ID,
            "signal": "A_KL_RPM",
            "predicate": "above_threshold",
            "threshold": 0.0,
            "min_duration_seconds": 999.0,
        })
        # All events shorter than 999s — none should survive the
        # filter on a 4-min trip.
        assert "0 of 0 found" in result or "No events" in result

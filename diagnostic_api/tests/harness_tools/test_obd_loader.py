"""Unit tests for the Yamaha-aware OBD log loader.

Exercises ``detect_format``, the Yamaha-dual CSV parser, and the
metadata-DTC extractor against the real road-test fixture.

The fixture lives at ``obd_agent/fixtures/yamaha_dual_road_test_20260508.csv``
— a 257-sample real recording committed in #80 (PR #82).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness_tools.obd_loader import (
    OBDLogData,
    _parse_yamaha_metadata_dtcs,
    detect_format,
    load_obd_data,
    parse_timestamp,
    try_float,
)

# Path to the real Yamaha road-test fixture.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_YAMAHA_FIXTURE = (
    _REPO_ROOT
    / "obd_agent"
    / "fixtures"
    / "yamaha_dual_road_test_20260508.csv"
)


@pytest.fixture(scope="module")
def yamaha_data() -> OBDLogData:
    """Parse the Yamaha fixture once for the module."""
    assert _YAMAHA_FIXTURE.exists(), (
        f"Yamaha fixture missing: {_YAMAHA_FIXTURE}"
    )
    return load_obd_data(_YAMAHA_FIXTURE)


# ── Format detection ─────────────────────────────────────────────


class TestDetectFormat:
    """Tests for ``detect_format``."""

    def test_detects_yamaha_dual_from_marker_comment(self) -> None:
        """The 'Yamaha Dual' marker in the header is recognized."""
        sample = "\n".join([
            "# Yamaha Dual OBDLink EX Log",
            "# vehicle_id: TEST",
            "Timestamp,A_KL_RPM",
            "2026-05-08 11:00:00.000,1500.0",
        ])
        assert detect_format(sample) == "yamaha_dual"

    def test_detects_yamaha_from_a_kl_columns(self) -> None:
        """A_KL_ / A_YAM_ column prefixes trigger Yamaha detection
        even without the comment marker (defense-in-depth)."""
        sample = "\n".join([
            "# Ch.A: K-Line",
            "Timestamp,A_KL_RPM",
            "t,1500",
        ])
        assert detect_format(sample) == "yamaha_dual"

    def test_detects_standard_tsv(self) -> None:
        """Standard python-OBD TSV format is recognised."""
        sample = "\n".join([
            "OBD Data Log",
            "Start Time: 2025-01-01 00:00:00",
            "--------",
            "Timestamp\tRPM\tSPEED",
            "--------",
            "2025-01-01 00:00:00\t1500\t30",
        ])
        assert detect_format(sample) == "standard_tsv"

    def test_unknown_format_returns_unknown(self) -> None:
        """Non-OBD content gets the unknown tag."""
        assert detect_format("hello world") == "unknown"

    def test_detects_real_yamaha_fixture(self) -> None:
        """The committed fixture file is classified as yamaha_dual."""
        text = _YAMAHA_FIXTURE.read_text(encoding="utf-8")
        assert detect_format(text) == "yamaha_dual"


# ── Yamaha metadata DTC extractor ────────────────────────────────


class TestYamahaMetadataDTCs:
    """Tests for ``_parse_yamaha_metadata_dtcs``."""

    def test_extracts_stored_and_pending(self) -> None:
        """Stored and pending DTC lines are parsed with status tags."""
        lines = [
            "# DTCs:",
            "#   KL_Stored: 87F11043000000000000CB",
            "#   KL_Pending: 87F11047000000000000CF",
        ]
        out = _parse_yamaha_metadata_dtcs(lines)
        assert len(out) == 2
        statuses = {d.status for d in out}
        assert statuses == {"stored", "pending"}
        codes = [d.code for d in out]
        assert "87F11043000000000000CB" in codes
        assert "87F11047000000000000CF" in codes

    def test_attaches_ecu_label(self) -> None:
        """KL_ prefix maps to 'K-Line' ECU label."""
        out = _parse_yamaha_metadata_dtcs([
            "#   KL_Stored: ABCDEF0123",
        ])
        assert out[0].ecu == "K-Line"

    def test_skips_unrelated_metadata_lines(self) -> None:
        """Non-DTC metadata lines are ignored without erroring."""
        out = _parse_yamaha_metadata_dtcs([
            "# vehicle_id: TEST",
            "# Start: 2026-05-08 11:00:00",
        ])
        assert out == []


# ── End-to-end: parse the real fixture ───────────────────────────


class TestRealYamahaFixture:
    """Tests against the committed Yamaha road-test fixture.

    Validates the HARNESS-19 locked decision: ``A_YAM_*`` proprietary
    columns are exposed under their original names, and the metadata
    block's Yamaha hex DTCs are surfaced.
    """

    def test_format_classified_as_yamaha_dual(
        self, yamaha_data: OBDLogData,
    ) -> None:
        assert yamaha_data.format == "yamaha_dual"

    def test_rows_loaded(self, yamaha_data: OBDLogData) -> None:
        """The fixture has roughly 257 data rows."""
        # Fixture committed shape: 257 samples per #80 PR description.
        # Allow a small tolerance in case the committed file is
        # tweaked later (no semantic regression as long as it's
        # well over 100 samples).
        assert len(yamaha_data.rows) > 200

    def test_a_yam_columns_preserved(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """A_YAM_* proprietary columns survive the loader.

        This is the HARNESS-19 locked decision — the legacy
        format_normalizer.py drops these, the new loader must NOT.
        """
        yamaha_cols = [
            c for c in yamaha_data.columns
            if c.startswith("A_YAM_")
        ]
        # Fixture has 16 A_YAM_* columns per the header (see CSV
        # rows 9-10).
        assert len(yamaha_cols) >= 10, (
            f"Expected A_YAM_* columns to be preserved, got "
            f"{yamaha_cols}"
        )

    def test_a_kl_columns_preserved(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """K-Line canonical PIDs are present."""
        for col in (
            "A_KL_RPM",
            "A_KL_SPEED",
            "A_KL_COOLANT_TEMP",
            "A_KL_MAP",
        ):
            assert col in yamaha_data.columns, (
                f"Missing K-Line column {col}"
            )

    def test_metadata_dtcs_extracted(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """Fixture's 2 Yamaha-hex DTCs are surfaced via metadata."""
        assert len(yamaha_data.metadata_dtcs) == 2
        codes = [d.code for d in yamaha_data.metadata_dtcs]
        assert "87F11043000000000000CB" in codes
        assert "87F11047000000000000CF" in codes
        statuses = sorted(d.status for d in yamaha_data.metadata_dtcs)
        assert statuses == ["pending", "stored"]

    def test_engine_channel_present(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """Engine ECU (K-Line) channel detected from A_* columns."""
        assert "engine" in yamaha_data.channels_present

    def test_first_row_is_warmup_na(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """First data row contains N/A — sensor warm-up."""
        first = yamaha_data.rows[0]
        # Fixture row 17 (first data row) is all N/A.
        rpm_val = first.get("A_KL_RPM", "")
        assert rpm_val.strip().upper() == "N/A"

    def test_subsequent_rows_have_numeric_values(
        self, yamaha_data: OBDLogData,
    ) -> None:
        """Rows after warm-up contain parseable floats."""
        # Row index 1 should have numeric RPM (e.g. 0.0 idle).
        second = yamaha_data.rows[1]
        assert try_float(second["A_KL_RPM"]) is not None


# ── Time helpers ─────────────────────────────────────────────────


class TestTimestampParser:
    """Tests for ``parse_timestamp``."""

    def test_parses_millisecond_format(self) -> None:
        """Fixture format with .508 ms suffix parses."""
        dt = parse_timestamp("2026-05-08 11:20:40.508")
        assert dt is not None
        assert dt.year == 2026
        assert dt.second == 40

    def test_parses_second_precision(self) -> None:
        dt = parse_timestamp("2026-05-08 11:20:40")
        assert dt is not None

    def test_parses_iso_t_separator(self) -> None:
        dt = parse_timestamp("2026-05-08T11:20:40")
        assert dt is not None

    def test_returns_none_for_garbage(self) -> None:
        assert parse_timestamp("not a date") is None

    def test_returns_none_for_empty(self) -> None:
        assert parse_timestamp("") is None


class TestTryFloat:
    """Tests for ``try_float`` missing-value handling."""

    def test_n_a_token_returns_none(self) -> None:
        assert try_float("N/A") is None

    def test_n_a_lowercase_returns_none(self) -> None:
        assert try_float("n/a") is None

    def test_empty_returns_none(self) -> None:
        assert try_float("") is None
        assert try_float("   ") is None

    def test_nan_returns_none(self) -> None:
        assert try_float("nan") is None

    def test_numeric_string_returns_float(self) -> None:
        assert try_float("3.14") == pytest.approx(3.14)

    def test_integer_string_returns_float(self) -> None:
        assert try_float("42") == 42.0

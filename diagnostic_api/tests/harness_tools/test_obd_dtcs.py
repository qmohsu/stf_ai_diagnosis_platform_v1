"""Unit tests for the OBD DTC tools (HARNESS-19).

Exercises ``list_dtcs`` and ``lookup_dtc`` against the real Yamaha
road-test fixture.  Validates the locked design decision: Yamaha
proprietary hex DTCs are surfaced honestly with a manual-search
pivot, never fabricated.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.harness_tools.obd_dtcs import (
    _classify_code,
    list_dtcs,
    lookup_dtc,
)
from app.harness_tools.obd_loader import OBDLogData, load_obd_data

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
    """Short-circuit DB lookup with the parsed fixture."""
    with patch(
        "app.harness_tools.obd_dtcs.load_for_session",
        return_value=yamaha_data,
    ):
        yield


# ── _classify_code ───────────────────────────────────────────────


class TestClassifyCode:
    """Unit tests for the format classifier."""

    def test_standard_p_code(self) -> None:
        assert _classify_code("P0117") == "standard"
        assert _classify_code("p0117") == "standard"

    def test_standard_c_code(self) -> None:
        assert _classify_code("C0035") == "standard"

    def test_standard_b_code(self) -> None:
        assert _classify_code("B1234") == "standard"

    def test_standard_u_code(self) -> None:
        assert _classify_code("U0100") == "standard"

    def test_yamaha_hex_long_hex(self) -> None:
        """The fixture's 22-char hex codes classify as yamaha_hex."""
        assert _classify_code(
            "87F11043000000000000CB",
        ) == "yamaha_hex"

    def test_unknown_short_string(self) -> None:
        assert _classify_code("X1234") == "unknown"

    def test_unknown_too_short_hex(self) -> None:
        """8-byte+ minimum for Yamaha hex classification."""
        assert _classify_code("ABCDEF") == "unknown"


# ── list_dtcs against the real fixture ───────────────────────────


class TestListDTCsRealFixture:
    """Tests for ``list_dtcs`` against the Yamaha fixture.

    The fixture has 2 Yamaha-hex DTCs in the metadata block
    (1 stored, 1 pending) and no column-level DTCs.
    """

    @pytest.mark.asyncio
    async def test_lists_two_yamaha_hex_dtcs(self) -> None:
        """Fixture metadata yields exactly 2 DTCs."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
            "status": "all",
            "ecu": "all",
        })
        assert "87F11043000000000000CB" in result
        assert "87F11047000000000000CF" in result

    @pytest.mark.asyncio
    async def test_groups_yamaha_hex_separately(self) -> None:
        """Output has a 'Yamaha-proprietary' section header."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
        })
        assert "Yamaha-proprietary" in result

    @pytest.mark.asyncio
    async def test_separates_stored_vs_pending(self) -> None:
        """Both status values appear in the output."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
        })
        assert "STORED" in result
        assert "PENDING" in result

    @pytest.mark.asyncio
    async def test_status_filter_stored(self) -> None:
        """status='stored' surfaces only the stored code."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
            "status": "stored",
        })
        assert "87F11043000000000000CB" in result
        assert "87F11047000000000000CF" not in result

    @pytest.mark.asyncio
    async def test_status_filter_pending(self) -> None:
        """status='pending' surfaces only the pending code."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
            "status": "pending",
        })
        assert "87F11047000000000000CF" in result
        assert "87F11043000000000000CB" not in result

    @pytest.mark.asyncio
    async def test_abs_ecu_filter_empty(self) -> None:
        """Fixture has no ABS DTCs — abs filter is empty."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
            "ecu": "abs",
        })
        # Should not surface either K-Line code.
        assert "87F11043000000000000CB" not in result

    @pytest.mark.asyncio
    async def test_notes_guide_agent_to_lookup_dtc(self) -> None:
        """Output reminds the agent to call lookup_dtc."""
        result = await list_dtcs({
            "_session_id": FAKE_SESSION_ID,
        })
        assert "lookup_dtc" in result


class TestListDTCsEmptySession:
    """Tests for sessions with no DTCs (informational, not error)."""

    @pytest.mark.asyncio
    async def test_no_dtcs_returns_informational_message(self) -> None:
        """An empty DTC list is information, not an error."""
        empty_data = OBDLogData(
            format="standard_tsv",
            rows=[
                {"Timestamp": "2026-05-08 11:00:00", "RPM": "1500"},
            ],
            columns=["RPM"],
            metadata_dtcs=[],
            metadata_lines=[],
            channels_present={"engine"},
        )
        with patch(
            "app.harness_tools.obd_dtcs.load_for_session",
            return_value=empty_data,
        ):
            result = await list_dtcs({
                "_session_id": FAKE_SESSION_ID,
            })
        assert "No DTCs found" in result


# ── lookup_dtc ───────────────────────────────────────────────────


class TestLookupDTCYamahaHex:
    """Tests for the honest 'no decoder' Yamaha hex pivot."""

    @pytest.mark.asyncio
    async def test_returns_no_decoder_message(self) -> None:
        """The locked decision: surface honestly, do not fabricate."""
        result = await lookup_dtc({
            "code": "87F11043000000000000CB",
        })
        assert "Yamaha-proprietary" in result
        assert "No decoder available" in result

    @pytest.mark.asyncio
    async def test_suggests_search_manual_pivot(self) -> None:
        """Output guides the agent to search_manual."""
        result = await lookup_dtc({
            "code": "87F11043000000000000CB",
        })
        assert "search_manual" in result

    @pytest.mark.asyncio
    async def test_does_not_fabricate_decoding(self) -> None:
        """Crucial: no fake P-code mapping or made-up description."""
        result = await lookup_dtc({
            "code": "87F11043000000000000CB",
        })
        # Should NOT claim it's a standard P/C/B/U code.
        assert "Description: Coolant" not in result
        assert "P0117" not in result


class TestLookupDTCStandard:
    """Tests for standard P/C/B/U code decoding."""

    @pytest.mark.asyncio
    async def test_classifies_p_code_as_powertrain(self) -> None:
        result = await lookup_dtc({"code": "P0117"})
        assert "standard OBD-II code" in result
        assert "powertrain" in result.lower() or "engine" in result.lower()

    @pytest.mark.asyncio
    async def test_classifies_c_code_as_chassis(self) -> None:
        result = await lookup_dtc({"code": "C0035"})
        assert "chassis" in result.lower()

    @pytest.mark.asyncio
    async def test_includes_related_signals_for_known_p_code(
        self,
    ) -> None:
        """Curated map includes related signals for P0117."""
        result = await lookup_dtc({"code": "P0117"})
        assert "COOLANT_TEMP" in result

    @pytest.mark.asyncio
    async def test_suggests_search_manual_next_step(self) -> None:
        """Output suggests pulling the manufacturer's procedure."""
        result = await lookup_dtc({"code": "P0117"})
        assert "search_manual" in result


class TestLookupDTCUnknown:
    """Tests for unrecognized code formats."""

    @pytest.mark.asyncio
    async def test_unknown_format_gives_actionable_error(self) -> None:
        result = await lookup_dtc({"code": "X1234"})
        assert "not a recognised" in result.lower() or "verify" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_code_validation_error(self) -> None:
        result = await lookup_dtc({"code": ""})
        assert "Validation error" in result or "non-empty" in result.lower()

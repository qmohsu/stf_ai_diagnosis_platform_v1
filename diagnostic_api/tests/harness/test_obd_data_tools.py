"""Tests for the read_obd_data tool (OBD data reader)."""

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FAKE_SESSION_ID = str(uuid.uuid4())

# Minimal TSV log for testing (4-line header + columns + data).
SAMPLE_TSV = """\
OBD Data Log
Start Time: 2025-07-23 14:42:16
Log Interval: 1.0 seconds
--------------------------------------------------------------------------------
Timestamp\tRPM\tSPEED\tCOOLANT_TEMP\tFUEL_TYPE\tGET_DTC\tVIN
--------------------------------------------------------------------------------
2025-07-23 14:42:16\t0.00\t0.00\t32.00\tGasoline\t[]\tbytearray(b'JHMGK5830HX202404')
2025-07-23 14:42:19\t750.00\t0.00\t35.00\tGasoline\t[]\tbytearray(b'JHMGK5830HX202404')
2025-07-23 14:42:21\t2100.00\t45.50\t45.00\tGasoline\t[('P0300', 'Random Misfire')]\tbytearray(b'JHMGK5830HX202404')
2025-07-23 14:42:23\t2200.00\t48.00\t46.00\tGasoline\t[('P0300', 'Random Misfire')]\tbytearray(b'JHMGK5830HX202404')
2025-07-23 14:42:25\t1800.00\t42.00\t47.00\tGasoline\t[]\tbytearray(b'JHMGK5830HX202404')
"""


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def tmp_log(tmp_path: Path) -> Path:
    """Write the sample TSV to a temp file and return its path."""
    log_file = tmp_path / "test_session.txt"
    log_file.write_text(SAMPLE_TSV, encoding="utf-8")
    return log_file


@pytest.fixture(autouse=True)
def _mock_resolve(tmp_log: Path):
    """Patch _resolve_log_path so no real DB is needed."""
    with patch(
        "app.harness_tools.obd_data_tools._resolve_log_path",
        return_value=tmp_log,
    ):
        yield


# ------------------------------------------------------------------
# Tests: overview mode
# ------------------------------------------------------------------


class TestOverviewMode:
    """Tests for read_obd_data with no signals (overview)."""

    @pytest.mark.asyncio
    async def test_overview_returns_signal_list(self):
        """Overview lists available numeric PIDs."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data(
            {"_session_id": FAKE_SESSION_ID},
        )

        assert "=== OBD Data Overview ===" in result
        assert "RPM (rpm)" in result
        assert "SPEED (km/h)" in result
        assert "COOLANT_TEMP (degC)" in result

    @pytest.mark.asyncio
    async def test_overview_shows_time_range(self):
        """Overview includes first and last timestamps."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data(
            {"_session_id": FAKE_SESSION_ID},
        )

        assert "2025-07-23 14:42:16" in result
        assert "2025-07-23 14:42:25" in result

    @pytest.mark.asyncio
    async def test_overview_shows_dtcs(self):
        """Overview extracts DTC codes from data."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data(
            {"_session_id": FAKE_SESSION_ID},
        )

        assert "P0300" in result

    @pytest.mark.asyncio
    async def test_overview_shows_row_count(self):
        """Overview reports total data rows."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data(
            {"_session_id": FAKE_SESSION_ID},
        )

        assert "5 rows" in result

    @pytest.mark.asyncio
    async def test_overview_excludes_metadata_cols(self):
        """Overview does not list FUEL_TYPE, VIN, etc."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data(
            {"_session_id": FAKE_SESSION_ID},
        )

        assert "FUEL_TYPE" not in result
        assert "VIN" not in result
        assert "GET_DTC" not in result


# ------------------------------------------------------------------
# Tests: signal query mode
# ------------------------------------------------------------------


class TestSignalQuery:
    """Tests for read_obd_data with specific signals."""

    @pytest.mark.asyncio
    async def test_returns_filtered_table(self):
        """Signal query returns a table with requested PIDs."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["RPM", "SPEED"],
        })

        assert "Timestamp" in result
        assert "RPM" in result
        assert "SPEED" in result
        # COOLANT_TEMP should not appear in the table.
        assert "COOLANT_TEMP" not in result
        # Check actual values are present.
        assert "2100.00" in result

    @pytest.mark.asyncio
    async def test_accepts_semantic_names(self):
        """Semantic names like engine_rpm are resolved."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["engine_rpm"],
        })

        # Should resolve to RPM column.
        assert "RPM" in result
        assert "2100.00" in result

    @pytest.mark.asyncio
    async def test_unknown_signal_noted(self):
        """Unknown signals produce a note in output."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["RPM", "FAKE_SIGNAL"],
        })

        assert "RPM" in result
        assert "FAKE_SIGNAL" in result
        assert "Unrecognized" in result

    @pytest.mark.asyncio
    async def test_all_unknown_signals_error(self):
        """All-unknown signal list returns informative error."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["FAKE1", "FAKE2"],
        })

        assert "None of the requested signals" in result
        assert "Available PIDs" in result

    @pytest.mark.asyncio
    async def test_time_range_filter(self):
        """start_time/end_time filters to a time window."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["RPM"],
            "start_time": "2025-07-23T14:42:20",
            "end_time": "2025-07-23T14:42:24",
        })

        # Only rows at :21 and :23 should match.
        assert "2100.00" in result
        assert "2200.00" in result
        # Rows at :16 (RPM=0), :19 (RPM=750), :25 (RPM=1800)
        # should be excluded.
        data_lines = [
            l for l in result.strip().split("\n")
            if l.startswith("2025-")
        ]
        assert len(data_lines) == 2

    @pytest.mark.asyncio
    async def test_every_nth_downsampling(self):
        """every_nth returns every Nth row."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": ["RPM"],
            "every_nth": 2,
        })

        # 5 rows, every 2nd → rows 0, 2, 4 → 3 data rows.
        lines = [
            l for l in result.strip().split("\n")
            if l and not l.startswith("[")
        ]
        # 1 header + 3 data = 4 lines.
        assert len(lines) == 4


# ------------------------------------------------------------------
# Tests: edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Edge case handling for read_obd_data."""

    @pytest.mark.asyncio
    async def test_missing_file(self, _mock_resolve, tmp_path):
        """Non-existent file returns informative error."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        missing = tmp_path / "nonexistent.txt"
        with patch(
            "app.harness_tools.obd_data_tools"
            "._resolve_log_path",
            return_value=missing,
        ):
            result = await read_obd_data(
                {"_session_id": FAKE_SESSION_ID},
            )

        assert "not found" in result

    @pytest.mark.asyncio
    async def test_empty_signals_overview(self):
        """Empty signals list triggers overview mode."""
        from app.harness_tools.obd_data_tools import (
            read_obd_data,
        )

        result = await read_obd_data({
            "_session_id": FAKE_SESSION_ID,
            "signals": [],
        })

        # Empty list is falsy → triggers overview mode.
        assert "=== OBD Data Overview ===" in result

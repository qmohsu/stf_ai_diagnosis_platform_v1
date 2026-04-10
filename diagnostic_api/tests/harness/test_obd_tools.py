"""Tests for OBD diagnostic tool wrappers."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ------------------------------------------------------------------
# Fixture data
# ------------------------------------------------------------------

FAKE_SESSION_ID = str(uuid.uuid4())

FAKE_RESULT_PAYLOAD = {
    "value_statistics": {
        "stats": {
            "engine_rpm": {
                "mean": 2100.0,
                "std": 340.5,
                "min": 780.0,
                "max": 4200.0,
                "p5": 850.0,
                "p25": 1400.0,
                "p50": 2100.0,
                "p75": 2800.0,
                "p95": 3900.0,
                "autocorrelation_lag1": 0.95,
                "mean_abs_change": 50.0,
                "max_abs_change": 500.0,
                "energy": 5000000.0,
                "entropy": 3.2,
                "valid_count": 500,
            },
            "coolant_temp": {
                "mean": 92.0,
                "std": 3.0,
                "min": 85.0,
                "max": 98.0,
                "p5": 86.0,
                "p25": 90.0,
                "p50": 92.0,
                "p75": 94.0,
                "p95": 97.0,
                "autocorrelation_lag1": 0.99,
                "mean_abs_change": 0.5,
                "max_abs_change": 3.0,
                "energy": 42000.0,
                "entropy": 2.1,
                "valid_count": 500,
            },
        },
        "column_units": {
            "engine_rpm": "rpm",
            "coolant_temp": "C",
        },
        "resample_interval_seconds": 1.0,
    },
    "anomaly_events": [
        {
            "time_window": [
                "2025-01-01T12:03:00",
                "2025-01-01T12:05:00",
            ],
            "signals": ["engine_rpm"],
            "pattern": "sudden drop from 2100 to 780 rpm",
            "context": "cruise",
            "severity": "high",
            "detector": "changepoint",
            "score": 0.87,
        },
        {
            "time_window": [
                "2025-01-01T12:10:00",
                "2025-01-01T12:12:00",
            ],
            "signals": ["coolant_temp"],
            "pattern": "temperature spike",
            "context": "idle",
            "severity": "medium",
            "detector": "isolation_forest",
            "score": 0.65,
        },
    ],
    "clue_details": [
        {
            "rule_id": "STAT_001",
            "category": "statistical",
            "clue": "Engine RPM unstable during cruise",
            "evidence": [
                "engine_rpm.std=340.5",
                "engine_rpm.max_abs_change=500.0",
            ],
            "severity": "warning",
        },
        {
            "rule_id": "ANOM_002",
            "category": "anomaly",
            "clue": "Coolant temperature spike at idle",
            "evidence": [
                "coolant_temp.max=98",
            ],
            "severity": "critical",
        },
    ],
}

FAKE_PARSED_SUMMARY = {
    "parse_ok": "YES",
    "vehicle_id": "V-TEST123",
    "time_range": "2025-01-01T12:00:00 to 2025-01-01T12:30:00",
    "dtc_codes": "P0300, P0420",
    "pid_summary": "RPM: 780-4200 rpm, COOLANT: 85-98 C",
    "anomaly_events": "2 events detected",
    "diagnostic_clues": "RPM unstable, coolant spike",
}


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

_UNSET = object()


def _make_mock_session(
    session_id=FAKE_SESSION_ID,
    result_payload=_UNSET,
    parsed_summary_payload=_UNSET,
    status="COMPLETED",
    vehicle_id="V-TEST123",
):
    """Build a mock OBDAnalysisSession."""
    s = MagicMock()
    s.id = uuid.UUID(session_id)
    s.status = status
    s.vehicle_id = vehicle_id
    s.result_payload = (
        FAKE_RESULT_PAYLOAD
        if result_payload is _UNSET
        else result_payload
    )
    s.parsed_summary_payload = (
        FAKE_PARSED_SUMMARY
        if parsed_summary_payload is _UNSET
        else parsed_summary_payload
    )
    return s


@pytest.fixture(autouse=True)
def _mock_db():
    """Patch SessionLocal so no real DB is needed."""
    mock_db = MagicMock()
    mock_session = _make_mock_session()
    mock_db.query.return_value.filter.return_value.first.return_value = (
        mock_session
    )
    with patch(
        "app.harness_tools.obd_tools.SessionLocal",
        return_value=mock_db,
    ):
        yield mock_db


# ------------------------------------------------------------------
# Tests: get_pid_statistics
# ------------------------------------------------------------------


class TestGetPidStatistics:
    """Tests for the get_pid_statistics tool handler."""

    @pytest.mark.asyncio
    async def test_returns_formatted_text(self):
        """Output contains per-signal stat lines."""
        from app.harness_tools.obd_tools import (
            get_pid_statistics,
        )

        result = await get_pid_statistics(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "engine_rpm" in result
        assert "coolant_temp" in result
        assert "mean=" in result
        assert "std=" in result
        assert "(rpm)" in result

    @pytest.mark.asyncio
    async def test_empty_stats(self, _mock_db):
        """Empty stats returns informative message."""
        from app.harness_tools.obd_tools import (
            get_pid_statistics,
        )

        empty_session = _make_mock_session(
            result_payload={"value_statistics": {"stats": {}}},
        )
        _mock_db.query.return_value.filter.return_value.first.return_value = (
            empty_session
        )

        result = await get_pid_statistics(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "No PID statistics" in result


# ------------------------------------------------------------------
# Tests: detect_anomalies
# ------------------------------------------------------------------


class TestDetectAnomalies:
    """Tests for the detect_anomalies tool handler."""

    @pytest.mark.asyncio
    async def test_returns_events(self):
        """Output lists anomaly events with severity."""
        from app.harness_tools.obd_tools import detect_anomalies

        result = await detect_anomalies(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "[HIGH]" in result
        assert "[MEDIUM]" in result
        assert "engine_rpm" in result
        assert "score=0.87" in result

    @pytest.mark.asyncio
    async def test_focus_signals_filter(self, _mock_db):
        """focus_signals filters to matching events only."""
        from app.harness_tools.obd_tools import detect_anomalies

        result = await detect_anomalies({
            "session_id": FAKE_SESSION_ID,
            "focus_signals": ["coolant_temp"],
        })

        assert isinstance(result, str)
        assert "coolant_temp" in result
        assert "engine_rpm" not in result

    @pytest.mark.asyncio
    async def test_no_events(self, _mock_db):
        """Empty anomaly_events returns descriptive message."""
        from app.harness_tools.obd_tools import detect_anomalies

        empty_session = _make_mock_session(
            result_payload={"anomaly_events": []},
        )
        _mock_db.query.return_value.filter.return_value.first.return_value = (
            empty_session
        )

        result = await detect_anomalies(
            {"session_id": FAKE_SESSION_ID},
        )

        assert result == "No anomaly events detected."


# ------------------------------------------------------------------
# Tests: generate_clues
# ------------------------------------------------------------------


class TestGenerateClues:
    """Tests for the generate_clues tool handler."""

    @pytest.mark.asyncio
    async def test_returns_clue_text(self):
        """Output lists clues with rule IDs."""
        from app.harness_tools.obd_tools import generate_clues

        result = await generate_clues(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "STAT_001" in result
        assert "ANOM_002" in result
        assert "Evidence:" in result

    @pytest.mark.asyncio
    async def test_no_clues(self, _mock_db):
        """Empty clue_details returns descriptive message."""
        from app.harness_tools.obd_tools import generate_clues

        empty_session = _make_mock_session(
            result_payload={"clue_details": []},
        )
        _mock_db.query.return_value.filter.return_value.first.return_value = (
            empty_session
        )

        result = await generate_clues(
            {"session_id": FAKE_SESSION_ID},
        )

        assert result == "No diagnostic clues generated."


# ------------------------------------------------------------------
# Tests: get_session_context
# ------------------------------------------------------------------


class TestGetSessionContext:
    """Tests for the get_session_context tool handler."""

    @pytest.mark.asyncio
    async def test_returns_summary(self):
        """Output contains parsed summary fields."""
        from app.harness_tools.obd_tools import (
            get_session_context,
        )

        result = await get_session_context(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "V-TEST123" in result
        assert "P0300" in result
        assert "Vehicle:" in result
        assert "DTC codes:" in result

    @pytest.mark.asyncio
    async def test_no_parsed_summary(self, _mock_db):
        """Missing parsed_summary returns descriptive message."""
        from app.harness_tools.obd_tools import (
            get_session_context,
        )

        no_summary = _make_mock_session(
            parsed_summary_payload=None,
        )
        _mock_db.query.return_value.filter.return_value.first.return_value = (
            no_summary
        )

        result = await get_session_context(
            {"session_id": FAKE_SESSION_ID},
        )

        assert isinstance(result, str)
        assert "no parsed summary" in result.lower()


# ------------------------------------------------------------------
# Tests: session not found
# ------------------------------------------------------------------


class TestSessionErrors:
    """Error handling for invalid session lookups."""

    @pytest.mark.asyncio
    async def test_session_not_found(self, _mock_db):
        """Non-existent session_id produces ValueError."""
        from app.harness_tools.obd_tools import (
            get_pid_statistics,
        )

        _mock_db.query.return_value.filter.return_value.first.return_value = (
            None
        )

        with pytest.raises(ValueError, match="not found"):
            await get_pid_statistics(
                {"session_id": FAKE_SESSION_ID},
            )

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(self):
        """Malformed UUID produces ValueError."""
        from app.harness_tools.obd_tools import (
            get_pid_statistics,
        )

        with pytest.raises(ValueError, match="Invalid"):
            await get_pid_statistics(
                {"session_id": "not-a-uuid"},
            )

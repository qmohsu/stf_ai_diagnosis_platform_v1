"""Tests for history tool wrappers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.harness_tools.history_tools import search_case_history


# ------------------------------------------------------------------
# Fixture data
# ------------------------------------------------------------------


def _make_history_row(
    diagnosis_text="Engine misfire on cylinder 3.",
    provider="local",
    model_name="qwen3.5:27b",
    created_at=None,
):
    """Build a mock DiagnosisHistory row."""
    row = MagicMock()
    row.diagnosis_text = diagnosis_text
    row.provider = provider
    row.model_name = model_name
    row.created_at = created_at or datetime(2025, 6, 15, 14, 30)
    return row


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_db():
    """Patch SessionLocal so no real DB is needed."""
    mock_db = MagicMock()
    # Default: no results.
    mock_db.query.return_value \
        .join.return_value \
        .filter.return_value \
        .filter.return_value \
        .order_by.return_value \
        .limit.return_value \
        .all.return_value = []
    # Single-filter path (no vehicle_id).
    mock_db.query.return_value \
        .join.return_value \
        .filter.return_value \
        .order_by.return_value \
        .limit.return_value \
        .all.return_value = []

    with patch(
        "app.harness_tools.history_tools.SessionLocal",
        return_value=mock_db,
    ):
        yield mock_db


def _set_rows(mock_db, rows, *, with_vehicle_filter=False):
    """Wire mock_db to return *rows* from the query chain."""
    if with_vehicle_filter:
        mock_db.query.return_value \
            .join.return_value \
            .filter.return_value \
            .filter.return_value \
            .order_by.return_value \
            .limit.return_value \
            .all.return_value = rows
    else:
        mock_db.query.return_value \
            .join.return_value \
            .filter.return_value \
            .order_by.return_value \
            .limit.return_value \
            .all.return_value = rows


# ------------------------------------------------------------------
# Tests: search_case_history
# ------------------------------------------------------------------


class TestSearchCaseHistory:
    """Tests for the search_case_history tool handler."""

    @pytest.mark.asyncio
    async def test_returns_str(self, _mock_db):
        """Output is always a string."""
        _set_rows(_mock_db, [_make_history_row()])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self, _mock_db):
        """Output contains timestamp, provider, model, and text."""
        _set_rows(_mock_db, [
            _make_history_row(
                diagnosis_text="Cylinder 3 misfire detected.",
                provider="local",
                model_name="qwen3.5:27b",
                created_at=datetime(2025, 6, 15, 14, 30),
            ),
        ])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        assert "[2025-06-15 14:30]" in result
        assert "local/qwen3.5:27b" in result
        assert "Cylinder 3 misfire" in result

    @pytest.mark.asyncio
    async def test_multiple_results(self, _mock_db):
        """Multiple rows produce multi-line output."""
        _set_rows(_mock_db, [
            _make_history_row(diagnosis_text="First case."),
            _make_history_row(diagnosis_text="Second case."),
        ])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        assert "First case." in result
        assert "Second case." in result
        assert result.count("\n") >= 1

    @pytest.mark.asyncio
    async def test_no_results_message(self):
        """Empty query result returns descriptive message."""
        result = await search_case_history(
            {"dtc_codes": ["P9999"]},
        )

        assert result == "No similar past cases found."

    @pytest.mark.asyncio
    async def test_empty_dtc_codes(self):
        """Empty dtc_codes list returns early with message."""
        result = await search_case_history(
            {"dtc_codes": []},
        )

        assert result == "No DTC codes provided for case search."

    @pytest.mark.asyncio
    async def test_truncates_long_diagnosis(self, _mock_db):
        """Diagnosis text longer than 300 chars is truncated."""
        long_text = "A" * 400
        _set_rows(_mock_db, [
            _make_history_row(diagnosis_text=long_text),
        ])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        # 300 chars + "..." suffix.
        assert "A" * 300 + "..." in result
        assert "A" * 301 not in result

    @pytest.mark.asyncio
    async def test_none_diagnosis_text(self, _mock_db):
        """None diagnosis_text is treated as empty string."""
        _set_rows(_mock_db, [
            _make_history_row(diagnosis_text=None),
        ])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        assert isinstance(result, str)
        assert "local/qwen3.5:27b" in result

    @pytest.mark.asyncio
    async def test_none_created_at(self, _mock_db):
        """None created_at shows 'unknown' timestamp."""
        row = _make_history_row()
        row.created_at = None
        _set_rows(_mock_db, [row])

        result = await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        assert "[unknown]" in result

    @pytest.mark.asyncio
    async def test_db_session_closed(self, _mock_db):
        """DB session is closed even on success."""
        _set_rows(_mock_db, [_make_history_row()])

        await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        _mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_session_closed_on_empty(self, _mock_db):
        """DB session is closed even when no results."""
        await search_case_history(
            {"dtc_codes": ["P0300"]},
        )

        _mock_db.close.assert_called_once()

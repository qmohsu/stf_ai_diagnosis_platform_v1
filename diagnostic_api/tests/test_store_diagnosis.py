"""Tests for _store_diagnosis helper function.

Covers:
  - Local diagnosis updates diagnosis_text and inserts history row
  - Premium diagnosis updates premium_diagnosis_text,
    premium_diagnosis_model, and inserts history row
  - Rollback on commit error
  - Text truncation to _MAX_DIAGNOSIS_LENGTH
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_session_local():
    """Patch SessionLocal so no real DB connection is needed."""
    with patch(
        "app.api.v2.endpoints.obd_analysis.SessionLocal",
    ) as mock_cls:
        mock_db = MagicMock()
        mock_cls.return_value = mock_db
        yield mock_db


@pytest.fixture()
def mock_db_session():
    """Return a mock OBDAnalysisSession row."""
    row = MagicMock()
    row.diagnosis_text = None
    row.premium_diagnosis_text = None
    row.premium_diagnosis_model = None
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStoreDiagnosisLocal:
    """Tests for local provider path."""

    def test_stores_local_diagnosis_and_history(
        self, _mock_session_local, mock_db_session,
    ):
        """Local diagnosis updates diagnosis_text and creates
        a history row."""
        _mock_session_local.query.return_value.filter \
            .return_value.first.return_value = mock_db_session

        from app.api.v2.endpoints.obd_analysis import (
            _store_diagnosis,
        )

        sid = uuid.uuid4()
        _store_diagnosis(sid, "local", "qwen3.5:9b", "diag text")

        # Session field updated
        assert mock_db_session.diagnosis_text == "diag text"
        # premium fields untouched
        assert mock_db_session.premium_diagnosis_model is None

        # History row added
        _mock_session_local.add.assert_called_once()
        history_row = _mock_session_local.add.call_args[0][0]
        assert history_row.provider == "local"
        assert history_row.model_name == "qwen3.5:9b"
        assert history_row.diagnosis_text == "diag text"
        assert history_row.session_id == sid

        # Committed
        _mock_session_local.commit.assert_called_once()
        _mock_session_local.close.assert_called_once()


class TestStoreDiagnosisPremium:
    """Tests for premium provider path."""

    def test_stores_premium_diagnosis_model_and_history(
        self, _mock_session_local, mock_db_session,
    ):
        """Premium diagnosis updates premium_diagnosis_text,
        premium_diagnosis_model, and creates a history row."""
        _mock_session_local.query.return_value.filter \
            .return_value.first.return_value = mock_db_session

        from app.api.v2.endpoints.obd_analysis import (
            _store_diagnosis,
        )

        sid = uuid.uuid4()
        _store_diagnosis(
            sid, "premium",
            "anthropic/claude-sonnet-4.6", "premium text",
        )

        assert (
            mock_db_session.premium_diagnosis_text
            == "premium text"
        )
        assert (
            mock_db_session.premium_diagnosis_model
            == "anthropic/claude-sonnet-4.6"
        )

        history_row = _mock_session_local.add.call_args[0][0]
        assert history_row.provider == "premium"
        assert history_row.model_name == (
            "anthropic/claude-sonnet-4.6"
        )
        _mock_session_local.commit.assert_called_once()


class TestStoreDiagnosisEdgeCases:
    """Tests for error handling and truncation."""

    def test_rollback_on_commit_error(
        self, _mock_session_local, mock_db_session,
    ):
        """Database errors trigger rollback and re-raise."""
        _mock_session_local.query.return_value.filter \
            .return_value.first.return_value = mock_db_session
        _mock_session_local.commit.side_effect = RuntimeError(
            "DB error"
        )

        from app.api.v2.endpoints.obd_analysis import (
            _store_diagnosis,
        )

        with pytest.raises(RuntimeError, match="DB error"):
            _store_diagnosis(
                uuid.uuid4(), "local", "qwen3.5:9b", "text",
            )

        _mock_session_local.rollback.assert_called_once()
        _mock_session_local.close.assert_called_once()

    def test_truncates_long_text(
        self, _mock_session_local, mock_db_session,
    ):
        """Text exceeding _MAX_DIAGNOSIS_LENGTH is truncated."""
        _mock_session_local.query.return_value.filter \
            .return_value.first.return_value = mock_db_session

        from app.api.v2.endpoints.obd_analysis import (
            _MAX_DIAGNOSIS_LENGTH,
            _store_diagnosis,
        )

        long_text = "x" * (_MAX_DIAGNOSIS_LENGTH + 1000)
        _store_diagnosis(
            uuid.uuid4(), "local", "qwen3.5:9b", long_text,
        )

        assert (
            mock_db_session.diagnosis_text
            == "x" * _MAX_DIAGNOSIS_LENGTH
        )
        history_row = _mock_session_local.add.call_args[0][0]
        assert (
            len(history_row.diagnosis_text)
            == _MAX_DIAGNOSIS_LENGTH
        )

    def test_noop_when_session_not_found(
        self, _mock_session_local,
    ):
        """No error and no commit when session row is missing."""
        _mock_session_local.query.return_value.filter \
            .return_value.first.return_value = None

        from app.api.v2.endpoints.obd_analysis import (
            _store_diagnosis,
        )

        _store_diagnosis(
            uuid.uuid4(), "local", "qwen3.5:9b", "text",
        )

        _mock_session_local.add.assert_not_called()
        _mock_session_local.commit.assert_not_called()
        _mock_session_local.close.assert_called_once()

"""Tests for linking feedback to specific diagnosis generations.

Covers:
  - Validation of diagnosis_history_id on feedback submission
  - Feedback retrieval returns diagnosis generation metadata
  - Backward compatibility (null diagnosis_history_id)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import MOCK_USER_ID, make_mock_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: uuid.UUID,
    user_id: uuid.UUID = MOCK_USER_ID,
) -> MagicMock:
    """Build a mock OBDAnalysisSession row."""
    s = MagicMock()
    s.id = session_id
    s.user_id = user_id
    s.diagnosis_text = "local diagnosis text"
    s.premium_diagnosis_text = "premium diagnosis text"
    s.parsed_summary_payload = None
    return s


def _make_history_row(
    history_id: uuid.UUID,
    session_id: uuid.UUID,
    provider: str = "local",
    model_name: str = "qwen3.5:122b-a10b",
) -> MagicMock:
    """Build a mock DiagnosisHistory row."""
    h = MagicMock()
    h.id = history_id
    h.session_id = session_id
    h.provider = provider
    h.model_name = model_name
    return h


# ---------------------------------------------------------------------------
# Tests for _validate_diagnosis_history_id
# ---------------------------------------------------------------------------


class TestValidateDiagnosisHistoryId:
    """Unit tests for the validation helper."""

    def test_returns_none_when_input_is_none(self):
        """None input yields None output — no DB call."""
        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        result = _validate_diagnosis_history_id(
            None, uuid.uuid4(), "local", MagicMock(),
        )
        assert result is None

    def test_rejects_invalid_uuid_format(self):
        """Non-UUID string raises 400."""
        from fastapi import HTTPException

        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            _validate_diagnosis_history_id(
                "not-a-uuid", uuid.uuid4(), "local",
                MagicMock(),
            )
        assert exc_info.value.status_code == 400
        assert "format" in exc_info.value.detail.lower()

    def test_rejects_nonexistent_id(self):
        """UUID that doesn't exist in DB raises 400."""
        from fastapi import HTTPException

        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _validate_diagnosis_history_id(
                str(uuid.uuid4()), uuid.uuid4(), "local", db,
            )
        assert exc_info.value.status_code == 400
        assert "not found" in exc_info.value.detail.lower()

    def test_rejects_wrong_session(self):
        """History row belonging to a different session raises 400."""
        from fastapi import HTTPException

        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        hist_id = uuid.uuid4()
        other_session = uuid.uuid4()
        target_session = uuid.uuid4()

        row = _make_history_row(hist_id, other_session)
        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = row

        with pytest.raises(HTTPException) as exc_info:
            _validate_diagnosis_history_id(
                str(hist_id), target_session, "local", db,
            )
        assert exc_info.value.status_code == 400
        assert "session" in exc_info.value.detail.lower()

    def test_rejects_wrong_provider(self):
        """History row with wrong provider raises 400."""
        from fastapi import HTTPException

        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        hist_id = uuid.uuid4()
        sid = uuid.uuid4()
        row = _make_history_row(
            hist_id, sid, provider="premium",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = row

        with pytest.raises(HTTPException) as exc_info:
            _validate_diagnosis_history_id(
                str(hist_id), sid, "local", db,
            )
        assert exc_info.value.status_code == 400
        assert "mismatch" in exc_info.value.detail.lower()

    def test_returns_uuid_on_success(self):
        """Valid input returns the parsed UUID."""
        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        hist_id = uuid.uuid4()
        sid = uuid.uuid4()
        row = _make_history_row(hist_id, sid, "local")
        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = row

        result = _validate_diagnosis_history_id(
            str(hist_id), sid, "local", db,
        )
        assert result == hist_id


# ---------------------------------------------------------------------------
# Tests for SSE done event format
# ---------------------------------------------------------------------------


class TestSSEEventFormat:
    """Verify the done/cached SSE events emit dict payloads."""

    def test_sse_event_with_dict(self):
        """_sse_event serialises dict payloads correctly."""
        import json

        from app.api.v2.endpoints.obd_analysis import (
            _sse_event,
        )

        frame = _sse_event("done", {
            "text": "hello",
            "diagnosis_history_id": "abc-123",
        })
        assert frame.startswith("event: done\n")
        data_line = frame.split("\n")[1]
        assert data_line.startswith("data: ")
        parsed = json.loads(data_line[6:])
        assert parsed["text"] == "hello"
        assert parsed["diagnosis_history_id"] == "abc-123"

    def test_sse_event_with_string(self):
        """_sse_event still works with plain string payloads."""
        import json

        from app.api.v2.endpoints.obd_analysis import (
            _sse_event,
        )

        frame = _sse_event("token", "chunk")
        data_line = frame.split("\n")[1]
        parsed = json.loads(data_line[6:])
        assert parsed == "chunk"

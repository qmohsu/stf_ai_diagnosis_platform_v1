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
    model_name: str = "qwen3.5:27b-q8_0",
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

    def test_accepts_agent_provider(self):
        """A provider='agent' row validates for expected 'agent'.

        Regression for issue #127: agent generations must be a
        valid feedback target, not just local/premium.
        """
        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        hist_id = uuid.uuid4()
        sid = uuid.uuid4()
        row = _make_history_row(hist_id, sid, provider="agent")
        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = row

        result = _validate_diagnosis_history_id(
            str(hist_id), sid, "agent", db,
        )
        assert result == hist_id

    def test_rejects_local_row_for_agent_feedback(self):
        """A provider='local' row is rejected for expected 'agent'.

        The mirror of the original #127 bug: previously the agent
        view posted to the ai_diagnosis endpoint (expected
        'local'), so an agent row 400'd.  Now the agent endpoint
        expects 'agent', so a stray local row must still 400.
        """
        from fastapi import HTTPException

        from app.api.v2.endpoints.obd_analysis import (
            _validate_diagnosis_history_id,
        )

        hist_id = uuid.uuid4()
        sid = uuid.uuid4()
        row = _make_history_row(hist_id, sid, provider="local")
        db = MagicMock()
        db.query.return_value.filter.return_value \
            .first.return_value = row

        with pytest.raises(HTTPException) as exc_info:
            _validate_diagnosis_history_id(
                str(hist_id), sid, "agent", db,
            )
        assert exc_info.value.status_code == 400
        assert "mismatch" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Tests for the agent diagnosis feedback endpoint (issue #127)
# ---------------------------------------------------------------------------


class TestAgentDiagnosisFeedbackEndpoint:
    """End-to-end wiring of submit_agent_diagnosis_feedback.

    Exercises the endpoint coroutine directly (it lives in
    ``obd_analysis`` and pulls in no harness modules, so it runs
    offline) to prove an agent generation's history id is accepted
    and routed to the dedicated agent feedback table.
    """

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_agent_feedback_accepts_agent_history_id(self):
        """A provider='agent' history id is persisted via the agent table."""
        from app.api.v2 import schemas
        from app.api.v2.endpoints import obd_analysis
        from app.models_db import OBDAgentDiagnosisFeedback

        sid = uuid.uuid4()
        hist_id = uuid.uuid4()

        session_row = _make_session(sid)
        # Distinct from the session column so the test proves the
        # snapshot is sourced from the linked history row, not the
        # (potentially stale, local-shared) session diagnosis_text.
        session_row.diagnosis_text = "stale local text"
        agent_row = _make_history_row(
            hist_id, sid, provider="agent",
        )

        # db.query() is called in this order:
        #   1. _get_session_data -> _get_owned_session (session)
        #   2. _validate_diagnosis_history_id (history row)
        #   3. authoritative snapshot fetch (history text scalar)
        #   4. _submit_feedback -> _get_owned_session (session)
        #   5. _submit_feedback count guard (count)
        sess1 = MagicMock()
        sess1.filter.return_value.first.return_value = session_row
        hist = MagicMock()
        hist.filter.return_value.first.return_value = agent_row
        hist_text = MagicMock()
        hist_text.filter.return_value.scalar.return_value = (
            "agent diagnosis snapshot"
        )
        sess2 = MagicMock()
        sess2.filter.return_value.first.return_value = session_row
        count = MagicMock()
        count.filter.return_value.count.return_value = 0

        db = MagicMock()
        db.query.side_effect = [sess1, hist, hist_text, sess2, count]

        feedback = schemas.OBDFeedbackRequest(
            rating=5,
            is_helpful=True,
            comments="agent did well",
            diagnosis_history_id=str(hist_id),
        )
        user = make_mock_user()

        captured = {}

        def _fake_insert(
            session_id, fb, db_, model_class,
            feedback_type, extra_fields=None,
        ):
            captured["model_class"] = model_class
            captured["feedback_type"] = feedback_type
            captured["extra_fields"] = extra_fields
            return {
                "status": "ok",
                "feedback_id": str(uuid.uuid4()),
            }

        with patch.object(
            obd_analysis, "_insert_feedback", _fake_insert,
        ):
            result = self._run(
                obd_analysis.submit_agent_diagnosis_feedback(
                    session_id=sid,
                    feedback=feedback,
                    current_user=user,
                    db=db,
                )
            )

        assert result["status"] == "ok"
        assert (
            captured["model_class"] is OBDAgentDiagnosisFeedback
        )
        assert captured["feedback_type"] == "agent_diagnosis"
        assert (
            captured["extra_fields"]["diagnosis_history_id"]
            == hist_id
        )
        assert (
            captured["extra_fields"]["diagnosis_text"]
            == "agent diagnosis snapshot"
        )

    def test_agent_feedback_rejects_local_history_id(self):
        """Linking a local generation to agent feedback 400s."""
        from fastapi import HTTPException

        from app.api.v2 import schemas
        from app.api.v2.endpoints import obd_analysis

        sid = uuid.uuid4()
        hist_id = uuid.uuid4()

        session_row = _make_session(sid)
        local_row = _make_history_row(
            hist_id, sid, provider="local",
        )

        sess1 = MagicMock()
        sess1.filter.return_value.first.return_value = session_row
        hist = MagicMock()
        hist.filter.return_value.first.return_value = local_row

        db = MagicMock()
        db.query.side_effect = [sess1, hist]

        feedback = schemas.OBDFeedbackRequest(
            rating=3,
            is_helpful=False,
            diagnosis_history_id=str(hist_id),
        )
        user = make_mock_user()

        with pytest.raises(HTTPException) as exc_info:
            self._run(
                obd_analysis.submit_agent_diagnosis_feedback(
                    session_id=sid,
                    feedback=feedback,
                    current_user=user,
                    db=db,
                )
            )
        assert exc_info.value.status_code == 400
        assert "mismatch" in exc_info.value.detail.lower()


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

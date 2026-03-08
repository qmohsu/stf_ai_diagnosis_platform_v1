"""Tests for per-user session isolation.

Covers:
  - Same file uploaded by different users → separate sessions
  - Same user re-uploading → returns existing session (dedup)
  - User A cannot access User B's session (_get_owned_session)
  - User A cannot submit feedback on User B's session
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MOCK_USER_ID, make_mock_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_A_ID = uuid.UUID("00000000-0000-0000-0000-00000000000a")
USER_B_ID = uuid.UUID("00000000-0000-0000-0000-00000000000b")

VALID_FEEDBACK = {
    "rating": 4,
    "is_helpful": True,
    "comments": "Good",
}


@pytest.fixture()
def client():
    """Create a TestClient that bypasses DB-dependent startup."""
    with patch("app.db.session.SessionLocal"), \
         patch("app.db.session.engine"):
        from app.main import app
        yield TestClient(app)


@pytest.fixture()
def app_ref():
    """Return the FastAPI app for dependency overrides."""
    from app.main import app
    return app


@pytest.fixture(autouse=True)
def clear_overrides(app_ref):
    """Clean up dependency overrides after each test."""
    yield
    app_ref.dependency_overrides.clear()


def _override_auth(app_ref, user_id: uuid.UUID, username: str):
    """Override get_current_user to return a specific mock user."""
    from app.auth.security import get_current_user

    mock_user = make_mock_user(
        user_id=user_id, username=username,
    )
    app_ref.dependency_overrides[get_current_user] = (
        lambda: mock_user
    )
    return mock_user


# ---------------------------------------------------------------------------
# _get_owned_session isolation
# ---------------------------------------------------------------------------


class TestSessionOwnership:
    """Tests for _get_owned_session: user can only access own sessions."""

    def test_owner_can_access_session(self, client, app_ref):
        """Session owner gets 200."""
        _override_auth(app_ref, USER_A_ID, "user_a")

        sid = uuid.uuid4()
        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.id = sid
        mock_row.status = "COMPLETED"
        mock_row.result_payload = None
        mock_row.error_message = None
        mock_row.parsed_summary_payload = None
        mock_row.diagnosis_text = None
        mock_row.premium_diagnosis_text = None
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_row

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.get(f"/v2/obd/{sid}")
        assert resp.status_code == 200

    def test_non_owner_gets_404(self, client, app_ref):
        """Non-owner gets 404 (not 403, to prevent enumeration)."""
        _override_auth(app_ref, USER_B_ID, "user_b")

        mock_db = MagicMock()
        # _get_owned_session filters by user_id — returns None
        mock_db.query.return_value.filter.return_value \
            .first.return_value = None

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        sid = uuid.uuid4()
        resp = client.get(f"/v2/obd/{sid}")
        assert resp.status_code == 404

    def test_non_owner_cannot_get_history(
        self, client, app_ref,
    ):
        """Non-owner cannot access diagnosis history."""
        _override_auth(app_ref, USER_B_ID, "user_b")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = None

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.get(
            f"/v2/obd/{uuid.uuid4()}/history",
        )
        assert resp.status_code == 404

    def test_non_owner_cannot_submit_feedback(
        self, client, app_ref,
    ):
        """Non-owner cannot submit feedback on another's session."""
        _override_auth(app_ref, USER_B_ID, "user_b")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = None

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/summary",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-user deduplication in analyze endpoint
# ---------------------------------------------------------------------------


class TestPerUserDedup:
    """Tests for per-user hash deduplication in analyze."""

    @patch(
        "app.api.v2.endpoints.obd_analysis."
        "format_summary_flat_strings",
    )
    @patch(
        "app.api.v2.endpoints.obd_analysis._run_pipeline",
    )
    def test_same_user_same_file_returns_existing(
        self, mock_pipeline, mock_format, client, app_ref,
    ):
        """Same user uploading same file returns cached session."""
        _override_auth(app_ref, USER_A_ID, "user_a")

        sid = uuid.uuid4()
        mock_db = MagicMock()

        # Dedup query returns existing session
        existing_row = MagicMock()
        existing_row.id = sid
        existing_row.status = "COMPLETED"
        existing_row.result_payload = {
            "vehicle_id": "V-1",
            "time_range": {
                "start": "2025-01-01T00:00:00",
                "end": "2025-01-01T00:01:00",
                "duration_seconds": 60,
                "sample_count": 60,
            },
            "dtc_codes": [],
            "pid_summary": {},
            "value_statistics": {
                "stats": {},
                "column_units": {},
                "resample_interval_seconds": 1.0,
            },
            "anomaly_events": [],
            "diagnostic_clues": [],
            "clue_details": [],
        }
        existing_row.parsed_summary_payload = {"parse_ok": "YES"}
        existing_row.diagnosis_text = None
        existing_row.premium_diagnosis_text = None
        mock_db.query.return_value.filter.return_value \
            .first.return_value = existing_row

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/v2/obd/analyze", content=b"test data\n",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == str(sid)

        # Pipeline should NOT have been called (dedup hit)
        mock_pipeline.assert_not_called()

    @patch(
        "app.api.v2.endpoints.obd_analysis."
        "format_summary_flat_strings",
    )
    @patch(
        "app.api.v2.endpoints.obd_analysis._run_pipeline",
    )
    def test_different_user_same_file_creates_new(
        self, mock_pipeline, mock_format, client, app_ref,
    ):
        """Different user uploading same file creates new session."""
        from app.api.v2.schemas import LogSummaryV2

        _override_auth(app_ref, USER_B_ID, "user_b")

        mock_db = MagicMock()
        # Dedup query returns None (no existing session for
        # this user)
        mock_db.query.return_value.filter.return_value \
            .first.return_value = None

        result_payload = {
            "vehicle_id": "V-1",
            "time_range": {
                "start": "2025-01-01T00:00:00",
                "end": "2025-01-01T00:01:00",
                "duration_seconds": 60,
                "sample_count": 60,
            },
            "dtc_codes": [],
            "pid_summary": {},
            "value_statistics": {
                "stats": {},
                "column_units": {},
                "resample_interval_seconds": 1.0,
            },
            "anomaly_events": [],
            "diagnostic_clues": [],
            "clue_details": [],
        }
        mock_pipeline.return_value = LogSummaryV2(
            **result_payload,
        )
        mock_format.return_value = {"parse_ok": "YES"}

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/v2/obd/analyze", content=b"test data\n",
        )
        assert resp.status_code == 200

        # Pipeline WAS called (new session for this user)
        mock_pipeline.assert_called_once()
        # DB row was added
        mock_db.add.assert_called_once()


# ---------------------------------------------------------------------------
# UniqueConstraint: user_id + input_text_hash
# ---------------------------------------------------------------------------


class TestUniqueConstraint:
    """Tests for the DB-level unique constraint on (user_id, input_text_hash)."""

    def test_model_has_unique_constraint(self):
        """OBDAnalysisSession has UniqueConstraint on user_id + input_text_hash."""
        from app.models_db import OBDAnalysisSession

        table_args = OBDAnalysisSession.__table_args__
        found = False
        for arg in table_args:
            if isinstance(arg, UniqueConstraint):
                cols = [c.name for c in arg.columns]
                if (
                    "user_id" in cols
                    and "input_text_hash" in cols
                ):
                    found = True
                    break
        assert found, (
            "Expected UniqueConstraint on "
            "(user_id, input_text_hash)"
        )


# Import UniqueConstraint for the model test
from sqlalchemy import UniqueConstraint

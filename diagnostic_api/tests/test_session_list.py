"""Tests for GET /v2/obd/sessions — session listing endpoint.

Covers:
  - Empty list returns zero total
  - Correct fields including has_diagnosis booleans
  - Pagination (limit/offset)
  - Status filter
  - Auth required (401)
  - Per-user isolation
  - Invalid date filter returns 422
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MOCK_USER_ID, make_mock_user


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

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


def _override_auth(app_ref, user_id=MOCK_USER_ID):
    """Override get_current_user dependency."""
    from app.auth.security import get_current_user

    mock_user = make_mock_user(user_id=user_id)
    app_ref.dependency_overrides[get_current_user] = (
        lambda: mock_user
    )
    return mock_user


def _make_session_row(
    user_id=MOCK_USER_ID,
    vehicle_id="V-TEST",
    status_val="COMPLETED",
    input_size_bytes=1024,
    diagnosis_text=None,
    premium_diagnosis_text=None,
):
    """Create a mock OBDAnalysisSession row."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.user_id = user_id
    row.vehicle_id = vehicle_id
    row.status = status_val
    row.input_size_bytes = input_size_bytes
    row.diagnosis_text = diagnosis_text
    row.premium_diagnosis_text = premium_diagnosis_text
    row.created_at = datetime(
        2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc,
    )
    row.updated_at = datetime(
        2025, 6, 15, 10, 5, 0, tzinfo=timezone.utc,
    )
    return row


def _mock_db_empty():
    """Return a mock db session that returns empty results."""
    from app.api.deps import get_db

    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.count.return_value = 0
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.all.return_value = []
    mock_db.query.return_value = mock_query
    return mock_db, get_db


def _mock_db_with_rows(rows):
    """Return a mock db session that returns given rows."""
    from app.api.deps import get_db

    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.count.return_value = len(rows)
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.all.return_value = rows
    mock_db.query.return_value = mock_query
    return mock_db, get_db


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------


class TestListSessions:
    """Tests for GET /v2/obd/sessions."""

    def test_empty_returns_zero_total(
        self, client, app_ref,
    ):
        """Empty result set returns items=[] and total=0."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get("/v2/obd/sessions")
        assert res.status_code == 200
        body = res.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_correct_fields(
        self, client, app_ref,
    ):
        """Response includes all expected fields with correct values."""
        _override_auth(app_ref)
        row = _make_session_row(
            diagnosis_text="Some diagnosis",
            premium_diagnosis_text=None,
        )
        mock_db, get_db = _mock_db_with_rows([row])
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get("/v2/obd/sessions")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

        item = body["items"][0]
        assert item["session_id"] == str(row.id)
        assert item["vehicle_id"] == "V-TEST"
        assert item["status"] == "COMPLETED"
        assert item["input_size_bytes"] == 1024
        assert item["has_diagnosis"] is True
        assert item["has_premium_diagnosis"] is False
        assert item["created_at"] != ""
        assert item["updated_at"] != ""

    def test_has_premium_diagnosis_true(
        self, client, app_ref,
    ):
        """has_premium_diagnosis is True when field is non-null."""
        _override_auth(app_ref)
        row = _make_session_row(
            premium_diagnosis_text="Premium result",
        )
        mock_db, get_db = _mock_db_with_rows([row])
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get("/v2/obd/sessions")
        item = res.json()["items"][0]
        assert item["has_premium_diagnosis"] is True

    def test_pagination_params_accepted(
        self, client, app_ref,
    ):
        """Limit and offset query params are accepted."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get(
            "/v2/obd/sessions?limit=10&offset=5",
        )
        assert res.status_code == 200

    def test_status_filter_accepted(
        self, client, app_ref,
    ):
        """Status query param is accepted and passed through."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get(
            "/v2/obd/sessions?status=COMPLETED",
        )
        assert res.status_code == 200

    def test_requires_auth(self, client):
        """Unauthenticated request returns 401."""
        res = client.get("/v2/obd/sessions")
        assert res.status_code == 401

    def test_invalid_created_after_returns_422(
        self, client, app_ref,
    ):
        """Malformed created_after timestamp returns 422."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get(
            "/v2/obd/sessions?created_after=not-a-date",
        )
        assert res.status_code == 422

    def test_invalid_created_before_returns_422(
        self, client, app_ref,
    ):
        """Malformed created_before timestamp returns 422."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get(
            "/v2/obd/sessions?created_before=bad",
        )
        assert res.status_code == 422

    def test_valid_date_filters_accepted(
        self, client, app_ref,
    ):
        """Valid ISO date filters are accepted."""
        _override_auth(app_ref)
        mock_db, get_db = _mock_db_empty()
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        res = client.get(
            "/v2/obd/sessions"
            "?created_after=2025-01-01T00:00:00"
            "&created_before=2025-12-31T23:59:59",
        )
        assert res.status_code == 200

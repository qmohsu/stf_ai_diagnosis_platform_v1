"""Tests for authentication endpoints (register and login).

Covers:
  - POST /auth/register (success, duplicate, validation errors)
  - POST /auth/login (success, wrong password, unknown user)
  - JWT token decode and get_current_user dependency
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


class TestRegister:
    """Tests for the registration endpoint."""

    def test_register_success(self, client, app_ref):
        """Successful registration returns 201."""
        mock_db = MagicMock()

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/register",
            json={
                "username": "newuser",
                "password": "securepass123",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "newuser"
        assert "message" in body

        # Verify user was added to DB
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_register_duplicate_returns_409(self, client, app_ref):
        """Duplicate username returns 409."""
        from sqlalchemy.exc import IntegrityError

        mock_db = MagicMock()
        mock_db.commit.side_effect = IntegrityError(
            "UNIQUE", {}, Exception(),
        )

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/register",
            json={
                "username": "existing",
                "password": "securepass123",
            },
        )
        assert resp.status_code == 409
        assert "exists" in resp.json()["detail"].lower()

    def test_register_short_username_returns_422(self, client):
        """Username shorter than 3 chars returns 422."""
        resp = client.post(
            "/auth/register",
            json={"username": "ab", "password": "securepass123"},
        )
        assert resp.status_code == 422

    def test_register_long_username_returns_422(self, client):
        """Username longer than 50 chars returns 422."""
        resp = client.post(
            "/auth/register",
            json={
                "username": "x" * 51,
                "password": "securepass123",
            },
        )
        assert resp.status_code == 422

    def test_register_invalid_chars_returns_422(self, client):
        """Username with special chars returns 422."""
        resp = client.post(
            "/auth/register",
            json={
                "username": "user@name",
                "password": "securepass123",
            },
        )
        assert resp.status_code == 422

    def test_register_short_password_returns_422(self, client):
        """Password shorter than 8 chars returns 422."""
        resp = client.post(
            "/auth/register",
            json={"username": "validuser", "password": "short"},
        )
        assert resp.status_code == 422

    def test_register_long_password_returns_422(self, client):
        """Password longer than 128 chars returns 422."""
        resp = client.post(
            "/auth/register",
            json={
                "username": "validuser",
                "password": "x" * 129,
            },
        )
        assert resp.status_code == 422

    def test_register_valid_username_formats(self, client, app_ref):
        """Usernames with underscores and hyphens are accepted."""
        mock_db = MagicMock()

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        for name in ["user_name", "user-name", "User123"]:
            mock_db.reset_mock()
            resp = client.post(
                "/auth/register",
                json={
                    "username": name,
                    "password": "securepass123",
                },
            )
            assert resp.status_code == 201, (
                f"Username '{name}' should be valid"
            )


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    """Tests for the login endpoint."""

    def test_login_success(self, client, app_ref):
        """Valid credentials return JWT token."""
        from app.auth.security import get_password_hash

        mock_user = MagicMock()
        mock_user.username = "testuser"
        mock_user.hashed_password = get_password_hash(
            "securepass123",
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_user

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/login",
            data={
                "username": "testuser",
                "password": "securepass123",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(
        self, client, app_ref,
    ):
        """Wrong password returns 401."""
        from app.auth.security import get_password_hash

        mock_user = MagicMock()
        mock_user.username = "testuser"
        mock_user.hashed_password = get_password_hash(
            "correctpass",
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_user

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/login",
            data={
                "username": "testuser",
                "password": "wrongpass123",
            },
        )
        assert resp.status_code == 401

    def test_login_unknown_user_returns_401(
        self, client, app_ref,
    ):
        """Unknown username returns 401."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = None

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/login",
            data={
                "username": "noexist",
                "password": "somepass123",
            },
        )
        assert resp.status_code == 401

    def test_login_returns_valid_jwt(self, client, app_ref):
        """Returned JWT decodes to the correct username."""
        from app.auth.security import get_password_hash
        from jose import jwt
        from app.config import settings

        mock_user = MagicMock()
        mock_user.username = "jwtuser"
        mock_user.hashed_password = get_password_hash(
            "securepass123",
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_user

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            "/auth/login",
            data={
                "username": "jwtuser",
                "password": "securepass123",
            },
        )
        token = resp.json()["access_token"]
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert payload["sub"] == "jwtuser"
        assert "exp" in payload


# ---------------------------------------------------------------------------
# Protected endpoints — auth required
# ---------------------------------------------------------------------------


class TestProtectedEndpoints:
    """Tests that endpoints return 401 without auth."""

    def test_analyze_requires_auth(self, client):
        """POST /v2/obd/analyze returns 401 without token."""
        resp = client.post(
            "/v2/obd/analyze", content=b"data",
        )
        assert resp.status_code == 401

    def test_get_session_requires_auth(self, client):
        """GET /v2/obd/{session_id} returns 401 without token."""
        resp = client.get(f"/v2/obd/{uuid.uuid4()}")
        assert resp.status_code == 401

    def test_feedback_requires_auth(self, client):
        """POST /v2/obd/{sid}/feedback/summary returns 401."""
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/summary",
            json={"rating": 4, "is_helpful": True},
        )
        assert resp.status_code == 401

    def test_diagnose_requires_auth(self, client):
        """POST /v2/obd/{sid}/diagnose returns 401."""
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/diagnose",
        )
        assert resp.status_code == 401

    def test_history_requires_auth(self, client):
        """GET /v2/obd/{sid}/history returns 401."""
        resp = client.get(
            f"/v2/obd/{uuid.uuid4()}/history",
        )
        assert resp.status_code == 401

    def test_health_does_not_require_auth(self, client):
        """GET /health is public — no auth needed."""
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# get_current_user dependency — unit tests
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    """Tests for the get_current_user dependency logic."""

    def test_invalid_token_returns_401(self, client):
        """Malformed JWT returns 401."""
        resp = client.get(
            f"/v2/obd/{uuid.uuid4()}",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp.status_code == 401

    def test_expired_token_returns_401(self, client, app_ref):
        """Expired JWT returns 401."""
        from jose import jwt
        from datetime import datetime, timedelta, timezone
        from app.config import settings

        expired_payload = {
            "sub": "testuser",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = jwt.encode(
            expired_payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        resp = client.get(
            f"/v2/obd/{uuid.uuid4()}",
            headers={
                "Authorization": f"Bearer {expired_token}",
            },
        )
        assert resp.status_code == 401

    def test_inactive_user_returns_401(self, client, app_ref):
        """Token for inactive user returns 401."""
        from app.auth.security import create_access_token

        token = create_access_token(data={"sub": "inactive"})

        mock_user = MagicMock()
        mock_user.is_active = False

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_user

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.get(
            f"/v2/obd/{uuid.uuid4()}",
            headers={
                "Authorization": f"Bearer {token}",
            },
        )
        assert resp.status_code == 401

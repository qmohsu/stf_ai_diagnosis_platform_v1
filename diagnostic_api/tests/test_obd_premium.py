"""Tests for premium AI diagnosis endpoints.

Covers:
  - GET  /v2/obd/premium/models (200, 403)
  - POST /v2/obd/{session_id}/diagnose/premium (403, 503, 422, caching)
  - POST /v2/obd/{session_id}/feedback/premium_diagnosis (404, snapshot)
  - Per-session regeneration rate limit (429)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

FAKE_PARSED_SUMMARY = {
    "parse_ok": "YES",
    "vehicle_id": "V-TEST",
    "time_range": "2025-01-01T00:00:00 to 2025-01-01T00:01:00",
    "dtc_codes": "P0420",
    "pid_summary": "RPM: 700-3000 rpm",
    "anomaly_events": "none",
    "diagnostic_clues": "Catalyst efficiency below threshold",
    "rag_query": "catalyst efficiency P0420",
    "debug": "",
}

VALID_FEEDBACK = {
    "rating": 4,
    "is_helpful": True,
    "comments": "Great premium diagnosis",
}


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
    """Set up auth override and clean up after each test."""
    from app.auth.security import get_current_user
    from tests.conftest import make_mock_user

    mock_user = make_mock_user()
    app_ref.dependency_overrides[get_current_user] = (
        lambda: mock_user
    )
    yield
    app_ref.dependency_overrides.clear()


def _mock_db_none():
    """Return a mock DB where query().filter().first() returns None."""
    mock = MagicMock()
    mock.query.return_value.filter.return_value.first.return_value = None
    return mock


# ---------------------------------------------------------------------------
# GET /v2/obd/premium/models — model listing
# ---------------------------------------------------------------------------


class TestListPremiumModels:
    """Tests for the premium model listing endpoint."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_returns_models_when_enabled(
        self, mock_settings, client,
    ):
        """Returns curated model list when premium is enabled."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

        resp = client.get("/v2/obd/premium/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default"] == "anthropic/claude-sonnet-4.6"
        assert "openai/gpt-5.2" in body["models"]
        assert len(body["models"]) == 2

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_returns_403_when_disabled(
        self, mock_settings, client,
    ):
        """Returns 403 when premium feature is disabled."""
        mock_settings.premium_llm_enabled = False

        resp = client.get("/v2/obd/premium/models")
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/diagnose/premium — feature gate
# ---------------------------------------------------------------------------


class TestPremiumFeatureGate:
    """Tests for the premium feature flag and API key checks."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_returns_403_when_disabled(
        self, mock_settings, client,
    ):
        """Premium endpoint returns 403 when feature is disabled."""
        mock_settings.premium_llm_enabled = False
        sid = uuid.uuid4()
        resp = client.post(f"/v2/obd/{sid}/diagnose/premium")
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_returns_503_when_no_api_key(
        self, mock_settings, client, app_ref,
    ):
        """Premium endpoint returns 503 when API key is empty."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = ""
        sid = uuid.uuid4()
        resp = client.post(f"/v2/obd/{sid}/diagnose/premium")
        assert resp.status_code == 503
        assert "api key" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/diagnose/premium — caching
# ---------------------------------------------------------------------------


class TestPremiumDiagnosisCaching:
    """Tests for cached premium diagnosis responses."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_returns_cached_when_existing(
        self, mock_get_data, mock_settings, client,
    ):
        """When premium_diagnosis_text exists, return cached SSE event."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
        ]

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text="local diag",
            premium_diagnosis_text="cached premium text",
        )

        sid = uuid.uuid4()
        resp = client.post(f"/v2/obd/{sid}/diagnose/premium")
        assert resp.status_code == 200
        assert "event: cached" in resp.text
        assert "cached premium text" in resp.text

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_returns_422_when_no_parsed_summary(
        self, mock_get_data, mock_settings, client,
    ):
        """Returns 422 when session has no parsed summary."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
        ]

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=None,
            diagnosis_text=None,
            premium_diagnosis_text=None,
        )

        sid = uuid.uuid4()
        resp = client.post(f"/v2/obd/{sid}/diagnose/premium")
        assert resp.status_code == 422
        assert "parsed summary" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/diagnose/premium — model parameter
# ---------------------------------------------------------------------------


class TestPremiumModelParam:
    """Tests for model override via query parameter."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_accepts_curated_model_query_param(
        self, mock_get_data, mock_settings, client,
    ):
        """Curated model query parameter is accepted."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=None,
            diagnosis_text=None,
            premium_diagnosis_text=None,
        )

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium"
            "?model=openai/gpt-5.2",
        )
        # Should fail with 422 (no parsed summary), not 400
        # for an invalid model
        assert resp.status_code == 422
        assert "parsed summary" in resp.json()["detail"].lower()

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_rejects_uncurated_model(
        self, mock_settings, client,
    ):
        """Model not in curated list returns 400."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium"
            "?model=evil/expensive-model",
        )
        assert resp.status_code == 400
        assert "curated list" in resp.json()["detail"].lower()

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_default_model_must_be_in_curated_list(
        self, mock_settings, client,
    ):
        """Default model is validated against curated list."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "unknown/bad-default"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
        ]

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium",
        )
        assert resp.status_code == 400
        assert "curated list" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/diagnose/premium — rate limit
# ---------------------------------------------------------------------------


class TestPremiumRateLimit:
    """Tests for per-session premium regeneration cap."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_returns_429_when_regen_cap_reached(
        self, mock_get_data, mock_settings, client, app_ref,
    ):
        """Force-regeneration blocked after cap is reached."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
        ]

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text="existing premium text",
        )

        # Mock DB to return count >= _MAX_PREMIUM_REGENERATIONS
        mock_db = MagicMock()
        count_chain = MagicMock()
        count_chain.filter.return_value.count.return_value = 3
        mock_db.query.return_value = count_chain

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium?force=true",
        )
        assert resp.status_code == 429
        assert "re-generation" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/feedback/premium_diagnosis
# ---------------------------------------------------------------------------


class TestPremiumFeedback:
    """Tests for premium diagnosis feedback endpoint."""

    def test_feedback_unknown_session_returns_404(
        self, client, app_ref,
    ):
        """Returns 404 when session does not exist."""
        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = _mock_db_none
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/premium_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 404

    @patch(
        "app.api.v2.endpoints.obd_premium._submit_feedback",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_feedback_snapshots_premium_diagnosis_text(
        self, mock_get_data, mock_submit, client,
    ):
        """Feedback includes a snapshot of premium_diagnosis_text."""
        sid = uuid.uuid4()
        premium_text = "Premium Claude analysis result"

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text=premium_text,
        )
        mock_submit.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        resp = client.post(
            f"/v2/obd/{sid}/feedback/premium_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "ok"

        # Verify _submit_feedback was called with diagnosis_text
        # in extra_fields (6th positional arg)
        call_args = mock_submit.call_args
        extra_fields = call_args[1].get(
            "extra_fields",
            call_args[0][5] if len(call_args[0]) > 5 else None,
        )
        assert extra_fields is not None
        assert extra_fields["diagnosis_text"] == premium_text

    @patch(
        "app.api.v2.endpoints.obd_premium._submit_feedback",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    def test_feedback_truncates_long_diagnosis(
        self, mock_get_data, mock_submit, client,
    ):
        """Long premium diagnosis text is truncated in snapshot."""
        sid = uuid.uuid4()
        long_text = "x" * 60_000

        from app.api.v2.endpoints.obd_analysis import SessionData
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text=long_text,
        )
        mock_submit.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        resp = client.post(
            f"/v2/obd/{sid}/feedback/premium_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201

        call_args = mock_submit.call_args
        extra_fields = call_args[1].get(
            "extra_fields",
            call_args[0][5] if len(call_args[0]) > 5 else None,
        )
        assert extra_fields is not None
        assert len(extra_fields["diagnosis_text"]) == 50_000

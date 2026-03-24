"""Tests for premium AI diagnosis endpoints.

Covers:
  - GET  /v2/obd/premium/models (200, 403, availability filter)
  - POST /v2/obd/{session_id}/diagnose/premium (403, 503, 422,
    caching, region-blocked fallback)
  - POST /v2/obd/{session_id}/feedback/premium_diagnosis (404,
    snapshot)
  - Per-session regeneration rate limit (429)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import openai
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
        self, mock_get_data, mock_settings, client, app_ref,
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

        # The cached path now queries DiagnosisHistory — mock get_db.
        mock_db = MagicMock()
        mock_hist = MagicMock()
        mock_hist.id = uuid.uuid4()
        mock_db.query.return_value.filter.return_value \
            .order_by.return_value.first.return_value = mock_hist
        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

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


# ---------------------------------------------------------------------------
# Region-block handling (403 fallback)
# ---------------------------------------------------------------------------


def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE event frames from a response body string.

    Returns:
        List of dicts with ``event`` and ``data`` keys.
    """
    events = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame or frame.startswith(":"):
            continue
        event = ""
        data = ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append({"event": event, "data": data})
    return events


class TestRegionBlockHandling:
    """Tests for 403 region-block detection and fallback."""

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.retrieve_context",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_premium_client",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._store_diagnosis",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.get_available_models",
    )
    def test_403_triggers_fallback_to_next_model(
        self,
        mock_avail,
        mock_store,
        mock_client_fn,
        mock_retrieve,
        mock_get_data,
        mock_settings,
        client,
    ):
        """When first model returns 403, falls back to next."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]

        from app.api.v2.endpoints.obd_analysis import (
            SessionData,
        )
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text=None,
        )
        mock_retrieve.return_value = []
        mock_store.return_value = uuid.uuid4()
        mock_avail.return_value = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]

        # First call raises 403, second succeeds
        call_count = 0

        async def _fake_stream(
            ps, ctx, model_override=None, locale="en",
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise openai.PermissionDeniedError(
                    message="region blocked",
                    response=AsyncMock(status_code=403),
                    body=None,
                )
            yield "OK"

        mock_client = MagicMock()
        mock_client.generate_obd_diagnosis_stream = _fake_stream
        mock_client_fn.return_value = mock_client

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium",
        )
        assert resp.status_code == 200

        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]

        # Should have: status (connecting first), status (blocked),
        # status (connecting second), done
        assert "done" in event_types
        assert call_count == 2

        # Verify the done event includes model_used
        done_evt = next(
            e for e in events if e["event"] == "done"
        )
        assert done_evt["data"]["model_used"] == (
            "deepseek/deepseek-v3.2"
        )

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.retrieve_context",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_premium_client",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.get_available_models",
    )
    def test_all_models_blocked_returns_error(
        self,
        mock_avail,
        mock_client_fn,
        mock_retrieve,
        mock_get_data,
        mock_settings,
        client,
    ):
        """When all fallback models return 403, returns error."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

        from app.api.v2.endpoints.obd_analysis import (
            SessionData,
        )
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text=None,
        )
        mock_retrieve.return_value = []
        mock_avail.return_value = [
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

        # All calls raise 403
        async def _always_403(
            ps, ctx, model_override=None, locale="en",
        ):
            raise openai.PermissionDeniedError(
                message="region blocked",
                response=AsyncMock(status_code=403),
                body=None,
            )
            yield  # pragma: no cover — makes it a generator

        mock_client = MagicMock()
        mock_client.generate_obd_diagnosis_stream = _always_403
        mock_client_fn.return_value = mock_client

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium",
        )
        assert resp.status_code == 200

        events = _parse_sse_events(resp.text)
        error_events = [
            e for e in events if e["event"] == "error"
        ]
        assert len(error_events) == 1
        assert error_events[0]["data"]["error_code"] == (
            "all_models_blocked"
        )

    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_session_data",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.retrieve_context",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium._get_premium_client",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.get_available_models",
    )
    def test_non_403_error_does_not_trigger_fallback(
        self,
        mock_avail,
        mock_client_fn,
        mock_retrieve,
        mock_get_data,
        mock_settings,
        client,
    ):
        """Non-403 errors stop immediately, no fallback."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_model = "anthropic/claude-sonnet-4.6"
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]

        from app.api.v2.endpoints.obd_analysis import (
            SessionData,
        )
        mock_get_data.return_value = SessionData(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
            premium_diagnosis_text=None,
        )
        mock_retrieve.return_value = []
        mock_avail.return_value = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]

        call_count = 0

        async def _generic_error(
            ps, ctx, model_override=None, locale="en",
        ):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("connection timeout")
            yield  # pragma: no cover

        mock_client = MagicMock()
        mock_client.generate_obd_diagnosis_stream = (
            _generic_error
        )
        mock_client_fn.return_value = mock_client

        sid = uuid.uuid4()
        resp = client.post(
            f"/v2/obd/{sid}/diagnose/premium",
        )
        assert resp.status_code == 200

        events = _parse_sse_events(resp.text)
        error_events = [
            e for e in events if e["event"] == "error"
        ]
        assert len(error_events) == 1
        assert error_events[0]["data"]["error_code"] == (
            "stream_error"
        )
        # Only ONE attempt — no fallback on non-403
        assert call_count == 1


# ---------------------------------------------------------------------------
# GET /v2/obd/premium/models — availability filtering
# ---------------------------------------------------------------------------


class TestPremiumModelsAvailability:
    """Tests for model listing with availability filtering."""

    @patch(
        "app.api.v2.endpoints.obd_premium.refresh_availability",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.is_cache_stale",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.get_blocked_models",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.get_available_models",
    )
    @patch(
        "app.api.v2.endpoints.obd_premium.settings",
    )
    def test_returns_filtered_models_with_blocked(
        self,
        mock_settings,
        mock_avail,
        mock_blocked,
        mock_stale,
        mock_refresh,
        client,
    ):
        """Endpoint returns available and blocked model lists."""
        mock_settings.premium_llm_enabled = True
        mock_settings.premium_llm_api_key = "sk-test"
        mock_settings.premium_llm_base_url = (
            "https://openrouter.ai/api/v1"
        )
        mock_settings.premium_llm_model = (
            "anthropic/claude-sonnet-4.6"
        )
        mock_settings.premium_llm_model_list = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]
        mock_stale.return_value = False
        mock_avail.return_value = ["deepseek/deepseek-v3.2"]
        mock_blocked.return_value = [
            "anthropic/claude-sonnet-4.6"
        ]

        resp = client.get("/v2/obd/premium/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["models"] == ["deepseek/deepseek-v3.2"]
        assert body["blocked"] == [
            "anthropic/claude-sonnet-4.6"
        ]
        # Default falls back since original default is blocked
        assert body["default"] == "deepseek/deepseek-v3.2"

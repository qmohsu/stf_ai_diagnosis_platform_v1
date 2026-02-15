"""Tests for OBD analysis endpoints (analyze, retrieve, feedback).

Covers:
  - POST /v2/obd/analyze (empty body, oversized, pipeline error, success)
  - GET  /v2/obd/{session_id} (cache hit, DB fallback, 404)
  - POST /v2/obd/{session_id}/feedback/{tab} (all 4 tabs, 404, 429 cap)
  - POST /v2/obd/{session_id}/feedback/ai_diagnosis (diagnosis text snapshot)
  - raw_input_text excluded from API responses (C-1 regression)
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.cache import CachedSession, obd_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_RESULT_PAYLOAD = {
    "vehicle_id": "V-TEST",
    "time_range": {
        "start": "2025-01-01T00:00:00",
        "end": "2025-01-01T00:01:00",
        "duration_seconds": 60,
        "sample_count": 60,
    },
    "dtc_codes": ["P0420"],
    "pid_summary": {
        "RPM": {"min": 700, "max": 3000, "mean": 1500.0, "latest": 800, "unit": "rpm"},
    },
    "value_statistics": {
        "stats": {
            "RPM": {"mean": 1500.0, "std": 500.0, "min": 700, "max": 3000, "valid_count": 60},
        },
        "column_units": {"RPM": "rpm"},
        "resample_interval_seconds": 1.0,
    },
    "anomaly_events": [],
    "diagnostic_clues": ["Catalyst efficiency below threshold"],
    "clue_details": [
        {
            "rule_id": "CAT_EFF_LOW",
            "category": "emissions",
            "clue": "Catalyst efficiency below threshold",
            "evidence": ["P0420 present"],
            "severity": "medium",
        },
    ],
}

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
    "comments": "Good analysis",
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
def clear_cache():
    """Ensure the in-memory cache is empty before each test."""
    obd_cache.clear()
    yield
    obd_cache.clear()


@pytest.fixture(autouse=True)
def clear_overrides(app_ref):
    """Ensure dependency overrides are cleaned up after each test."""
    yield
    app_ref.dependency_overrides.clear()


def _make_cached_session(session_id: str | None = None) -> CachedSession:
    """Create a CachedSession with realistic test data."""
    return CachedSession(
        session_id=session_id or str(uuid.uuid4()),
        status="COMPLETED",
        vehicle_id="V-TEST",
        input_text_hash="a" * 64,
        input_size_bytes=100,
        raw_input_text="fake log data",
        result_payload=FAKE_RESULT_PAYLOAD,
        parsed_summary_payload=FAKE_PARSED_SUMMARY,
        error_message=None,
    )


def _mock_db_none():
    """Return a mock DB where query().filter().first() returns None."""
    mock = MagicMock()
    mock.query.return_value.filter.return_value.first.return_value = None
    return mock


# ---------------------------------------------------------------------------
# POST /v2/obd/analyze
# ---------------------------------------------------------------------------


class TestAnalyzeEndpoint:
    """Tests for the analyze OBD log endpoint."""

    def test_empty_body_returns_422(self, client):
        resp = client.post("/v2/obd/analyze", content=b"")
        assert resp.status_code == 422

    @patch("app.api.v2.endpoints.obd_analysis._MAX_FILE_SIZE", 1024)
    def test_oversized_body_returns_413(self, client):
        resp = client.post("/v2/obd/analyze", content=b"x" * 1025)
        assert resp.status_code == 413
        assert "limit" in resp.json()["detail"].lower()

    @patch(
        "app.api.v2.endpoints.obd_analysis._run_pipeline",
        side_effect=ValueError("bad data"),
    )
    def test_pipeline_error_returns_422(self, mock_pipeline, client):
        resp = client.post("/v2/obd/analyze", content=b"corrupt\tdata\n")
        assert resp.status_code == 422
        assert "Failed to parse" in resp.json()["detail"]

    @patch("app.api.v2.endpoints.obd_analysis.format_summary_for_dify")
    @patch("app.api.v2.endpoints.obd_analysis._run_pipeline")
    def test_successful_analyze_returns_session(
        self, mock_pipeline, mock_format, client,
    ):
        from app.api.v2.schemas import LogSummaryV2

        mock_pipeline.return_value = LogSummaryV2(**FAKE_RESULT_PAYLOAD)
        mock_format.return_value = FAKE_PARSED_SUMMARY

        resp = client.post("/v2/obd/analyze", content=b"valid log data\n")
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "COMPLETED"
        assert body["session_id"]
        assert body["result"] is not None
        assert body["parsed_summary"] is not None

    @patch("app.api.v2.endpoints.obd_analysis.format_summary_for_dify")
    @patch("app.api.v2.endpoints.obd_analysis._run_pipeline")
    def test_response_excludes_raw_input_text(
        self, mock_pipeline, mock_format, client,
    ):
        """Regression: raw_input_text must NOT appear in the response (C-1)."""
        from app.api.v2.schemas import LogSummaryV2

        mock_pipeline.return_value = LogSummaryV2(**FAKE_RESULT_PAYLOAD)
        mock_format.return_value = FAKE_PARSED_SUMMARY

        resp = client.post("/v2/obd/analyze", content=b"valid log data\n")
        assert resp.status_code == 200
        assert "raw_input_text" not in resp.json()


# ---------------------------------------------------------------------------
# GET /v2/obd/{session_id}
# ---------------------------------------------------------------------------


class TestGetSessionEndpoint:
    """Tests for the session retrieval endpoint."""

    def test_cache_hit_returns_session(self, client):
        sid = str(uuid.uuid4())
        cached = _make_cached_session(sid)
        obd_cache.put(cached)

        resp = client.get(f"/v2/obd/{sid}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["session_id"] == sid
        assert body["status"] == "COMPLETED"
        assert body["result"]["vehicle_id"] == "V-TEST"
        assert body["parsed_summary"] is not None

    def test_cache_hit_excludes_raw_input_text(self, client):
        """Regression: raw_input_text must NOT appear in cache-hit response."""
        sid = str(uuid.uuid4())
        obd_cache.put(_make_cached_session(sid))

        resp = client.get(f"/v2/obd/{sid}")
        assert resp.status_code == 200
        assert "raw_input_text" not in resp.json()

    def test_unknown_session_returns_404(self, client, app_ref):
        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = _mock_db_none
        resp = client.get(f"/v2/obd/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_invalid_uuid_returns_422(self, client):
        resp = client.get("/v2/obd/not-a-uuid")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /v2/obd/{session_id}/feedback/{tab}
# ---------------------------------------------------------------------------


class TestFeedbackEndpoints:
    """Tests for all three feedback tab endpoints."""

    @pytest.mark.parametrize("tab", ["summary", "detailed", "rag", "ai_diagnosis"])
    def test_feedback_unknown_session_returns_404(self, tab, client, app_ref):
        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = _mock_db_none
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/{tab}",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize("tab", ["summary", "detailed", "rag"])
    @patch("app.api.v2.endpoints.obd_analysis._insert_feedback")
    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_success_all_tabs(
        self, mock_ensure, mock_insert, tab, client, app_ref,
    ):
        """Each tab endpoint calls _submit_feedback which promotes + inserts."""
        sid = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = (sid,)
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        mock_insert.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/{tab}",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "ok"
        assert "feedback_id" in body

    def test_feedback_invalid_rating_too_low(self, client):
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/summary",
            json={"rating": 0, "is_helpful": True},
        )
        assert resp.status_code == 422

    def test_feedback_invalid_rating_too_high(self, client):
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/summary",
            json={"rating": 6, "is_helpful": True},
        )
        assert resp.status_code == 422

    def test_feedback_missing_is_helpful(self, client):
        resp = client.post(
            f"/v2/obd/{uuid.uuid4()}/feedback/summary",
            json={"rating": 3},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Feedback rate-limiting (H-2)
# ---------------------------------------------------------------------------


class TestFeedbackCap:
    """Tests for the per-session feedback submission cap."""

    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_cap_returns_429(self, mock_ensure, client, app_ref):
        """After 10 submissions, further attempts should return 429."""
        sid = uuid.uuid4()

        # We need the mock to differentiate the two db.query() calls:
        # 1st: db.query(OBDAnalysisSession.id).filter(...).first() → (sid,)
        # 2nd: db.query(model_class).filter(...).count() → 10
        # Use side_effect on .query to return different chains.
        mock_db = MagicMock()
        exists_chain = MagicMock()
        exists_chain.filter.return_value.first.return_value = (sid,)

        count_chain = MagicMock()
        count_chain.filter.return_value.count.return_value = 10

        mock_db.query.side_effect = [exists_chain, count_chain]

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/summary",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 429
        assert "Maximum feedback" in resp.json()["detail"]

    @patch("app.api.v2.endpoints.obd_analysis._insert_feedback")
    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_under_cap_allowed(
        self, mock_ensure, mock_insert, client, app_ref,
    ):
        """Fewer than 10 submissions should still be allowed."""
        sid = uuid.uuid4()

        mock_db = MagicMock()
        exists_chain = MagicMock()
        exists_chain.filter.return_value.first.return_value = (sid,)

        count_chain = MagicMock()
        count_chain.filter.return_value.count.return_value = 9

        mock_db.query.side_effect = [exists_chain, count_chain]

        mock_insert.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/rag",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# AI Diagnosis feedback — diagnosis text snapshot
# ---------------------------------------------------------------------------


class TestAIDiagnosisFeedback:
    """Tests for the AI diagnosis feedback endpoint and its text snapshot."""

    @patch("app.api.v2.endpoints.obd_analysis._insert_feedback")
    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_snapshots_diagnosis_text(
        self, mock_ensure, mock_insert, client, app_ref,
    ):
        """AI diagnosis feedback should include the current diagnosis_text."""
        sid = str(uuid.uuid4())
        cached = _make_cached_session(sid)
        cached_with_diag = replace(cached, diagnosis_text="Test diagnosis output")
        obd_cache.put(cached_with_diag)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = (sid,)
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        mock_insert.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/ai_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201

        # Verify diagnosis_text was passed through extra_fields
        # extra_fields is the 6th positional arg to _insert_feedback
        extra = mock_insert.call_args[0][5]
        assert extra == {"diagnosis_text": "Test diagnosis output"}

    @patch("app.api.v2.endpoints.obd_analysis._insert_feedback")
    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_snapshots_none_when_no_diagnosis(
        self, mock_ensure, mock_insert, client, app_ref,
    ):
        """When no diagnosis exists, snapshot should be None."""
        sid = str(uuid.uuid4())
        cached = _make_cached_session(sid)
        obd_cache.put(cached)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = (sid,)
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        mock_insert.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/ai_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201

        extra = mock_insert.call_args[0][5]
        assert extra == {"diagnosis_text": None}

    @patch("app.api.v2.endpoints.obd_analysis._insert_feedback")
    @patch("app.api.v2.endpoints.obd_analysis._ensure_session_in_db")
    def test_feedback_truncates_long_diagnosis(
        self, mock_ensure, mock_insert, client, app_ref,
    ):
        """Diagnosis text exceeding MAX_DIAGNOSIS_LENGTH should be truncated."""
        sid = str(uuid.uuid4())
        cached = _make_cached_session(sid)
        long_text = "x" * 60_000
        cached_with_diag = replace(cached, diagnosis_text=long_text)
        obd_cache.put(cached_with_diag)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = (sid,)
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        mock_insert.return_value = {
            "status": "ok",
            "feedback_id": str(uuid.uuid4()),
        }

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{sid}/feedback/ai_diagnosis",
            json=VALID_FEEDBACK,
        )
        assert resp.status_code == 201

        extra = mock_insert.call_args[0][5]
        assert len(extra["diagnosis_text"]) == 50_000

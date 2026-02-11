"""Tests for POST /v2/tools/summarize-log-raw endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "obd_agent" / "fixtures"
FIXTURE_LOG = FIXTURE_DIR / "obd_log_20250723_144216.txt"


@pytest.fixture()
def client():
    """Create a TestClient that bypasses DB-dependent startup."""
    with patch("app.db.session.SessionLocal"), \
         patch("app.db.session.engine"):
        from app.main import app
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests (mock pipeline)
# ---------------------------------------------------------------------------


class TestV2SummarizeLogRawUnit:
    """Unit tests with pipeline functions mocked out."""

    def test_empty_body_returns_422(self, client):
        resp = client.post("/v2/tools/summarize-log-raw", content=b"")
        assert resp.status_code == 422

    @patch("app.api.v2.endpoints.log_summary._MAX_FILE_SIZE", 1024)
    def test_oversized_body_returns_413(self, client):
        resp = client.post(
            "/v2/tools/summarize-log-raw",
            content=b"x" * 1025,
        )
        assert resp.status_code == 413
        assert "limit" in resp.json()["detail"].lower()

    @patch("app.api.v2.endpoints.log_summary.summarize_log_file", side_effect=ValueError("bad data"))
    def test_pipeline_exception_returns_422(self, mock_summarize, client):
        resp = client.post(
            "/v2/tools/summarize-log-raw",
            content=b"corrupt\tdata\n",
        )
        assert resp.status_code == 422
        assert "Failed to parse" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Integration tests (real parser + pipeline, no mocks)
# ---------------------------------------------------------------------------


class TestV2SummarizeLogRawIntegration:
    """Integration tests using the real fixture file and full pipeline."""

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_full_pipeline_all_stages_populated(self, client):
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/summarize-log-raw",
            content=fixture_bytes,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Legacy fields
        assert body["vehicle_id"] == "V-38615C39"
        assert body["time_range"]["sample_count"] == 158
        assert "RPM" in body["pid_summary"]

        # Pipeline fields must be present
        assert isinstance(body["value_statistics"]["stats"], dict)
        assert len(body["value_statistics"]["stats"]) > 0
        assert body["value_statistics"]["resample_interval_seconds"] > 0

        assert isinstance(body["anomaly_events"], list)

        assert isinstance(body["diagnostic_clues"], list)

        assert isinstance(body["clue_details"], list)

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_v1_endpoint_still_works(self, client):
        """v1 endpoint must not be broken by v2 addition."""
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v1/tools/summarize-log-raw",
            content=fixture_bytes,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vehicle_id"] == "V-38615C39"
        assert "pid_summary" in body
        # v1 must NOT have v2 fields
        assert "value_statistics" not in body

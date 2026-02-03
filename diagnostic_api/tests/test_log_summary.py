"""Tests for POST /v1/tools/summarize-log endpoint."""

from __future__ import annotations

import glob
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from obd_agent.log_summarizer import LogSummary, PIDStatModel, TimeRange

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "obd_agent" / "fixtures"
FIXTURE_LOG = FIXTURE_DIR / "obd_log_20250723_144216.txt"


@pytest.fixture()
def client():
    """Create a TestClient that bypasses DB-dependent startup."""
    # Patch SessionLocal so importing app.main doesn't require a live DB.
    with patch("app.db.session.SessionLocal"), \
         patch("app.db.session.engine"):
        from app.main import app
        yield TestClient(app)


def _make_summary(**overrides) -> LogSummary:
    """Build a minimal valid LogSummary for mocking."""
    defaults = dict(
        vehicle_id="V-TEST",
        adapter="ELM327 v1.4b",
        time_range=TimeRange(
            start="2025-07-23T14:42:16Z",
            end="2025-07-23T14:47:04Z",
            duration_seconds=288,
            sample_count=158,
        ),
        dtc_codes=[],
        pid_summary={
            "RPM": PIDStatModel(min=0, max=0, mean=0, latest=0, unit="rpm"),
        },
        anomalies=[],
    )
    defaults.update(overrides)
    return LogSummary(**defaults)


# ---------------------------------------------------------------------------
# Unit tests (mock summarize_log_file)
# ---------------------------------------------------------------------------

class TestSummarizeLogUnit:
    """Unit tests with the real parser mocked out."""

    @patch("app.api.v1.endpoints.log_summary.summarize_log_file")
    def test_valid_file_returns_200(self, mock_summarize, client):
        mock_summarize.return_value = _make_summary()
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", b"some\tdata\n", "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vehicle_id"] == "V-TEST"
        assert "pid_summary" in body
        mock_summarize.assert_called_once()

    def test_wrong_extension_returns_422(self, client):
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.csv", b"data", "text/csv")},
        )
        assert resp.status_code == 422
        assert "Only .txt files" in resp.json()["detail"]

    def test_empty_file_returns_422(self, client):
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", b"", "text/plain")},
        )
        assert resp.status_code == 422
        assert "empty" in resp.json()["detail"].lower()

    @patch("app.api.v1.endpoints.log_summary.summarize_log_file")
    def test_parse_error_returns_422(self, mock_summarize, client):
        mock_summarize.side_effect = ValueError("bad TSV")
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", b"bad data", "text/plain")},
        )
        assert resp.status_code == 422
        assert "Failed to parse log file" in resp.json()["detail"]

    @patch("app.api.v1.endpoints.log_summary._MAX_FILE_SIZE", 1024)
    def test_oversized_file_returns_413(self, client):
        big = b"x" * 1025
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", big, "text/plain")},
        )
        assert resp.status_code == 413
        assert "10 MB" in resp.json()["detail"]

    def test_no_file_returns_422(self, client):
        resp = client.post("/v1/tools/summarize-log")
        assert resp.status_code == 422

    @patch("app.api.v1.endpoints.log_summary.summarize_log_file")
    def test_uppercase_txt_extension_accepted(self, mock_summarize, client):
        mock_summarize.return_value = _make_summary()
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("LOG.TXT", b"some\tdata\n", "text/plain")},
        )
        assert resp.status_code == 200

    @patch("app.api.v1.endpoints.log_summary.summarize_log_file")
    def test_special_char_filename_accepted(self, mock_summarize, client):
        mock_summarize.return_value = _make_summary()
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("obd log (2).txt", b"some\tdata\n", "text/plain")},
        )
        assert resp.status_code == 200

    @patch("app.api.v1.endpoints.log_summary.summarize_log_file")
    def test_temp_file_cleaned_up(self, mock_summarize, client):
        mock_summarize.return_value = _make_summary()
        before = set(glob.glob(tempfile.gettempdir() + "/*summarize*"))
        client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", b"some\tdata\n", "text/plain")},
        )
        after = set(glob.glob(tempfile.gettempdir() + "/*summarize*"))
        assert after == before, "temp file was not cleaned up"

    def test_wrong_content_type_returns_422(self, client):
        resp = client.post(
            "/v1/tools/summarize-log",
            files={"file": ("log.txt", b"data", "application/json")},
        )
        assert resp.status_code == 422
        assert "content type" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Integration test (real parser, no mocks)
# ---------------------------------------------------------------------------

class TestSummarizeLogIntegration:
    """Integration test using the real fixture file and parser."""

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_real_fixture_file(self, client):
        with open(FIXTURE_LOG, "rb") as f:
            resp = client.post(
                "/v1/tools/summarize-log",
                files={"file": ("obd_log_20250723_144216.txt", f, "text/plain")},
            )

        assert resp.status_code == 200
        body = resp.json()

        # Golden values from obd_log_20250723_144216.summary.json
        assert body["vehicle_id"] == "V-38615C39"
        assert body["adapter"] == "ELM327 v1.4b"
        assert body["time_range"]["sample_count"] == 158
        assert body["time_range"]["duration_seconds"] == 288
        assert body["dtc_codes"] == []

        # PID spot-checks
        assert "RPM" in body["pid_summary"]
        assert body["pid_summary"]["COOLANT_TEMP"]["min"] == 32.0
        assert body["pid_summary"]["COOLANT_TEMP"]["max"] == 32.0
        assert body["pid_summary"]["LONG_FUEL_TRIM_1"]["min"] == -10.94

        # Anomalies
        assert len(body["anomalies"]) == 2
        assert any("LONG_FUEL_TRIM_1" in a for a in body["anomalies"])
        assert any("THROTTLE_POS" in a for a in body["anomalies"])

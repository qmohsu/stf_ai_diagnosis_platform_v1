"""Tests for POST /v2/tools/parse-summary-raw endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "obd_agent" / "fixtures"
FIXTURE_LOG = FIXTURE_DIR / "obd_log_20250723_144216.txt"
GOLDEN_LOG = FIXTURE_DIR / "obd_log_20260203_163503.txt"
GOLDEN_JSON = FIXTURE_DIR / "obd_log_20260203_163503.parsed_summary_golden.json"


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


class TestParseSummaryRawUnit:
    """Unit tests with pipeline functions mocked out."""

    def test_empty_body_returns_422(self, client):
        resp = client.post("/v2/tools/parse-summary-raw", content=b"")
        assert resp.status_code == 422

    @patch("app.api.v2.endpoints.parsed_summary._MAX_FILE_SIZE", 1024)
    def test_oversized_body_returns_413(self, client):
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=b"x" * 1025,
        )
        assert resp.status_code == 413
        assert "limit" in resp.json()["detail"].lower()

    @patch(
        "app.api.v2.endpoints.parsed_summary._run_pipeline",
        side_effect=ValueError("bad data"),
    )
    def test_pipeline_exception_returns_422(self, mock_pipeline, client):
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=b"corrupt\tdata\n",
        )
        assert resp.status_code == 422
        assert "Failed to parse" in resp.json()["detail"]

    @patch("app.api.v2.endpoints.parsed_summary._run_pipeline")
    def test_successful_parse_returns_all_9_fields(self, mock_pipeline, client):
        """Mock pipeline returns a fake LogSummaryV2 → 9 string fields."""
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "vehicle_id": "V-MOCK",
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
        mock_pipeline.return_value = mock_result

        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=b"fake log data\n",
        )
        assert resp.status_code == 200

        body = resp.json()
        expected_keys = {
            "parse_ok", "vehicle_id", "time_range", "dtc_codes",
            "pid_summary", "anomaly_events", "diagnostic_clues",
            "rag_query", "debug",
        }
        assert set(body.keys()) == expected_keys
        assert body["parse_ok"] == "YES"
        assert body["vehicle_id"] == "V-MOCK"
        assert "P0420" in body["dtc_codes"]
        assert "RPM" in body["pid_summary"]
        assert body["rag_query"] != ""
        # All values must be strings
        for key, value in body.items():
            assert isinstance(value, str), f"{key} is {type(value).__name__}"


# ---------------------------------------------------------------------------
# Integration tests (real fixture, no mocks)
# ---------------------------------------------------------------------------


class TestParseSummaryRawIntegration:
    """Integration tests using the real fixture file and full pipeline."""

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_full_pipeline_parse_ok(self, client):
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=fixture_bytes,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["parse_ok"] == "YES"

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_vehicle_id_correct(self, client):
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=fixture_bytes,
        )
        body = resp.json()
        assert body["vehicle_id"] == "V-38615C39"

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_rag_query_populated(self, client):
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=fixture_bytes,
        )
        body = resp.json()
        assert body["rag_query"] != ""
        assert body["rag_query"] != "general OBD vehicle health check"

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_pid_and_anomaly_formatting(self, client):
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/parse-summary-raw",
            content=fixture_bytes,
        )
        body = resp.json()
        # PID summary should contain known PIDs
        assert "RPM" in body["pid_summary"]

    @pytest.mark.skipif(
        not FIXTURE_LOG.exists(),
        reason="fixture file not found",
    )
    def test_v2_original_endpoint_still_works(self, client):
        """Regression: the original v2 endpoint must still function."""
        fixture_bytes = FIXTURE_LOG.read_bytes()
        resp = client.post(
            "/v2/tools/summarize-log-raw",
            content=fixture_bytes,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vehicle_id"] == "V-38615C39"
        assert "value_statistics" in body


# ---------------------------------------------------------------------------
# Golden-test helper
# ---------------------------------------------------------------------------


def assert_json_equal(actual, expected, path="$"):
    """Recursively compare two JSON-like structures.

    * ``float`` values use ``pytest.approx(abs=1e-6, rel=1e-4)``.
    * All other scalars require exact equality.
    * Assertion messages include the JSON path for easy debugging.
    """
    if expected is None:
        assert actual is None, f"{path}: expected None, got {actual!r}"
    elif isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {type(actual).__name__}"
        assert set(actual.keys()) == set(expected.keys()), (
            f"{path}: key mismatch — "
            f"extra={set(actual.keys()) - set(expected.keys())}, "
            f"missing={set(expected.keys()) - set(actual.keys())}"
        )
        for key in expected:
            assert_json_equal(actual[key], expected[key], path=f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: list length {len(actual)} != {len(expected)}"
        )
        for idx, (a, e) in enumerate(zip(actual, expected)):
            assert_json_equal(a, e, path=f"{path}[{idx}]")
    elif isinstance(expected, float):
        assert isinstance(actual, (int, float)), (
            f"{path}: expected number, got {type(actual).__name__}"
        )
        assert actual == pytest.approx(expected, abs=1e-6, rel=1e-4), (
            f"{path}: {actual} != {expected} (float)"
        )
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


# ---------------------------------------------------------------------------
# Golden snapshot test
# ---------------------------------------------------------------------------


def _load_golden(path: Path) -> dict:
    """Load the human-readable golden JSON and reconstruct flat strings.

    The golden file stores multiline text fields as JSON arrays for
    readability.  This helper converts them back to the newline-joined
    strings the endpoint returns.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Arrays of lines → newline-joined strings
    for key in ("pid_summary", "anomaly_events", "diagnostic_clues"):
        val = raw[key]
        if isinstance(val, list):
            raw[key] = "\n".join(val)

    return raw


class TestParsedSummaryGoldenOutput:
    """Golden test: full parsed summary response must match saved snapshot."""

    @pytest.mark.skipif(
        not GOLDEN_LOG.exists() or not GOLDEN_JSON.exists(),
        reason="golden fixture files not found",
    )
    def test_response_matches_golden(self, client):
        """Full parsed summary response must exactly match golden snapshot."""
        fixture_bytes = GOLDEN_LOG.read_bytes()
        golden = _load_golden(GOLDEN_JSON)

        resp = client.post("/v2/tools/parse-summary-raw", content=fixture_bytes)
        assert resp.status_code == 200

        actual = resp.json()
        assert_json_equal(actual, golden, path="$")

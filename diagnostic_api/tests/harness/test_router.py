"""Tests for the agent diagnosis endpoint (``app.harness.router``).

Covers:
  - POST /v2/obd/{session_id}/diagnose/agent (auth, cache, SSE stream)
  - Auth required (401 without token)
  - Session ownership (404 for other user's session)
  - Cached diagnosis returned when ``force=False``
  - SSE stream format with ``text/event-stream`` content type
  - ``done`` event contains ``diagnosis_history_id``
  - ``DiagnosisHistory`` row created with ``provider="agent"``
  - Error event on agent loop failure
  - V1 ``/diagnose`` endpoint regression test
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.harness.deps import HarnessEvent


# ── Constants ───────────────────────────────────────────────────────

FAKE_SESSION_ID = uuid.UUID(
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
)
FAKE_USER_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000001",
)
OTHER_USER_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000099",
)
FAKE_HISTORY_ID = uuid.UUID(
    "11111111-2222-3333-4444-555555555555",
)

FAKE_PARSED_SUMMARY: Dict[str, Any] = {
    "vehicle_id": "V12345",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
    "dtc_codes": "P0300 (Random/Multiple Cylinder Misfire)",
    "pid_summary": "RPM: 780-4200, COOLANT_TEMP: 89-95",
    "anomaly_events": "RPM range_shift at 08:32",
    "diagnostic_clues": "STAT_001 Engine misfire pattern",
}


# ── Fixtures ────────────────────────────────────────────────────────


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


def _mock_db_with_session(
    parsed_summary: Any = None,
    diagnosis_text: str | None = None,
    user_id: uuid.UUID = FAKE_USER_ID,
    history_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock DB where session lookup succeeds.

    Args:
        parsed_summary: Value for ``parsed_summary_payload``.
        diagnosis_text: Existing cached diagnosis (or ``None``).
        user_id: Owner user ID.
        history_id: UUID for latest DiagnosisHistory row.

    Returns:
        Mock SQLAlchemy session.
    """
    mock_session = MagicMock()
    mock_session.id = FAKE_SESSION_ID
    mock_session.user_id = user_id
    mock_session.parsed_summary_payload = parsed_summary
    mock_session.diagnosis_text = diagnosis_text

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = (
        mock_session
    )

    if history_id is not None:
        mock_hist = MagicMock()
        mock_hist.id = history_id
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            mock_hist
        )
    else:
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

    return mock_db


def _mock_db_no_session() -> MagicMock:
    """Build a mock DB where session lookup returns None."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = (
        None
    )
    return mock_db


def _parse_sse_events(text: str) -> List[Dict[str, Any]]:
    """Parse SSE text into a list of event dicts.

    Each dict has ``event`` (str) and ``data`` (parsed JSON).

    Args:
        text: Raw SSE response body.

    Returns:
        List of parsed events (comments and padding excluded).
    """
    events = []
    current_event = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: ") and current_event:
            data = json.loads(line[6:])
            events.append({
                "event": current_event,
                "data": data,
            })
            current_event = None
    return events


# ── Auth Tests ──────────────────────────────────────────────────────


class TestAgentDiagnosisAuth:
    """Auth and session ownership tests."""

    def test_401_without_token(self, app_ref):
        """Endpoint returns 401 without Bearer token."""
        app_ref.dependency_overrides.clear()

        with patch("app.db.session.SessionLocal"), \
             patch("app.db.session.engine"):
            from app.main import app
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )
        assert resp.status_code == 401

    def test_404_for_other_users_session(
        self, client, app_ref,
    ):
        """Session owned by another user returns 404."""
        from app.api.deps import get_db
        mock_db = _mock_db_no_session()
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
        )
        assert resp.status_code == 404


# ── Cache Tests ─────────────────────────────────────────────────────


class TestAgentDiagnosisCached:
    """Cached diagnosis tests."""

    def test_cached_diagnosis_returned(
        self, client, app_ref,
    ):
        """Existing diagnosis + force=False returns cached SSE."""
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text="Prior agent diagnosis.",
            history_id=FAKE_HISTORY_ID,
        )
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent"
            f"?force=false",
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events = _parse_sse_events(resp.text)
        assert len(events) == 1
        assert events[0]["event"] == "cached"
        assert events[0]["data"]["text"] == (
            "Prior agent diagnosis."
        )
        assert events[0]["data"]["diagnosis_history_id"] == (
            str(FAKE_HISTORY_ID)
        )

    def test_force_bypasses_cache(
        self, client, app_ref,
    ):
        """force=True runs agent loop even with existing diagnosis."""
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text="Prior agent diagnosis.",
            history_id=FAKE_HISTORY_ID,
        )
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "New diagnosis.",
                "partial": False,
                "iterations": 1,
                "tools_called": [],
            })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent"
                f"?force=true",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "done" in event_types
        assert "cached" not in event_types


# ── Streaming Tests ─────────────────────────────────────────────────


class TestAgentDiagnosisStream:
    """Agent loop SSE streaming tests."""

    def _setup_db(self, app_ref):
        """Attach mock DB with valid session (no cached diagnosis)."""
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = lambda: mock_db

    def test_sse_content_type(self, client, app_ref):
        """Response has text/event-stream content type."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "Diagnosis text.",
                "partial": False,
                "iterations": 1,
                "tools_called": [],
            })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_2kb_padding_prefix(self, client, app_ref):
        """Response starts with 2KB padding comment."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "D.",
                "partial": False,
                "iterations": 1,
                "tools_called": [],
            })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        assert resp.text.startswith(": ")
        # First line should be at least 2048 chars (padding).
        first_line = resp.text.split("\n\n")[0]
        assert len(first_line) >= 2048

    def test_done_event_has_diagnosis_history_id(
        self, client, app_ref,
    ):
        """Done event includes diagnosis_history_id UUID."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "Final diagnosis.",
                "partial": False,
                "iterations": 2,
                "tools_called": ["search_manual"],
            })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        events = _parse_sse_events(resp.text)
        done_events = [
            e for e in events if e["event"] == "done"
        ]
        assert len(done_events) == 1
        done = done_events[0]["data"]
        assert done["diagnosis_history_id"] == str(
            FAKE_HISTORY_ID,
        )
        assert done["iterations"] == 2
        assert done["tools_called"] == ["search_manual"]
        assert done["autonomy_tier"] == 1
        assert done["text"] == "Final diagnosis."

    def test_tool_call_and_result_events(
        self, client, app_ref,
    ):
        """Tool call and result events are streamed."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("tool_call", {
                "name": "search_manual",
                "input": {"query": "P0300"},
                "iteration": 0,
                "tool_call_id": "tc_1",
            })
            yield HarnessEvent("tool_result", {
                "name": "search_manual",
                "output": "[0.87] MWS150-A#3.2",
                "duration_ms": 120.5,
                "is_error": False,
                "iteration": 0,
            })
            yield HarnessEvent("done", {
                "diagnosis": "Misfire diagnosis.",
                "partial": False,
                "iterations": 1,
                "tools_called": ["search_manual"],
            })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "status" in event_types
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "done" in event_types

        tc_event = next(
            e for e in events if e["event"] == "tool_call"
        )
        assert tc_event["data"]["name"] == "search_manual"

        tr_event = next(
            e for e in events if e["event"] == "tool_result"
        )
        assert tr_event["data"]["name"] == "search_manual"
        assert tr_event["data"]["is_error"] is False

    def test_store_diagnosis_called_with_agent_provider(
        self, client, app_ref,
    ):
        """_store_diagnosis is called with provider='agent'."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "Agent result.",
                "partial": False,
                "iterations": 1,
                "tools_called": [],
            })

        mock_store = MagicMock(return_value=FAKE_HISTORY_ID)

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            mock_store,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        assert resp.status_code == 200
        mock_store.assert_called_once()
        args = mock_store.call_args
        assert args[0][0] == FAKE_SESSION_ID
        assert args[0][1] == "agent"
        assert args[0][3] == "Agent result."


# ── Error Handling Tests ────────────────────────────────────────────


class TestAgentDiagnosisErrors:
    """Error handling tests."""

    def test_no_parsed_summary_returns_422(
        self, client, app_ref,
    ):
        """Session without parsed_summary returns 422."""
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=None,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
        )
        assert resp.status_code == 422

    def test_agent_loop_exception_yields_error_event(
        self, client, app_ref,
    ):
        """Exception during agent loop yields SSE error event."""
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=FAKE_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        async def _failing_loop(
            session_id, parsed_summary, deps,
        ):
            raise RuntimeError("LLM connection failed")
            yield  # noqa: unreachable — makes this a generator

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_failing_loop,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [
            e for e in events if e["event"] == "error"
        ]
        assert len(error_events) >= 1
        assert "LLM connection failed" in (
            error_events[0]["data"]["message"]
        )


# ── V1 Regression Test ──────────────────────────────────────────────


class TestV1DiagnoseRegression:
    """V1 endpoints still work after harness router registration."""

    def test_v1_diagnose_still_accessible(
        self, client, app_ref,
    ):
        """V1 /diagnose returns 404 for missing session (not 405).

        A 404 (session not found) proves the route is registered
        and the endpoint handler runs.  405 would mean the route
        is gone.
        """
        from app.api.deps import get_db
        mock_db = _mock_db_no_session()
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose",
        )
        assert resp.status_code == 404

    def test_v1_premium_diagnose_still_accessible(
        self, client, app_ref,
    ):
        """V1 /diagnose/premium returns a handled status (not 405).

        The premium endpoint has a feature gate that returns 403
        when ``premium_llm_enabled=False`` (test default).  A 403
        (or 404) proves the route is registered and the handler
        runs.  405 would mean the route was lost.
        """
        from app.api.deps import get_db
        mock_db = _mock_db_no_session()
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose/premium",
        )
        # 403 (feature disabled) or 404 (session not found)
        # — both prove the handler runs.
        assert resp.status_code in (403, 404)

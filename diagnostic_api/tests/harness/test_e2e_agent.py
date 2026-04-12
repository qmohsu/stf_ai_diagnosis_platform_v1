"""End-to-end tests for the agent diagnosis endpoint (HARNESS-08).

Exercises the full HTTP path through FastAPI's ``TestClient``,
mocking only the LLM and DB layers.  Tests marked with
``@pytest.mark.e2e_real_llm`` use a real premium LLM and are
excluded from CI (run with ``pytest -m e2e_real_llm``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.harness.autonomy import AutonomyDecision
from app.harness.deps import HarnessEvent

from tests.harness.fixtures import (
    load_fallback_fixture,
    load_llm_responses,
)

# ── Constants ──────────────────────────────────────────────────────

FAKE_SESSION_ID = uuid.UUID(
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
)
FAKE_USER_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000001",
)
FAKE_HISTORY_ID = uuid.UUID(
    "11111111-2222-3333-4444-555555555555",
)

PARSED_SUMMARY: Dict[str, Any] = {
    "vehicle_id": "V12345",
    "time_range": (
        "2026-04-01 08:00 – 2026-04-01 09:00"
    ),
    "dtc_codes": (
        "P0300 (Random/Multiple Cylinder Misfire), "
        "P0301 (Cylinder 1 Misfire), "
        "P0302 (Cylinder 2 Misfire)"
    ),
    "pid_summary": "RPM: 780-4200, COOLANT_TEMP: 89-95",
    "anomaly_events": (
        "RPM range_shift at 08:32 (high severity)"
    ),
    "diagnostic_clues": (
        "STAT_001 Engine misfire pattern; "
        "RULE_002 Ignition system suspect"
    ),
}

_DEFAULT_DECISION = AutonomyDecision(
    tier=1,
    strategy="agent loop",
    reason="e2e test fixture",
    use_agent=True,
    suggested_max_iterations=5,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """Create a TestClient that bypasses DB startup."""
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


@pytest.fixture()
def patch_autonomy():
    """Stub autonomy classifier to Tier 1 (agent path)."""
    with patch(
        "app.harness.router.classify_complexity",
        return_value=_DEFAULT_DECISION,
    ), patch(
        "app.harness.router.apply_overrides",
        return_value=_DEFAULT_DECISION,
    ):
        yield


# ── Helpers ────────────────────────────────────────────────────────


def _mock_db_with_session(
    parsed_summary: Any = None,
    diagnosis_text: str | None = None,
    user_id: uuid.UUID = FAKE_USER_ID,
    history_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock DB where session lookup succeeds."""
    mock_session = MagicMock()
    mock_session.id = FAKE_SESSION_ID
    mock_session.user_id = user_id
    mock_session.parsed_summary_payload = parsed_summary
    mock_session.diagnosis_text = diagnosis_text

    mock_db = MagicMock()
    mock_db.query.return_value \
        .filter.return_value \
        .first.return_value = mock_session

    if history_id is not None:
        mock_hist = MagicMock()
        mock_hist.id = history_id
        mock_db.query.return_value \
            .filter.return_value \
            .order_by.return_value \
            .first.return_value = mock_hist
    else:
        mock_db.query.return_value \
            .filter.return_value \
            .order_by.return_value \
            .first.return_value = None

    return mock_db


def _parse_sse_events(
    text: str,
) -> List[Dict[str, Any]]:
    """Parse SSE text into a list of event dicts."""
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


# ── Golden-path E2E tests ─────────────────────────────────────────


@pytest.mark.usefixtures("patch_autonomy")
class TestE2EGoldenPath:
    """Full HTTP golden-path tests with mocked LLM."""

    def _setup_db(self, app_ref):
        """Attach mock DB with valid session."""
        from app.api.deps import get_db
        mock_db = _mock_db_with_session(
            parsed_summary=PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

    def test_e2e_golden_path_stream(
        self, client, app_ref,
    ):
        """Full SSE stream: 200, padding, status, tool_call,
        tool_result, done with diagnosis_history_id.
        """
        self._setup_db(app_ref)

        responses = load_llm_responses(
            "golden_path_responses.json",
        )

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            """Emit events matching golden-path fixture."""
            yield HarnessEvent("session_start", {
                "session_id": str(session_id),
            })
            tools_called = []
            for i, r in enumerate(responses):
                if r.tool_calls:
                    for tc in r.tool_calls:
                        tools_called.append(tc.name)
                        yield HarnessEvent("tool_call", {
                            "tool": tc.name,
                            "input": tc.arguments,
                            "iteration": i,
                        })
                        yield HarnessEvent(
                            "tool_result", {
                                "tool": tc.name,
                                "output": (
                                    f"{tc.name} result"
                                ),
                                "iteration": i,
                            },
                        )
                elif r.content:
                    yield HarnessEvent("done", {
                        "diagnosis": r.content,
                        "partial": False,
                        "iterations": i,
                        "tools_called": tools_called,
                    })

        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
            )

        assert resp.status_code == 200
        assert "text/event-stream" in (
            resp.headers["content-type"]
        )
        # 2KB padding at start.
        assert resp.text.startswith(": ")

        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]

        assert "status" in event_types
        assert event_types.count("tool_call") >= 2
        assert event_types.count("tool_result") >= 2
        assert "done" in event_types

        done_evt = [
            e for e in events if e["event"] == "done"
        ][0]
        assert done_evt["data"]["diagnosis_history_id"]
        assert done_evt["data"]["iterations"] >= 1
        assert (
            len(done_evt["data"]["tools_called"]) >= 2
        )
        assert "autonomy_tier" in done_evt["data"]
        assert "autonomy_strategy" in done_evt["data"]

    def test_e2e_stores_diagnosis_history(
        self, client, app_ref,
    ):
        """_store_diagnosis called with provider='agent'."""
        self._setup_db(app_ref)

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("done", {
                "diagnosis": "Test diagnosis.",
                "partial": False,
                "iterations": 1,
                "tools_called": ["search_manual"],
            })

        store_mock = MagicMock(
            return_value=FAKE_HISTORY_ID,
        )
        with patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_fake_loop,
        ), patch(
            "app.harness.router._store_diagnosis",
            store_mock,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
            )

        assert resp.status_code == 200
        store_mock.assert_called_once()
        call_args = store_mock.call_args
        assert call_args[0][0] == FAKE_SESSION_ID
        assert call_args[0][1] == "agent"

    def test_e2e_cached_skips_agent(
        self, client, app_ref,
    ):
        """force=false + existing diagnosis -> cached event."""
        from app.api.deps import get_db
        mock_db = _mock_db_with_session(
            parsed_summary=PARSED_SUMMARY,
            diagnosis_text="Prior diagnosis.",
            history_id=FAKE_HISTORY_ID,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        resp = client.post(
            f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent"
            f"?force=false",
        )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert len(events) == 1
        assert events[0]["event"] == "cached"
        assert events[0]["data"]["text"] == (
            "Prior diagnosis."
        )

    def test_e2e_force_bypasses_cache(
        self, client, app_ref,
    ):
        """force=true runs agent even with cached diagnosis."""
        from app.api.deps import get_db
        mock_db = _mock_db_with_session(
            parsed_summary=PARSED_SUMMARY,
            diagnosis_text="Prior diagnosis.",
            history_id=FAKE_HISTORY_ID,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

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
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent?force=true",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "done" in event_types
        assert "cached" not in event_types


# ── Fallback E2E tests ────────────────────────────────────────────


class TestE2EFallback:
    """Agent failure falls back to V1 one-shot via HTTP."""

    def test_e2e_fallback_on_agent_error(
        self, client, app_ref,
    ):
        """Agent raises -> fallback to V1 oneshot, SSE stream
        has error, status 'Falling back', tokens, and done.
        """
        from app.api.deps import get_db
        mock_db = _mock_db_with_session(
            parsed_summary=PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        fixture = load_fallback_fixture()
        decision = AutonomyDecision(
            tier=1,
            strategy="agent loop",
            reason="e2e fallback test",
            use_agent=True,
            suggested_max_iterations=5,
        )

        async def _failing_loop(
            session_id, parsed_summary, deps,
        ):
            raise RuntimeError(fixture["agent_error"])
            yield  # noqa: unreachable (async gen)

        async def _fake_oneshot_gen(summary, ctx, **kw):
            for token in fixture["oneshot_tokens"]:
                yield token

        with patch(
            "app.harness.router.classify_complexity",
            return_value=decision,
        ), patch(
            "app.harness.router.apply_overrides",
            return_value=decision,
        ), patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_failing_loop,
        ), patch(
            "app.harness.router.retrieve_context",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.harness.router._expert_client"
            ".generate_obd_diagnosis_stream",
            side_effect=_fake_oneshot_gen,
        ), patch(
            "app.harness.router._store_diagnosis",
            return_value=FAKE_HISTORY_ID,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]

        assert "error" in event_types
        assert "status" in event_types
        assert "token" in event_types
        assert "done" in event_types

        done_evt = [
            e for e in events if e["event"] == "done"
        ][0]
        assert "fallback" in (
            done_evt["data"]["autonomy_strategy"]
        )


# ── Optional real-LLM E2E test ───────────────────────────────────


@pytest.mark.e2e_real_llm
class TestE2ERealLLM:
    """Tests with real premium LLM (excluded from CI).

    Run with: ``pytest -m e2e_real_llm -v``

    Requires ``PREMIUM_LLM_API_KEY`` env var to be set.
    """

    def test_real_llm_golden_path(
        self, client, app_ref,
    ):
        """Real LLM produces diagnosis within timeout.

        Skipped unless PREMIUM_LLM_API_KEY is set.
        """
        import os

        if not os.environ.get("PREMIUM_LLM_API_KEY"):
            pytest.skip(
                "PREMIUM_LLM_API_KEY not set"
            )

        from app.api.deps import get_db
        mock_db = _mock_db_with_session(
            parsed_summary=PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        decision = AutonomyDecision(
            tier=1,
            strategy="agent loop",
            reason="real LLM e2e test",
            use_agent=True,
            suggested_max_iterations=5,
        )
        with patch(
            "app.harness.router.classify_complexity",
            return_value=decision,
        ), patch(
            "app.harness.router.apply_overrides",
            return_value=decision,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
                timeout=60,
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "done" in event_types

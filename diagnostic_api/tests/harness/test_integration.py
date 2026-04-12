"""Integration tests for the harness agent pipeline (HARNESS-08).

Tests the full loop + tool-registry + event-log pipeline with a
mocked LLM (pre-recorded responses from JSON fixtures).  Unlike
the unit tests in ``test_loop.py`` that patch ``emit_event`` to a
no-op, these tests capture emitted events for assertion.

Also covers graduated-autonomy routing and the agent-to-V1-oneshot
fallback path via the ``/diagnose/agent`` HTTP endpoint.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.harness.autonomy import AutonomyDecision
from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    HarnessEvent,
    LLMResponse,
    ToolCallInfo,
)
from app.harness.loop import run_diagnosis_loop
from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
)

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

INTEGRATION_PARSED_SUMMARY: Dict[str, Any] = {
    "vehicle_id": "V12345",
    "time_range": "2026-04-01 08:00 – 2026-04-01 09:00",
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

TIER_0_PARSED_SUMMARY: Dict[str, Any] = {
    "vehicle_id": "V99999",
    "time_range": "2026-04-01 10:00 – 2026-04-01 11:00",
    "dtc_codes": "P0420 (Catalyst System Efficiency)",
    "pid_summary": "RPM: 800, COOLANT_TEMP: 90",
    "anomaly_events": "",
    "diagnostic_clues": "STAT_010 Minor catalyst aging",
}


# ── Mock LLM Client ───────────────────────────────────────────────


class MockLLMClient:
    """LLM client that replays pre-recorded responses.

    Attributes:
        calls: Recorded call kwargs for assertions.
    """

    def __init__(
        self, responses: List[LLMResponse],
    ) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: List[Dict[str, Any]] = []

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Return the next pre-recorded response."""
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "model": model,
        })
        if self._index >= len(self._responses):
            raise RuntimeError(
                "MockLLMClient exhausted all responses"
            )
        resp = self._responses[self._index]
        self._index += 1
        return resp


class ErrorMockLLMClient:
    """LLM client that raises on the first call."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def chat(self, **kwargs: Any) -> LLMResponse:
        """Raise the configured error."""
        raise self._error


# ── Echo tool factory ─────────────────────────────────────────────


def _echo_tool(
    name: str,
    required_fields: List[str] | None = None,
) -> ToolDefinition:
    """Create a tool that echoes its input as a string.

    Args:
        name: Tool name (e.g. ``"search_manual"``).
        required_fields: Required input fields.
            Defaults to ``["session_id"]``.

    Returns:
        ToolDefinition with an echo handler.
    """
    if required_fields is None:
        required_fields = ["session_id"]
    props = {
        f: {"type": "string"} for f in required_fields
    }

    async def handler(
        input_data: Dict[str, Any],
    ) -> str:
        return f"{name} result: {input_data}"

    return ToolDefinition(
        name=name,
        description=f"Echo tool: {name}",
        input_schema={
            "type": "object",
            "properties": props,
            "required": required_fields,
        },
        handler=handler,
    )


def _make_golden_registry() -> ToolRegistry:
    """Build a registry matching golden-path fixture tools."""
    registry = ToolRegistry()
    registry.register(_echo_tool("read_obd_data"))
    registry.register(
        _echo_tool("search_manual", ["query"]),
    )
    return registry


# ── Helpers ────────────────────────────────────────────────────────


def _make_deps(
    client: Any,
    registry: ToolRegistry | None = None,
    **overrides: Any,
) -> HarnessDeps:
    """Build HarnessDeps with test defaults."""
    if registry is None:
        registry = _make_golden_registry()
    config_kwargs: Dict[str, Any] = {
        "model": "test/mock-model",
        "max_iterations": 10,
        "timeout_seconds": 30.0,
    }
    config_kwargs.update(overrides)
    return HarnessDeps(
        llm_client=client,
        tool_registry=registry,
        config=HarnessConfig(**config_kwargs),
    )


async def _collect_events(
    gen: AsyncIterator[HarnessEvent],
) -> List[HarnessEvent]:
    """Drain an async generator into a list."""
    events: List[HarnessEvent] = []
    async for event in gen:
        events.append(event)
    return events


# ── HTTP helpers ──────────────────────────────────────────────────


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


# ── HTTP fixtures ─────────────────────────────────────────────────


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


# ── Golden-path integration tests ─────────────────────────────────


class TestGoldenPathIntegration:
    """Agent loop integration with mocked LLM + echo tools."""

    @pytest.mark.asyncio
    async def test_golden_path_agent_calls_tools_and_diagnosis(
        self,
    ):
        """Agent calls >= 2 tools, produces a non-partial
        diagnosis with tools_called list in the done event.
        """
        responses = load_llm_responses(
            "golden_path_responses.json",
        )
        llm = MockLLMClient(responses)
        deps = _make_deps(llm)

        with patch(
            "app.harness.loop.emit_event",
            new_callable=AsyncMock,
        ):
            events = await _collect_events(
                run_diagnosis_loop(
                    FAKE_SESSION_ID,
                    INTEGRATION_PARSED_SUMMARY,
                    deps,
                ),
            )

        types = [e.event_type for e in events]
        assert "session_start" in types
        assert "done" in types
        assert types.count("tool_call") >= 2
        assert types.count("tool_result") >= 2

        done_event = [
            e for e in events if e.event_type == "done"
        ][0]
        assert done_event.payload["partial"] is False
        assert len(done_event.payload["tools_called"]) >= 2
        assert done_event.payload["diagnosis"]

    @pytest.mark.asyncio
    async def test_event_log_records_all_required_types(
        self,
    ):
        """emit_event is called with session_start, tool_call,
        tool_result, and done event types.
        """
        responses = load_llm_responses(
            "golden_path_responses.json",
        )
        llm = MockLLMClient(responses)
        deps = _make_deps(llm)

        emit_mock = AsyncMock()
        with patch(
            "app.harness.loop.emit_event",
            emit_mock,
        ):
            await _collect_events(
                run_diagnosis_loop(
                    FAKE_SESSION_ID,
                    INTEGRATION_PARSED_SUMMARY,
                    deps,
                ),
            )

        emitted_types = [
            call.args[1] for call in emit_mock.call_args_list
        ]
        assert "session_start" in emitted_types
        assert "tool_call" in emitted_types
        assert "tool_result" in emitted_types
        assert "diagnosis_done" in emitted_types

        for call in emit_mock.call_args_list:
            assert call.args[0] == FAKE_SESSION_ID

    @pytest.mark.asyncio
    async def test_event_log_iterations_monotonic(self):
        """Iteration numbers in emitted events are
        non-decreasing.
        """
        responses = load_llm_responses(
            "golden_path_responses.json",
        )
        llm = MockLLMClient(responses)
        deps = _make_deps(llm)

        emit_mock = AsyncMock()
        with patch(
            "app.harness.loop.emit_event",
            emit_mock,
        ):
            await _collect_events(
                run_diagnosis_loop(
                    FAKE_SESSION_ID,
                    INTEGRATION_PARSED_SUMMARY,
                    deps,
                ),
            )

        iterations = []
        for call in emit_mock.call_args_list:
            if len(call.args) >= 4:
                iterations.append(call.args[3])
            elif "iteration" in call.kwargs:
                iterations.append(
                    call.kwargs["iteration"]
                )
        if iterations:
            for i in range(1, len(iterations)):
                assert iterations[i] >= iterations[i - 1]


# ── Autonomy routing integration tests ────────────────────────────


class TestAutonomyRoutingIntegration:
    """Tier 0 routes to one-shot; Tier 1+ routes to agent."""

    def test_tier0_routes_to_oneshot(
        self, client, app_ref,
    ):
        """Tier 0 input routes to V1 one-shot path, producing
        token events instead of tool_call events.
        """
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=TIER_0_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        async def _fake_oneshot_gen(summary, ctx, **kw):
            yield "One-shot diagnosis."

        with patch(
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
                f"/v2/obd/{FAKE_SESSION_ID}/diagnose/agent",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "tool_call" not in event_types
        assert "token" in event_types or "done" in event_types

    def test_tier1_routes_to_agent(
        self, client, app_ref,
    ):
        """Tier 1 input routes to agent loop path, producing
        tool_call events.
        """
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=INTEGRATION_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        async def _fake_loop(
            session_id, parsed_summary, deps,
        ):
            yield HarnessEvent("tool_call", {
                "tool": "search_manual",
                "input": {"query": "P0300"},
                "iteration": 0,
            })
            yield HarnessEvent("tool_result", {
                "tool": "search_manual",
                "output": "manual result",
                "iteration": 0,
            })
            yield HarnessEvent("done", {
                "diagnosis": "Agent diagnosis.",
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
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "tool_call" in event_types
        assert "done" in event_types


# ── Fallback integration tests ────────────────────────────────────


class TestFallbackIntegration:
    """Agent failure falls back to V1 one-shot."""

    def _patch_autonomy_tier1(self):
        """Patch autonomy to always return Tier 1 (agent)."""
        decision = AutonomyDecision(
            tier=1,
            strategy="agent loop",
            reason="integration test",
            use_agent=True,
            suggested_max_iterations=5,
        )
        return patch(
            "app.harness.router.classify_complexity",
            return_value=decision,
        ), patch(
            "app.harness.router.apply_overrides",
            return_value=decision,
        )

    def test_fallback_agent_error_triggers_oneshot(
        self, client, app_ref,
    ):
        """Agent loop raises -> error event emitted, then
        fallback to V1 one-shot produces token + done events.
        """
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=INTEGRATION_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )
        fixture = load_fallback_fixture()

        async def _failing_loop(
            session_id, parsed_summary, deps,
        ):
            raise RuntimeError(fixture["agent_error"])
            yield  # noqa: unreachable (async gen)

        async def _fake_oneshot_gen(summary, ctx, **kw):
            for token in fixture["oneshot_tokens"]:
                yield token

        p1, p2 = self._patch_autonomy_tier1()
        with p1, p2, patch(
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

        # Error event references the agent failure.
        error_evt = [
            e for e in events if e["event"] == "error"
        ][0]
        assert "stream_error" in (
            error_evt["data"]["error_type"]
        )

        # Done event has "(fallback)" in strategy.
        done_evt = [
            e for e in events if e["event"] == "done"
        ][0]
        assert "fallback" in (
            done_evt["data"]["autonomy_strategy"]
        )

    def test_fallback_both_fail_emits_two_errors(
        self, client, app_ref,
    ):
        """Agent raises, oneshot also raises -> two error events
        emitted but HTTP response remains 200 with valid SSE.
        """
        from app.api.deps import get_db

        mock_db = _mock_db_with_session(
            parsed_summary=INTEGRATION_PARSED_SUMMARY,
            diagnosis_text=None,
        )
        app_ref.dependency_overrides[get_db] = (
            lambda: mock_db
        )

        async def _failing_loop(
            session_id, parsed_summary, deps,
        ):
            raise RuntimeError("Agent LLM down")
            yield  # noqa: unreachable (async gen)

        async def _failing_oneshot_gen(summary, ctx, **kw):
            raise RuntimeError("Ollama also down")
            yield  # noqa: unreachable (async gen)

        p1, p2 = self._patch_autonomy_tier1()
        with p1, p2, patch(
            "app.harness.router.run_diagnosis_loop",
            side_effect=_failing_loop,
        ), patch(
            "app.harness.router.retrieve_context",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.harness.router._expert_client"
            ".generate_obd_diagnosis_stream",
            side_effect=_failing_oneshot_gen,
        ):
            resp = client.post(
                f"/v2/obd/{FAKE_SESSION_ID}"
                f"/diagnose/agent",
            )

        assert resp.status_code == 200
        assert "text/event-stream" in (
            resp.headers["content-type"]
        )

        events = _parse_sse_events(resp.text)
        error_events = [
            e for e in events if e["event"] == "error"
        ]
        assert len(error_events) >= 2

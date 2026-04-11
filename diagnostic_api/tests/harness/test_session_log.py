"""Tests for session event log persistence.

Verifies ``emit_event()`` and ``get_session_events()`` correctly
persist and retrieve harness events.  Mocks the database layer
to avoid requiring a running PostgreSQL instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.harness import session_log


# ── Test fixtures ──────────────────────────────────────────────────

FAKE_SESSION_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class FakeEventRow:
    """Mimics a ``HarnessEventLog`` ORM row for query results."""

    def __init__(
        self,
        event_id: uuid.UUID,
        session_id: uuid.UUID,
        event_type: str,
        iteration: int,
        payload: Dict[str, Any],
        created_at: datetime,
    ) -> None:
        self.id = event_id
        self.session_id = session_id
        self.event_type = event_type
        self.iteration = iteration
        self.payload = payload
        self.created_at = created_at


def _make_mock_db(
    query_rows: List[FakeEventRow] | None = None,
) -> MagicMock:
    """Build a mock SessionLocal that records add/commit calls.

    Args:
        query_rows: Rows returned by query().filter().order_by().all().

    Returns:
        Mock session factory (callable returning a mock session).
    """
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = MagicMock()
    mock_session.rollback = MagicMock()
    mock_session.close = MagicMock()

    # Chain: query().filter().order_by().all()
    mock_query = MagicMock()
    mock_filter = MagicMock()
    mock_order = MagicMock()
    mock_order.all.return_value = query_rows or []
    mock_filter.order_by.return_value = mock_order
    mock_query.filter.return_value = mock_filter
    mock_session.query.return_value = mock_query

    mock_factory = MagicMock(return_value=mock_session)
    return mock_factory


# ── Tests: emit_event ──────────────────────────────────────────────


class TestEmitEvent:
    """Tests for ``emit_event()`` persistence."""

    @pytest.mark.asyncio
    async def test_emit_calls_add_and_commit(self) -> None:
        """emit_event adds a row and commits the session."""
        mock_factory = _make_mock_db()
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            await session_log.emit_event(
                FAKE_SESSION_ID,
                "session_start",
                {"model": "test-model"},
                iteration=0,
            )

        mock_session = mock_factory.return_value
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

        # Verify the added object has correct attributes.
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.session_id == FAKE_SESSION_ID
        assert added_obj.event_type == "session_start"
        assert added_obj.iteration == 0
        assert added_obj.payload == {"model": "test-model"}

    @pytest.mark.asyncio
    async def test_emit_multiple_events(self) -> None:
        """Multiple emit calls create independent rows."""
        added_objects: List[Any] = []
        mock_factory = _make_mock_db()

        def capture_add(obj):
            added_objects.append(obj)

        mock_factory.return_value.add.side_effect = capture_add

        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            for etype, payload, iteration in [
                ("session_start", {"model": "m"}, 0),
                ("tool_call", {"name": "t"}, 0),
                ("tool_result", {"output": "r"}, 0),
                ("diagnosis_done", {"diagnosis": "d"}, 1),
            ]:
                await session_log.emit_event(
                    FAKE_SESSION_ID, etype, payload,
                    iteration=iteration,
                )

        assert len(added_objects) == 4
        types = [o.event_type for o in added_objects]
        assert types == [
            "session_start", "tool_call",
            "tool_result", "diagnosis_done",
        ]

    @pytest.mark.asyncio
    async def test_emit_preserves_complex_payload(
        self,
    ) -> None:
        """Complex nested payloads are stored faithfully."""
        payload: Dict[str, Any] = {
            "name": "search_manual",
            "input": {"query": "misfire", "top_k": 5},
            "nested": {"a": [1, 2, 3]},
        }
        mock_factory = _make_mock_db()
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            await session_log.emit_event(
                FAKE_SESSION_ID, "tool_call", payload,
                iteration=2,
            )

        added = mock_factory.return_value.add.call_args[0][0]
        assert added.payload == payload
        assert added.iteration == 2

    @pytest.mark.asyncio
    async def test_emit_default_iteration_zero(self) -> None:
        """Default iteration value is 0 when not specified."""
        mock_factory = _make_mock_db()
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            await session_log.emit_event(
                FAKE_SESSION_ID, "error",
                {"error_type": "timeout"},
            )

        added = mock_factory.return_value.add.call_args[0][0]
        assert added.iteration == 0

    @pytest.mark.asyncio
    async def test_emit_rollback_on_error(self) -> None:
        """DB errors trigger rollback and re-raise."""
        mock_factory = _make_mock_db()
        mock_factory.return_value.commit.side_effect = (
            RuntimeError("DB error")
        )

        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            with pytest.raises(RuntimeError, match="DB error"):
                await session_log.emit_event(
                    FAKE_SESSION_ID, "error",
                    {"msg": "test"},
                )

        mock_factory.return_value.rollback.assert_called_once()
        mock_factory.return_value.close.assert_called_once()


# ── Tests: get_session_events ──────────────────────────────────────


class TestGetSessionEvents:
    """Tests for ``get_session_events()`` retrieval."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_events(self) -> None:
        """No events returns an empty list."""
        mock_factory = _make_mock_db(query_rows=[])
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            result = await session_log.get_session_events(
                FAKE_SESSION_ID,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_events_in_order(self) -> None:
        """Events are returned with correct fields."""
        base_time = datetime(
            2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc,
        )
        rows = [
            FakeEventRow(
                event_id=uuid.uuid4(),
                session_id=FAKE_SESSION_ID,
                event_type="session_start",
                iteration=0,
                payload={"model": "m"},
                created_at=base_time,
            ),
            FakeEventRow(
                event_id=uuid.uuid4(),
                session_id=FAKE_SESSION_ID,
                event_type="tool_call",
                iteration=0,
                payload={"name": "detect"},
                created_at=base_time + timedelta(seconds=1),
            ),
            FakeEventRow(
                event_id=uuid.uuid4(),
                session_id=FAKE_SESSION_ID,
                event_type="tool_result",
                iteration=0,
                payload={"name": "detect", "output": "ok"},
                created_at=base_time + timedelta(seconds=2),
            ),
            FakeEventRow(
                event_id=uuid.uuid4(),
                session_id=FAKE_SESSION_ID,
                event_type="diagnosis_done",
                iteration=1,
                payload={"diagnosis": "all good"},
                created_at=base_time + timedelta(seconds=3),
            ),
        ]

        mock_factory = _make_mock_db(query_rows=rows)
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            events = await session_log.get_session_events(
                FAKE_SESSION_ID,
            )

        assert len(events) == 4
        types = [e["event_type"] for e in events]
        assert types == [
            "session_start", "tool_call",
            "tool_result", "diagnosis_done",
        ]

    @pytest.mark.asyncio
    async def test_event_dict_structure(self) -> None:
        """Returned dicts contain expected keys."""
        event_id = uuid.uuid4()
        created = datetime(
            2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc,
        )
        rows = [
            FakeEventRow(
                event_id=event_id,
                session_id=FAKE_SESSION_ID,
                event_type="error",
                iteration=3,
                payload={
                    "error_type": "llm_error",
                    "message": "fail",
                },
                created_at=created,
            ),
        ]

        mock_factory = _make_mock_db(query_rows=rows)
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            events = await session_log.get_session_events(
                FAKE_SESSION_ID,
            )

        assert len(events) == 1
        ev = events[0]
        assert set(ev.keys()) == {
            "id", "event_type", "iteration",
            "payload", "created_at",
        }
        assert ev["id"] == str(event_id)
        assert ev["event_type"] == "error"
        assert ev["iteration"] == 3
        assert ev["payload"]["error_type"] == "llm_error"
        assert ev["created_at"] == created.isoformat()

    @pytest.mark.asyncio
    async def test_session_close_called(self) -> None:
        """DB session is always closed after query."""
        mock_factory = _make_mock_db(query_rows=[])
        with patch.object(
            session_log, "SessionLocal", mock_factory,
        ):
            await session_log.get_session_events(
                FAKE_SESSION_ID,
            )
        mock_factory.return_value.close.assert_called_once()

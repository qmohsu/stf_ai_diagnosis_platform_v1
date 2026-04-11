"""Append-only event persistence for agent diagnosis sessions.

Provides ``emit_event()`` to write events and
``get_session_events()`` to retrieve them in chronological order.
All database operations use synchronous SQLAlchemy sessions
wrapped in ``run_in_executor`` for async compatibility.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, List

import structlog

from app.db.session import SessionLocal
from app.models_db import HarnessEventLog

logger = structlog.get_logger(__name__)

# Shared thread pool for DB writes (small — events are fast).
_executor = ThreadPoolExecutor(max_workers=2)


# ── Sync helpers (run in thread pool) ──────────────────────────────


def _sync_emit(
    session_id: uuid.UUID,
    event_type: str,
    payload: Dict[str, Any],
    iteration: int,
) -> None:
    """Persist a single event row (sync, blocking).

    Args:
        session_id: FK to ``obd_analysis_sessions``.
        event_type: One of the ``EventType`` literals.
        payload: JSONB event data.
        iteration: Agent loop iteration counter.
    """
    db = SessionLocal()
    try:
        event = HarnessEventLog(
            session_id=session_id,
            event_type=event_type,
            iteration=iteration,
            payload=payload,
        )
        db.add(event)
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "session_log_emit_failed",
            session_id=str(session_id),
            event_type=event_type,
            exc_info=True,
        )
        raise
    finally:
        db.close()


def _sync_get_events(
    session_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """Retrieve all events for a session, ordered by time (sync).

    Args:
        session_id: FK to ``obd_analysis_sessions``.

    Returns:
        List of dicts with id, event_type, iteration, payload,
        and created_at fields.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(HarnessEventLog)
            .filter(HarnessEventLog.session_id == session_id)
            .order_by(HarnessEventLog.created_at)
            .all()
        )
        return [
            {
                "id": str(row.id),
                "event_type": row.event_type,
                "iteration": row.iteration,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    finally:
        db.close()


# ── Public async API ───────────────────────────────────────────────


async def emit_event(
    session_id: uuid.UUID,
    event_type: str,
    payload: Dict[str, Any],
    iteration: int = 0,
) -> None:
    """Persist a harness event asynchronously.

    Offloads the blocking DB write to a thread pool so the
    async agent loop is not blocked.

    Args:
        session_id: OBD analysis session UUID.
        event_type: Event type string (see ``EventType``).
        payload: JSONB-serialisable event data.
        iteration: Current agent loop iteration (default 0).
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _executor,
        partial(
            _sync_emit, session_id, event_type,
            payload, iteration,
        ),
    )


async def get_session_events(
    session_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """Retrieve all events for a session in chronological order.

    Args:
        session_id: OBD analysis session UUID.

    Returns:
        Ordered list of event dicts.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        partial(_sync_get_events, session_id),
    )

"""Diagnosis event vocabulary (blueprint §3.7): one event, two consumers.

The names below ARE the ``audit_events.event_type`` values and the SSE
event names (PROD-11).  This tuple is the single source (FM-13): the
engine emits the first ten; ``waiting`` (PROD-11 D2) is written by the
diagnosis job before the engine starts (queued / model starting / GPU
busy …) and never by the engine itself.  ``EventSink`` collects a run's events in
order (``seq`` is per run) and can forward each one to an async callback
(the streaming consumer).  Sub-agent events carry
``parent_tool_call_id`` = the delegation tool call they belong to (FM-53).

Every event is also written as one structlog line (``agent.event``) with
its type, seq, sizes and ids — never its content (FM-21 / FM-52).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

SESSION_START = "session_start"
REASONING = "reasoning"
TOKEN = "token"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
HYPOTHESIS = "hypothesis"          # reserved (blueprint), never emitted yet
CONTEXT_COMPACT = "context_compact"
DIAGNOSIS_DONE = "diagnosis_done"
DONE = "done"
ERROR = "error"
WAITING = "waiting"                # PROD-11 D2: job-level, before the engine runs

EVENT_TYPES = (
    SESSION_START, REASONING, TOKEN, TOOL_CALL, TOOL_RESULT, HYPOTHESIS,
    CONTEXT_COMPACT, DIAGNOSIS_DONE, DONE, ERROR, WAITING,
)


@dataclass(frozen=True)
class AgentEvent:
    """One event of a diagnosis run.

    Attributes:
        seq: 1-based position within the run.
        event_type: One of ``EVENT_TYPES`` (= SSE name = audit event_type).
        payload: JSON-serialisable content; shape depends on the type.
        parent_tool_call_id: Delegation tool call this event happened
            inside (sub-agent events), else None.
        ts: UTC timestamp.
    """

    seq: int
    event_type: str
    payload: Dict[str, Any]
    parent_tool_call_id: Optional[str] = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict (``ts`` as ISO string)."""
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


EventCallback = Callable[[AgentEvent], Awaitable[None]]


class EventSink:
    """Ordered collector of a run's events with an optional async forward.

    ``emit`` is synchronous (usable from graph callbacks); forwarding to
    the callback is scheduled on the running loop and awaited by
    ``drain`` before the run returns, so callers never lose the tail.
    """

    def __init__(self, callback: Optional[EventCallback] = None) -> None:
        self.events: List[AgentEvent] = []
        self._callback = callback
        self._pending: List["asyncio.Task[None]"] = []

    def emit(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        parent_tool_call_id: Optional[str] = None,
    ) -> AgentEvent:
        """Append one event (and forward it) — returns the event."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        ev = AgentEvent(
            seq=len(self.events) + 1,
            event_type=event_type,
            payload=dict(payload or {}),
            parent_tool_call_id=parent_tool_call_id,
        )
        self.events.append(ev)
        logger.info(
            "agent.event",
            seq=ev.seq,
            event_type=event_type,
            parent_tool_call_id=parent_tool_call_id,
            tool=ev.payload.get("tool"),
            tool_call_id=ev.payload.get("tool_call_id"),
            chars=len(str(ev.payload.get("text", ""))) or None,
            duration_ms=ev.payload.get("duration_ms"),
            is_error=ev.payload.get("is_error"),
        )
        if self._callback is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                self._pending.append(loop.create_task(self._callback(ev)))
        return ev

    async def drain(self) -> None:
        """Wait for every forwarded event to be delivered."""
        pending, self._pending = self._pending, []
        for task in pending:
            try:
                await task
            except Exception as exc:  # noqa: BLE001 — a consumer bug must not kill the run
                logger.warning("agent.event_forward_failed", error=type(exc).__name__)

    def count(self, event_type: str) -> int:
        """Number of events of one type."""
        return sum(1 for e in self.events if e.event_type == event_type)

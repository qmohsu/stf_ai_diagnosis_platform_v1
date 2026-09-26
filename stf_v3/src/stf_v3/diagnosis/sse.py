"""Server-Sent Events for a conversation (PROD-11, blueprint §3.7).

The stream only READS the black box, so the API never shares memory with
the job and an API restart loses nothing; a replay is the same code
without the wait.  Frames::

    event: <event_type>
    id: <seq>
    data: <json {seq, event_type, payload, created_at}>

Rules (round-2 failure modes):

* the first bytes go out at once (``: connected``) so no proxy waits for
  headers while a diagnosis is still queued (FM-28), then ``: keepalive``
  every ``sse_keepalive_s`` of silence;
* each poll borrows a DB session and gives it back — a stream never holds
  a pool connection and never uses the request's session, which ends
  when the response starts (FM-36 / FM-53);
* only gapless seqs are sent: a missing seq is waited for up to
  ``sse_gap_wait_s`` (a late commit), then skipped with a log line (FM-19);
* the stream ends after sending ``done`` — NOT at ``error``, which a
  partial run sends before its report (FM-46) — or when the conversation
  is final and nothing new arrived in two polls (FM-12), or after
  ``sse_max_stream_s``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

import structlog
from pydantic_core import to_jsonable_python
from sqlalchemy import select

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.models import FINAL_STATUSES, AuditEvent, DiagnosisConversation
from stf_v3.diagnosis.store import read_events

log = structlog.get_logger(__name__)


def event_dict(row: AuditEvent) -> Dict[str, Any]:
    """The JSON object of one event — byte-identical in the JSON replay and
    the SSE ``data`` (same serializer as the response model)."""
    return {"seq": row.seq, "event_type": row.event_type, "payload": row.payload,
            "created_at": to_jsonable_python(row.created_at)}


def frame(row: AuditEvent) -> str:
    """One SSE frame."""
    data = json.dumps(event_dict(row), ensure_ascii=False, separators=(",", ":"))
    return f"event: {row.event_type}\nid: {row.seq}\ndata: {data}\n\n"


async def stream_events(
    conversation_id: uuid.UUID,
    after_seq: int,
    *,
    session_factory: Any,
    settings: Any,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Yields SSE text for events with ``seq > after_seq`` until the end."""
    yield ": connected\n\n"
    last = after_seq
    started = clock()
    last_sent = clock()
    gap_since: Optional[float] = None
    final_quiet_polls = 0
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        async with session_factory() as session:
            rows = await read_events(session, conversation_id, last, limit=200)
            status = (await session.execute(
                select(DiagnosisConversation.status).where(DiagnosisConversation.id == conversation_id)
            )).scalar_one_or_none()
        sent_any = False
        for row in rows:
            if row.seq != last + 1:
                now = clock()
                if gap_since is None:
                    gap_since = now
                if now - gap_since < settings.sse_gap_wait_s:
                    break
                log.warning("sse.gap_skipped", conversation_id=str(conversation_id), missing=last + 1,
                            got=row.seq)
            gap_since = None
            yield frame(row)
            last = row.seq
            sent_any = True
            last_sent = clock()
            if row.event_type == ev.DONE:
                return
        if not sent_any:
            if status is None or status in FINAL_STATUSES:
                final_quiet_polls += 1
                if final_quiet_polls >= 2:
                    return
            if clock() - last_sent >= settings.sse_keepalive_s:
                yield ": keepalive\n\n"
                last_sent = clock()
        else:
            final_quiet_polls = 0
        if clock() - started >= settings.sse_max_stream_s:
            return
        await sleep(settings.sse_poll_s)

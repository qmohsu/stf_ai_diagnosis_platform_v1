"""PROD-11 SSE generator edge cases (T-11): first bytes at once, keepalive,
a missing seq is waited for then skipped (FM-19), the stream ends at
``done`` and not at ``error`` (FM-46), a final conversation with nothing
new closes the stream (FM-12), and the maximum duration holds (FM-36).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any, List

from tests.conftest import requires_db
from tests.diagnosis_helpers import install_fake_queue, seed

pytestmark = requires_db

FAST = SimpleNamespace(sse_poll_s=0.02, sse_keepalive_s=0.1, sse_max_stream_s=1.5, sse_gap_wait_s=0.2)


async def _conv(client: Any, workshop_with_codes: Any, monkeypatch: Any) -> uuid.UUID:
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    r = await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                          json={"obd_log_id": str(s.log_id)})
    return uuid.UUID(r.json()["conversation_id"])


async def _append(cid: uuid.UUID, seqs_types: List[Any], status: str = "running") -> None:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    async with SessionLocal() as session:
        async with session.begin():
            conv = await store.lock_conversation(session, cid)
            conv.status = status
            for seq, t in seqs_types:
                store.add_events(session, cid, seq, [(t, {"n": seq})])


async def _collect(cid: uuid.UUID, after: int = 0) -> List[str]:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.sse import stream_events

    return [c async for c in stream_events(cid, after, session_factory=SessionLocal, settings=FAST)]


def _ids(chunks: List[str]) -> List[int]:
    return [int(c.split("\nid: ")[1].split("\n")[0]) for c in chunks if c.startswith("event:")]


async def test_first_bytes_keepalive_and_max_duration(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FM-28 / FM-36: ``: connected`` first; ``: keepalive`` during silence;
    an unfinished conversation's stream still ends at the maximum."""
    cid = await _conv(client, workshop_with_codes, monkeypatch)
    t0 = time.monotonic()
    chunks = await _collect(cid)
    took = time.monotonic() - t0
    assert chunks[0] == ": connected\n\n" and ": keepalive\n\n" in chunks
    assert _ids(chunks) == [1] and 1.4 <= took < 4


async def test_a_gap_is_waited_for_then_skipped(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FM-19: seq 3 missing → 1, 2 sent, then (after the gap wait) 4; the
    stream ends at ``done`` (5)."""
    cid = await _conv(client, workshop_with_codes, monkeypatch)
    await _append(cid, [(2, "session_start"), (4, "token"), (5, "done")])
    t0 = time.monotonic()
    chunks = await _collect(cid)
    assert _ids(chunks) == [1, 2, 4, 5] and time.monotonic() - t0 >= 0.2


async def test_error_does_not_end_the_stream_done_does(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FM-46: a partial run sends error → diagnosis_done → done; the client
    receives all three."""
    cid = await _conv(client, workshop_with_codes, monkeypatch)
    await _append(cid, [(2, "session_start"), (3, "error"), (4, "diagnosis_done"), (5, "done")], status="done")
    chunks = await _collect(cid)
    assert _ids(chunks) == [1, 2, 3, 4, 5] and chunks[-1].startswith("event: done")


async def test_a_final_conversation_without_done_still_closes(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FM-12: status final, no ``done`` row, nothing new → closes after two
    quiet polls (not at the maximum)."""
    cid = await _conv(client, workshop_with_codes, monkeypatch)
    await _append(cid, [(2, "session_start")], status="error")
    t0 = time.monotonic()
    chunks = await _collect(cid, after=2)
    assert _ids(chunks) == [] and time.monotonic() - t0 < 1.0

"""PROD-11 API tests: T-1 (ownership), T-2 (start / D4 / enqueue failure),
T-10 (cancel of queued and orphaned runs), T-11 (replay + SSE through the
API, expired token, 20 concurrent streams) and T-12 (no token in the URL).

The queue is replaced by ``FakeQueue`` (no worker runs in tests); the job
itself is covered by ``test_db_diagnosis_job.py``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List

import pytest

from tests.conftest import register_and_login, requires_db
from tests.diagnosis_helpers import (
    FAKE_VIN_B,
    FakeQueue,
    conversation_row,
    events_of,
    install_fake_queue,
    second_workshop,
    seed,
)

pytestmark = requires_db


async def _start(client: Any, s: Any) -> Any:
    return await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                             json={"obd_log_id": str(s.log_id)})


def _parse_sse(body: str) -> List[Dict[str, Any]]:
    frames = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        fields = dict(ln.split(": ", 1) for ln in lines)
        frames.append({"event": fields["event"], "id": int(fields["id"]), "data": json.loads(fields["data"])})
    return frames


async def _finish(cid: uuid.UUID, n_tokens: int = 3) -> None:
    """Writes a small finished run into the black box (as a job would)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    async with SessionLocal() as session:
        async with session.begin():
            conv = await store.lock_conversation(session, cid)
            conv.status = "running"
    items = [("session_start", {"vehicle": "x"})] + [("token", {"text": f"t{i}"}) for i in range(n_tokens)]
    writer = store.EventWriter(SessionLocal, cid, 2, flush_s=0.05).start()
    for t, p in items:
        writer.put(t, p)
    await writer.close()
    await store.finalize(SessionLocal, cid, status="done", report={
        "content_md": "# ok", "citations": [], "model": "test", "partial": False,
        "stopped_reason": "complete", "limitations": []},
        messages=[], tail=[("diagnosis_done", {"chars": 4}), ("done", {"stopped_reason": "complete"})],
        model="test")


# ── T-1 ownership ──────────────────────────────────────────────────────


async def test_other_workshops_get_404_on_every_diagnosis_endpoint(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-1 / FM-5 / FM-6: a member of another workshop sees none of the six
    endpoints (404, same as a missing id); a log of another vehicle or
    another workshop cannot be diagnosed; ids are UUIDs."""
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    a = await seed(client, wid, codes)
    r = await _start(client, a)
    assert r.status_code == 202, r.text
    cid = r.json()["conversation_id"]
    uuid.UUID(cid)
    wid_b, codes_b = await second_workshop()
    b = await seed(client, wid_b, codes_b, user="tech_b", vin=FAKE_VIN_B)
    h = b.headers
    checks = [
        ("post", f"/v3/vehicles/{a.vehicle_id}/diagnose", {"obd_log_id": str(a.log_id)}, "vehicle_not_found"),
        ("get", f"/v3/vehicles/{a.vehicle_id}/conversations", None, "vehicle_not_found"),
        ("get", f"/v3/conversations/{cid}", None, "conversation_not_found"),
        ("get", f"/v3/conversations/{cid}/events", None, "conversation_not_found"),
        ("get", f"/v3/conversations/{cid}/report", None, "conversation_not_found"),
        ("post", f"/v3/conversations/{cid}/cancel", None, "conversation_not_found"),
    ]
    for method, url, body, code in checks:
        resp = await getattr(client, method)(url, headers=h, **({"json": body} if body else {}))
        assert resp.status_code == 404 and resp.json()["code"] == code, (url, resp.text)
    sse = await client.get(f"/v3/conversations/{cid}/events", headers={**h, "Accept": "text/event-stream"})
    assert sse.status_code == 404
    # B's log on A's vehicle, and A's own second vehicle with B's log → log_not_found
    r = await client.post(f"/v3/vehicles/{a.vehicle_id}/diagnose", headers=a.headers,
                          json={"obd_log_id": str(b.log_id)})
    assert r.status_code == 404 and r.json()["code"] == "log_not_found"
    r = await client.post(f"/v3/vehicles/{a.vehicle_id}/diagnose", headers=a.headers,
                          json={"obd_log_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "log_not_found"


# ── T-2 start ──────────────────────────────────────────────────────────


async def test_enqueue_failure_closes_the_conversation_with_503(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-2 ① / FM-9: a failed defer answers 503 and the conversation ends as
    error ``queue_unavailable`` with error + done events (never left queued)."""
    install_fake_queue(monkeypatch, FakeQueue(fail_defer=True))
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    r = await _start(client, s)
    assert r.status_code == 503 and r.json()["code"] == "queue_unavailable"
    lst = await client.get(f"/v3/vehicles/{s.vehicle_id}/conversations", headers=s.headers)
    (conv,) = lst.json()
    assert conv["status"] == "error" and conv["error_code"] == "queue_unavailable"
    kinds = [t for _, t, _ in await events_of(uuid.UUID(conv["id"]))]
    assert kinds == ["waiting", "error", "done"]


async def test_ten_concurrent_clicks_create_one_conversation(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-2 ② / D4 / FM-15: ten simultaneous clicks → exactly one
    conversation; one 202, nine 200 with ``existing: true`` and the same id."""
    queue = install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    rs = await asyncio.gather(*[_start(client, s) for _ in range(10)])
    codes_seen = sorted(r.status_code for r in rs)
    assert codes_seen == [200] * 9 + [202], [r.text for r in rs]
    ids = {r.json()["conversation_id"] for r in rs}
    assert len(ids) == 1 and len(queue.deferred) == 1
    assert all(r.json()["existing"] for r in rs if r.status_code == 200)
    lst = await client.get(f"/v3/vehicles/{s.vehicle_id}/conversations", headers=s.headers)
    assert len(lst.json()) == 1
    (_, first, payload), = (await events_of(uuid.UUID(ids.pop())))
    assert first == "waiting" and payload["reason"] == "queued" and "message" in payload


async def test_a_finished_diagnosis_frees_the_vehicle_for_a_new_one(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """D4: after the first conversation ends, the same log can be diagnosed
    again (a new conversation); the history lists both, newest first."""
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    first = (await _start(client, s)).json()["conversation_id"]
    await _finish(uuid.UUID(first))
    r = await _start(client, s)
    assert r.status_code == 202 and r.json()["conversation_id"] != first
    lst = (await client.get(f"/v3/vehicles/{s.vehicle_id}/conversations", headers=s.headers)).json()
    assert [c["id"] for c in lst][1] == first and lst[1]["has_report"] is True


# ── report ─────────────────────────────────────────────────────────────


async def test_report_is_not_ready_then_readable(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """404 ``report_not_ready`` while queued; the full report (with the
    partial flag and stop reason) once finished."""
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = (await _start(client, s)).json()["conversation_id"]
    r = await client.get(f"/v3/conversations/{cid}/report", headers=s.headers)
    assert r.status_code == 404 and r.json()["code"] == "report_not_ready"
    await _finish(uuid.UUID(cid))
    r = await client.get(f"/v3/conversations/{cid}/report", headers=s.headers)
    assert r.status_code == 200 and r.json()["partial"] is False and r.json()["stopped_reason"] == "complete"
    d = (await client.get(f"/v3/conversations/{cid}", headers=s.headers)).json()
    assert d["status"] == "done" and d["has_report"] and d["messages"] is None


# ── T-10 cancel (API side) ─────────────────────────────────────────────


async def test_cancel_queued_and_orphan_and_finished(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-10 / FM-11: queued → cancelled at once (+ done, job cancelled);
    running whose job is gone → cancelled at once; finished → 409."""
    queue = install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = (await _start(client, s)).json()["conversation_id"]
    r = await client.post(f"/v3/conversations/{cid}/cancel", headers=s.headers)
    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert queue.cancelled == [1000]
    assert [t for _, t, _ in await events_of(uuid.UUID(cid))] == ["waiting", "done"]
    r = await client.post(f"/v3/conversations/{cid}/cancel", headers=s.headers)
    assert r.status_code == 409 and r.json()["code"] == "conversation_finished"
    # orphan: running, job no longer exists
    cid2 = (await _start(client, s)).json()["conversation_id"]
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    async with SessionLocal() as session:
        async with session.begin():
            conv = await store.lock_conversation(session, uuid.UUID(cid2))
            conv.status = "running"
    queue.statuses.clear()
    r = await client.post(f"/v3/conversations/{cid2}/cancel", headers=s.headers)
    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert (await conversation_row(uuid.UUID(cid2))).status == "cancelled"


async def test_cancel_of_a_live_run_only_sets_the_flag(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-10: running with a live job → ``cancel_requested`` only; the job
    ends it (see the job tests)."""
    queue = install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = uuid.UUID((await _start(client, s)).json()["conversation_id"])
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    async with SessionLocal() as session:
        async with session.begin():
            (await store.lock_conversation(session, cid)).status = "running"
    queue.statuses[1000] = "doing"
    r = await client.post(f"/v3/conversations/{cid}/cancel", headers=s.headers)
    assert r.status_code == 202 and r.json() == {"conversation_id": str(cid), "status": "running",
                                                 "cancel_requested": True}
    row = await conversation_row(cid)
    assert row.status == "running" and row.cancel_requested is True


# ── T-11 replay and SSE through the API ────────────────────────────────


async def test_replay_and_sse_carry_the_same_events(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-11: JSON replay and the SSE stream list the same events; SSE starts
    with a comment at once, frames are event/id/data, the stream ends after
    ``done``; ``after_seq`` and ``Last-Event-ID`` send only the rest."""
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = (await _start(client, s)).json()["conversation_id"]
    await _finish(uuid.UUID(cid))
    replay = (await client.get(f"/v3/conversations/{cid}/events", headers=s.headers)).json()
    assert [e["seq"] for e in replay] == list(range(1, len(replay) + 1))
    assert replay[-1]["event_type"] == "done" and replay[-2]["payload"]["report_id"]
    sse = await client.get(f"/v3/conversations/{cid}/events", headers={**s.headers, "Accept": "text/event-stream"})
    assert sse.status_code == 200 and sse.headers["content-type"].startswith("text/event-stream")
    assert sse.text.startswith(": connected\n\n")
    frames = _parse_sse(sse.text)
    assert [f["data"] for f in frames] == replay
    assert all(f["event"] == f["data"]["event_type"] and f["id"] == f["data"]["seq"] for f in frames)
    tail = _parse_sse((await client.get(f"/v3/conversations/{cid}/events?after_seq=4",
                                        headers={**s.headers, "Accept": "text/event-stream"})).text)
    assert [f["id"] for f in tail] == [e["seq"] for e in replay if e["seq"] > 4]
    tail2 = _parse_sse((await client.get(f"/v3/conversations/{cid}/events",
                                         headers={**s.headers, "Accept": "text/event-stream",
                                                  "Last-Event-ID": "5"})).text)
    assert [f["id"] for f in tail2] == [e["seq"] for e in replay if e["seq"] > 5]


async def test_expired_token_and_token_in_url_are_refused(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-11 / T-12 / FM-8 / FM-37: an expired token → 401 (refresh, then
    reconnect); a token in the URL is not accepted."""
    import jwt

    from stf_v3.settings import settings

    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = (await _start(client, s)).json()["conversation_id"]
    me = (await client.get("/v3/users/me", headers=s.headers)).json()
    expired = jwt.encode({"sub": me["id"], "aud": ["fastapi-users:auth"], "exp": int(time.time()) - 60},
                         settings.jwt_secret, algorithm="HS256")
    r = await client.get(f"/v3/conversations/{cid}/events",
                         headers={"Authorization": f"Bearer {expired}", "Accept": "text/event-stream"})
    assert r.status_code == 401
    token = s.headers["Authorization"].split(" ", 1)[1]
    for q in ("access_token", "token"):
        r = await client.get(f"/v3/conversations/{cid}/events?{q}={token}", headers={"Accept": "text/event-stream"})
        assert r.status_code == 401


async def test_twenty_streams_do_not_starve_the_pool(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-11 / FM-36: 20 streams on an unfinished conversation run while
    other endpoints still answer quickly (each poll borrows a connection
    and gives it back; the pool holds 5 + overflow)."""
    from stf_v3.settings import settings

    install_fake_queue(monkeypatch)
    monkeypatch.setattr(settings, "sse_max_stream_s", 2)
    monkeypatch.setattr(settings, "sse_poll_s", 0.05)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    cid = (await _start(client, s)).json()["conversation_id"]
    streams = [client.get(f"/v3/conversations/{cid}/events", headers={**s.headers, "Accept": "text/event-stream"})
               for _ in range(20)]

    async def other() -> float:
        await asyncio.sleep(0.5)
        t0 = time.monotonic()
        r = await client.get(f"/v3/vehicles/{s.vehicle_id}/conversations", headers=s.headers)
        assert r.status_code == 200
        return time.monotonic() - t0

    *results, took = await asyncio.wait_for(asyncio.gather(*streams, other()), timeout=30)
    assert all(r.status_code == 200 and r.text.startswith(": connected") for r in results)
    assert took < 2.0

"""PROD-15A ② T-14 / T-15 / T-16 / T-17 (database halves): the maintenance SQL.

The host maintenance (``scripts/backup.py``) sends these exact statements
to the V3 database as its owner; here they run against the CI Postgres
(a superuser there) with real conversations, events and queue jobs, the
clock being "moved forward" by backdating rows.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any, List

import pytest

from tests.conftest import requires_db
from tests.diagnosis_helpers import seed
from tests.test_backup_script import bk

pytestmark = requires_db


def _sync_url() -> str:
    return os.environ["STF_V3_TEST_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")


def _q(sql: str, *params: Any) -> List[Any]:
    import psycopg

    with psycopg.connect(_sync_url(), autocommit=True) as conn:
        cur = conn.execute(sql, params or None)
        return cur.fetchall() if cur.description else []


def _conversation(s: Any, status: str, finished_days_ago: float | None, events: int) -> str:
    user = _q("SELECT id FROM users ORDER BY created_at LIMIT 1")[0][0]
    finished = None if finished_days_ago is None else \
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=finished_days_ago)
    cid = _q("INSERT INTO diagnosis_conversations (vehicle_id, obd_log_id, created_by, status, "
             "finished_at, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, "
             "now() - interval '200 days', now() - interval '200 days') RETURNING id",
             s.vehicle_id, s.log_id, user, status, finished)[0][0]
    _q("INSERT INTO audit_events (conversation_id, seq, event_type, payload) "
       "SELECT %s, g, 'tool_call', jsonb_build_object('n', g) FROM generate_series(1, %s) g", cid, events)
    return str(cid)


def _export(ids: List[str]) -> bytes:
    """What ``\\copy … TO file`` writes (same SELECT, COPY text format)."""
    import psycopg

    sql = bk.archive_export_sql(ids, "/unused").split("\\copy ", 1)[1].rsplit(" TO '", 1)[0]
    with psycopg.connect(_sync_url()) as conn:
        with conn.cursor().copy(f"COPY {sql} TO STDOUT") as cp:
            return b"".join(bytes(chunk) for chunk in cp)


async def test_archive_selects_moves_and_marks_only_old_finished_conversations(
        client: Any, workshop_with_codes: Any) -> None:
    """T-14 / FM-4 / FM-43, T-15 / FM-62, T-16 / FM-42: of three conversations
    (finished 200 days ago, unfinished 200 days old, finished 10 days ago) only
    the first is archived; a delete whose count differs rolls back; the runtime
    role still cannot delete events; the API says `events_archived` and the
    replay answers 410; importing the archive back restores the replay."""
    import psycopg

    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    old = _conversation(s, "done", 200, 5)
    _conversation(s, "running", None, 3)
    recent = _conversation(s, "done", 10, 2)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=180)

    ids = [str(r[0]) for r in _q(bk.archive_candidates_sql(cutoff, 500))]
    assert ids == [old]
    assert _q(bk.archive_count_sql(ids))[0][0] == 5
    archive = _export(ids)
    assert len(archive.splitlines()) == 5

    with pytest.raises(psycopg.errors.RaiseException, match="archive: 5 rows to delete, 4 exported"):
        _q(bk.archive_delete_sql(ids, 4))                     # FM-4: all or nothing
    assert _q("SELECT count(*) FROM audit_events WHERE conversation_id = %s", old)[0][0] == 5

    _q(bk.archive_delete_sql(ids, 5))
    assert _q("SELECT count(*) FROM audit_events WHERE conversation_id = %s", old)[0][0] == 0
    assert _q("SELECT events_archived_at IS NOT NULL FROM diagnosis_conversations WHERE id = %s", old)[0][0]
    assert _q(bk.archive_candidates_sql(cutoff, 500)) == []    # a second run finds nothing

    # T-15 / FM-62: the black box stays append-only for the runtime role.
    with psycopg.connect(_sync_url(), autocommit=True) as conn:
        conn.execute("SET ROLE stf_v3_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_events WHERE conversation_id = %s", (recent,))

    # T-16 / FM-42: the marker and the replay's answer.
    detail = (await client.get(f"/v3/conversations/{old}", headers=s.headers)).json()
    assert detail["events_archived"] is True and detail["events_archived_at"]
    r = await client.get(f"/v3/conversations/{old}/events", headers=s.headers)
    assert r.status_code == 410 and r.json()["code"] == "events_archived"
    r = await client.get(f"/v3/conversations/{old}/events", headers={**s.headers, "Accept": "text/event-stream"})
    assert r.status_code == 410
    r = await client.get(f"/v3/conversations/{recent}/events", headers=s.headers)
    assert r.status_code == 200 and len(r.json()) == 2
    listed = (await client.get(f"/v3/vehicles/{s.vehicle_id}/conversations", headers=s.headers)).json()
    assert {c["id"]: c["events_archived"] for c in listed}[recent] is False

    # T-15: the archive imports back (runbook §7) and the replay is whole again.
    with psycopg.connect(_sync_url()) as conn:
        with conn.cursor().copy("COPY audit_events FROM STDIN") as cp:
            cp.write(archive)
        conn.execute("UPDATE diagnosis_conversations SET events_archived_at = NULL WHERE id = %s", (old,))
    r = await client.get(f"/v3/conversations/{old}/events", headers=s.headers)
    assert r.status_code == 200 and [e["payload"]["n"] for e in r.json()] == [1, 2, 3, 4, 5]


def test_queue_cleanup_deletes_only_old_succeeded_and_cancelled_jobs(migrated_db: str) -> None:
    """T-17 / FM-39: 40-day-old succeeded / cancelled go; failed stays (kept
    for diagnosis), a job a periodic-defer row points at stays, a recent one
    stays; no foreign-key error."""
    def job(status: str, days_ago: float) -> int:
        jid = _q("INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
                 "VALUES ('default', 'ci.probe', '{}', %s) RETURNING id", status)[0][0]
        _q("INSERT INTO procrastinate_events (job_id, type, at) VALUES (%s, %s, now())",
           jid, status if status in ("succeeded", "cancelled", "failed") else "deferred")
        _q("UPDATE procrastinate_events SET at = now() - make_interval(secs => %s) WHERE job_id = %s",
           days_ago * 86400, jid)
        return int(jid)

    s_old, c_old, f_old = job("succeeded", 40), job("cancelled", 40), job("failed", 40)
    s_ref, s_new = job("succeeded", 40), job("succeeded", 1)
    _q("INSERT INTO procrastinate_periodic_defers (task_name, defer_timestamp, job_id, periodic_id) "
       "VALUES ('ci.probe', 1, %s, '')", s_ref)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)

    candidates = {r[0] for r in _q(bk.queue_old_jobs_sql(cutoff))}
    assert {s_old, c_old} <= candidates and not ({f_old, s_ref, s_new} & candidates)
    assert _q(bk.queue_count_sql(cutoff))[0][0] == len(candidates)
    assert _q(bk.queue_delete_sql(cutoff))[0][0] == len(candidates)
    left = {r[0] for r in _q("SELECT id FROM procrastinate_jobs WHERE id = ANY(%s)",
                             [s_old, c_old, f_old, s_ref, s_new])}
    assert left == {f_old, s_ref, s_new}
    assert _q("SELECT count(*) FROM procrastinate_events WHERE job_id = ANY(%s)", [s_old, c_old])[0][0] == 0
    _q("DELETE FROM procrastinate_periodic_defers WHERE job_id = %s", s_ref)
    _q("DELETE FROM procrastinate_jobs WHERE id = ANY(%s)", [f_old, s_ref, s_new])


"""PROD-15A T-6 / T-8 (database halves): the backup role and restore finalize.

Run against the CI throwaway Postgres (its login is a superuser there).

* T-6 / FM-16: ``ops_roles.sql`` is idempotent and makes ``stf_v3_backup``
  a password-less, read-only reader of every table.
* T-8 / FM-9: ``restore_finalize.sql`` cancels queued jobs, fails running
  ones and resets the model controller; the diagnosis sweeper then closes
  the orphaned conversation as ``diagnosis_interrupted``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import os
import pathlib
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import requires_db
from tests.diagnosis_helpers import install_fake_queue, seed

pytestmark = requires_db

_SQL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "sql"


def _sync_url() -> str:
    return os.environ["STF_V3_TEST_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")


def _exec(sql: str) -> None:
    import psycopg

    with psycopg.connect(_sync_url(), autocommit=True) as conn:
        conn.execute(sql)


def test_backup_role_is_passwordless_read_only_and_idempotent(migrated_db: str) -> None:
    """T-6 / FM-16 / FM-24."""
    import psycopg

    sql = (_SQL / "ops_roles.sql").read_text()
    _exec(sql)
    _exec(sql)                                                    # idempotent
    with psycopg.connect(_sync_url(), autocommit=True) as conn:
        row = conn.execute("SELECT rolcanlogin, rolsuper, rolconnlimit, rolpassword IS NULL "
                           "FROM pg_authid WHERE rolname = 'stf_v3_backup'").fetchone()
        assert row == (True, False, 4, True)
        assert conn.execute(
            "SELECT pg_has_role('stf_v3_backup', 'pg_read_all_data', 'member')").fetchone()[0]
        cfg = conn.execute("SELECT setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                           "WHERE r.rolname = 'stf_v3_backup'").fetchone()[0]
        assert "default_transaction_read_only=on" in cfg
        conn.execute("SET ROLE stf_v3_backup")
        try:
            conn.execute("SELECT count(*) FROM users").fetchone()
            conn.execute("SELECT count(*) FROM procrastinate_jobs").fetchone()
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE model_service_state SET state = 'stopped'")
        finally:
            conn.execute("RESET ROLE")


async def test_restore_finalize_stops_restored_work(
    client: Any, workshop_with_codes: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-8 / FM-9: queued → cancelled, running → failed, controller reset,
    then the sweeper closes the conversation as interrupted."""
    from stf_v3.db import SessionLocal, engine
    from stf_v3.diagnosis import store

    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    r = await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                          json={"obd_log_id": str(s.log_id)})
    assert r.status_code == 202, r.text
    cid = uuid.UUID(r.json()["conversation_id"])

    async with engine.begin() as conn:
        todo = (await conn.execute(text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
            "VALUES ('diagnosis', 'diagnosis.run', '{}', 'todo') RETURNING id"))).scalar_one()
        doing = (await conn.execute(text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
            "VALUES ('diagnosis', 'diagnosis.run', '{}', 'doing') RETURNING id"))).scalar_one()
        await conn.execute(text("UPDATE diagnosis_conversations SET status = 'running', job_id = :j "
                                "WHERE id = :c"), {"j": doing, "c": cid})
        await conn.execute(text(
            "INSERT INTO model_service_state (id, state, started_by_us) VALUES (1, 'ready', true) "
            "ON CONFLICT (id) DO UPDATE SET state = 'ready', started_by_us = true"))

    _exec((_SQL / "restore_finalize.sql").read_text())

    async with engine.connect() as conn:
        st = dict((await conn.execute(text(
            "SELECT id, status::text FROM procrastinate_jobs WHERE id IN (:a, :b)"),
            {"a": todo, "b": doing})).all())
        model = (await conn.execute(text(
            "SELECT state, started_by_us FROM model_service_state WHERE id = 1"))).one()
    assert st == {todo: "cancelled", doing: "failed"}
    assert tuple(model) == ("stopped", False)

    async def job_status(job_id: int) -> Any:
        async with engine.connect() as conn:
            return (await conn.execute(text("SELECT status::text FROM procrastinate_jobs WHERE id = :i"),
                                       {"i": job_id})).scalar_one_or_none()

    counts = await store.sweep(SessionLocal, job_status, stale_s=300, locale="en")
    assert counts["job_finished"] == 1
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, error_code FROM diagnosis_conversations WHERE id = :c"), {"c": cid})).one()
    assert tuple(row) == ("error", "diagnosis_interrupted")

"""PROD-15A T-24 / T-22 (CI halves): the eval read-only role and connection limits.

Run against the CI throwaway Postgres (its login is a superuser there; the
workflow creates ``stf_v3_app``).

* T-24 / FM-12, FM-13, FM-15: ``stf_v3_eval`` reads the manual catalog and
  nothing else, cannot write, and every query the eval makes works under it.
* T-22 / FM-45, FM-60: ``stf_v3_app`` gets its limit; a non-superuser role
  at its limit is refused one more connection while another role still
  connects and writes.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import os
import pathlib
from typing import Any, List
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.conftest import requires_db

pytestmark = requires_db

_SQL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "sql"


def _url(user: str | None = None, password: str | None = None, sync: bool = True) -> str:
    base = os.environ["STF_V3_TEST_DATABASE_URL"]
    u = urlsplit(base)
    netloc = u.netloc
    if user:
        netloc = f"{user}:{password}@{u.hostname}:{u.port or 5432}"
    url = urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))
    return url.replace("postgresql+psycopg:", "postgresql:") if sync else url


def _exec(sql: str) -> None:
    import psycopg

    with psycopg.connect(_url(), autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture()
def ops_roles(migrated_db: str) -> None:
    """ops_roles.sql applied (twice: idempotent) + a CI-only eval password."""
    sql = (_SQL / "ops_roles.sql").read_text()
    _exec(sql)
    _exec(sql)
    _exec("ALTER ROLE stf_v3_eval PASSWORD 'ci-eval'")


def test_the_role_shapes_after_ops_roles(ops_roles: None) -> None:
    """FM-45 / FM-12: app limit 50; eval is a non-superuser, read-only, limit 10."""
    import psycopg

    with psycopg.connect(_url(), autocommit=True) as conn:
        rows = dict((r[0], r[1:]) for r in conn.execute(
            "SELECT rolname, rolsuper, rolconnlimit FROM pg_roles "
            "WHERE rolname IN ('stf_v3_app', 'stf_v3_eval')").fetchall())
        cfg = conn.execute("SELECT setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                           "WHERE r.rolname = 'stf_v3_eval'").fetchone()[0]
    assert rows == {"stf_v3_app": (False, 50), "stf_v3_eval": (False, 10)}
    assert "default_transaction_read_only=on" in cfg


def test_the_eval_role_reads_only_the_manual_catalog(ops_roles: None) -> None:
    """FM-12: manuals readable; accounts / vehicles / codes are not; no write,
    not even after switching the transaction to read-write."""
    import psycopg

    with psycopg.connect(_url("stf_v3_eval", "ci-eval"), autocommit=True) as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "stf_v3_eval"
        conn.execute("SELECT count(*) FROM manuals").fetchone()
        for table in ("users", "vehicles", "invite_codes", "vehicle_devices", "audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM manuals")
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM manuals")


async def test_every_eval_query_runs_under_the_eval_role(ops_roles: None) -> None:
    """FM-13: the eval's only database read (the manual inventory) works with
    the read-only role, so a later schema change that needs more is caught here."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from stf_v3.diagnosis.agent.bootstrap import load_manual_inventory

    engine = create_async_engine(_url("stf_v3_eval", "ci-eval", sync=False))
    try:
        async with AsyncSession(engine) as session:
            inventory = await load_manual_inventory(session)
    finally:
        await engine.dispose()
    assert isinstance(inventory, list)


def test_a_role_at_its_limit_is_refused_while_others_still_connect(migrated_db: str) -> None:
    """FM-60: the limit really applies to a non-superuser role (it is silently
    ignored for superusers); FM-59: another role still connects and writes."""
    import psycopg

    _exec("DROP ROLE IF EXISTS ci_limit_probe")
    _exec("CREATE ROLE ci_limit_probe LOGIN PASSWORD 'probe' NOSUPERUSER CONNECTION LIMIT 2")
    held: List[Any] = []
    try:
        for _ in range(2):
            held.append(psycopg.connect(_url("ci_limit_probe", "probe"), autocommit=True))
        with pytest.raises(psycopg.OperationalError, match="too many connections for role"):
            psycopg.connect(_url("ci_limit_probe", "probe"))
        with psycopg.connect(_url(), autocommit=True) as other:
            other.execute("CREATE TEMP TABLE t_limit_probe (x int)")
            other.execute("INSERT INTO t_limit_probe VALUES (1)")
            assert other.execute("SELECT count(*) FROM t_limit_probe").fetchone()[0] == 1
    finally:
        for conn in held:
            conn.close()
        _exec("DROP ROLE IF EXISTS ci_limit_probe")

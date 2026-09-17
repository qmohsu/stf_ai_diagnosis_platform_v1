"""Round-trip test for the migration chain against a real Postgres.

Skipped unless ``STF_V3_TEST_DATABASE_URL`` points at an EMPTY throwaway
database whose owner role has already been created by
``scripts/create_database.sh`` (needs the ``stf_v3_app`` role to exist).
Runs on the PolyU server inside the V3 container; never against the real
``stf_v3`` database.

Author: Xiangzhu Yan
"""

import os
import pathlib

import pytest
import sqlalchemy as sa

_URL = os.environ.get("STF_V3_TEST_DATABASE_URL")
_ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not _URL, reason="STF_V3_TEST_DATABASE_URL not set"
)


def _alembic_config():  # type: ignore[no-untyped-def]
    """Builds an Alembic config bound to the test database."""
    os.environ["STF_V3_DATABASE_URL"] = _URL or ""
    from alembic.config import Config

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    return cfg


def _business_tables(engine: sa.Engine) -> set:
    """Returns non-procrastinate, non-alembic table names."""
    names = set(sa.inspect(engine).get_table_names())
    return {n for n in names if not n.startswith("procrastinate_")} - {
        "alembic_version"
    }


def _truncate_business(engine: sa.Engine) -> None:
    """Empties every business table so a downgrade never trips a CHECK that
    an earlier (API) test's rows would violate — the test must not depend
    on which tests ran before it (PROD-08: a stored ``maxlog`` row broke
    the downgrade past ``b2c3d4e5f6a7``)."""
    from tests.conftest import _BUSINESS_TABLES

    tables = [t for t in _BUSINESS_TABLES if t in _business_tables(engine)]
    if not tables:
        return
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


def test_upgrade_check_downgrade_roundtrip() -> None:
    """upgrade head builds every table, `alembic check` finds no drift
    between models and DB, downgrade base removes everything again."""
    from alembic import command

    from stf_v3.metadata import EXPECTED_TABLES

    cfg = _alembic_config()
    engine = sa.create_engine(_URL or "")
    try:
        command.upgrade(cfg, "head")
        assert _business_tables(engine) == set(EXPECTED_TABLES)
        assert any(
            t.startswith("procrastinate_")
            for t in sa.inspect(engine).get_table_names()
        )
        command.check(cfg)  # raises if models and DB differ
        _truncate_business(engine)
        command.downgrade(cfg, "base")
        assert _business_tables(engine) == set()
        assert not any(
            t.startswith("procrastinate_")
            for t in sa.inspect(engine).get_table_names()
        )
        command.upgrade(cfg, "head")  # idempotent re-apply
        assert _business_tables(engine) == set(EXPECTED_TABLES)
    finally:
        engine.dispose()

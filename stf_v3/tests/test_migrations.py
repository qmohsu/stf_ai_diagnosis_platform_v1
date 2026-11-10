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


def test_logger_tz_migration_moves_stored_times_and_back() -> None:
    """Issue #257: ``e5a9c3f1d8b4`` moves every stored recording time 8 hours
    earlier (old UTC reading → HK reading), leaves ``uploaded_at`` and NULL
    times alone, and its downgrade restores the old values exactly."""
    from alembic import command

    cfg = _alembic_config()
    engine = sa.create_engine(_URL or "")
    old_start = "2026-11-10 15:37:11+00"  # what the old code stored
    try:
        # Arrange: schema just before the fix, one log with times, one without.
        command.upgrade(cfg, "head")
        _truncate_business(engine)
        command.downgrade(cfg, "d7f3b9a2c5e1")
        with engine.begin() as conn:
            wid = conn.execute(sa.text(
                "INSERT INTO workshops (name) VALUES ('tz-mig') RETURNING id"
            )).scalar_one()
            vid = conn.execute(sa.text(
                "INSERT INTO vehicles (workshop_id, vin, manufacturer, model) "
                "VALUES (:w, '1HGCM82633A123456', 'Honda', 'Jazz') RETURNING id"
            ), {"w": wid}).scalar_one()
            for sha, start in (("a" * 64, old_start), ("b" * 64, None)):
                conn.execute(sa.text(
                    "INSERT INTO obd_logs (vehicle_id, sha256, raw_path, source,"
                    " format, size_bytes, recorded_start, recorded_end, uploaded_at)"
                    " VALUES (:v, :s, 'x', 'device', 'maxlog', 1, :t, :t,"
                    " '2026-11-10 08:00:06+00')"
                ), {"v": vid, "s": sha, "t": start})
        q = sa.text(
            "SELECT sha256, recorded_start, recorded_end, uploaded_at"
            " FROM obd_logs ORDER BY sha256"
        )

        # Act
        command.upgrade(cfg, "e5a9c3f1d8b4")
        with engine.connect() as conn:
            moved = conn.execute(q).all()
        command.downgrade(cfg, "d7f3b9a2c5e1")
        with engine.connect() as conn:
            restored = conn.execute(q).all()

        # Assert
        with engine.connect() as conn:
            want = conn.execute(sa.text(
                "SELECT CAST(:t AS timestamptz) - interval '8 hours',"
                " CAST(:t AS timestamptz), CAST(:u AS timestamptz)"
            ), {"t": old_start, "u": "2026-11-10 08:00:06+00"}).one()
        assert moved[0][1] == want[0] and moved[0][2] == want[0]
        assert moved[0][3] == want[2]  # server clock untouched
        assert moved[1][1] is None and moved[1][2] is None
        assert restored[0][1] == want[1] and restored[0][2] == want[1]
    finally:
        _truncate_business(engine)
        command.upgrade(cfg, "head")
        engine.dispose()

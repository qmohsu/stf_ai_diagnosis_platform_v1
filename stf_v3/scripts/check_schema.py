#!/usr/bin/env python3
"""PROD-02 acceptance checks against a migrated ``stf_v3`` database.

Checks (each prints PASS/FAIL; exit code 1 if any fails):

1. tables   -- every expected table exists and its columns match the
               SQLAlchemy models (no missing / extra columns).
2. invariant-- inserting an ``obd_logs`` row without ``vehicle_id`` is
               rejected by the database (data-ownership invariant, D8).
3. append   -- the runtime role cannot UPDATE / DELETE ``audit_events``
               (append-only black box).  Needs ``--app-url``.
4. alembic  -- exactly one head and the database is at it.

Usage::

    python scripts/check_schema.py --url postgresql+psycopg://stf_v3:pw@h/db \
        --app-url postgresql+psycopg://stf_v3_app:pw@h/db

Author: Xiangzhu Yan
"""

import argparse
import os
import sys
from typing import List, Optional

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError, IntegrityError

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from stf_v3.metadata import EXPECTED_TABLES, metadata  # noqa: E402


def _report(name: str, ok: bool, detail: str = "") -> bool:
    """Prints one check line.

    Args:
        name: Check name.
        ok: Whether it passed.
        detail: Optional explanation.

    Returns:
        ``ok`` unchanged, for accumulation.
    """
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    return ok


def check_tables(engine: sa.Engine) -> bool:
    """Compares reflected tables/columns with the model metadata."""
    insp = sa.inspect(engine)
    present = {t for t in insp.get_table_names() if not t.startswith("procrastinate_")}
    present.discard("alembic_version")
    problems: List[str] = []
    missing = EXPECTED_TABLES - present
    extra = present - EXPECTED_TABLES
    if missing:
        problems.append(f"missing tables {sorted(missing)}")
    if extra:
        problems.append(f"unexpected tables {sorted(extra)}")
    for name in sorted(EXPECTED_TABLES & present):
        db_cols = {c["name"] for c in insp.get_columns(name)}
        model_cols = {c.name for c in metadata.tables[name].columns}
        if db_cols != model_cols:
            problems.append(
                f"{name}: db-only {sorted(db_cols - model_cols)} "
                f"model-only {sorted(model_cols - db_cols)}"
            )
    return _report("tables match models", not problems, "; ".join(problems))


def check_invariant(engine: sa.Engine) -> bool:
    """Verifies obd_logs.vehicle_id NOT NULL is enforced by the database."""
    stmt = sa.text(
        "INSERT INTO obd_logs (vehicle_id, sha256, raw_path, source, format, "
        "size_bytes) VALUES (NULL, repeat('0', 64), '/x', 'web', 'tsv', 1)"
    )
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(stmt)
        except IntegrityError as exc:
            trans.rollback()
            ok = "vehicle_id" in str(exc.orig) and "null" in str(exc.orig).lower()
            return _report("obd_logs without vehicle_id rejected", ok, str(exc.orig).splitlines()[0])
        trans.rollback()
    return _report("obd_logs without vehicle_id rejected", False, "insert succeeded")


def check_append_only(app_url: Optional[str]) -> bool:
    """Verifies the runtime role cannot UPDATE/DELETE audit_events."""
    if not app_url:
        return _report("audit_events append-only", False, "--app-url not given")
    engine = sa.create_engine(app_url)
    outcomes: List[str] = []
    ok = True
    for sql in (
        "UPDATE audit_events SET seq = seq WHERE false",
        "DELETE FROM audit_events WHERE false",
    ):
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(sa.text(sql))
                ok = False
                outcomes.append(f"allowed: {sql.split()[0]}")
            except DBAPIError as exc:
                if "permission denied" not in str(exc.orig):
                    ok = False
                outcomes.append(f"{sql.split()[0]}: {str(exc.orig).splitlines()[0]}")
            finally:
                trans.rollback()
    with engine.connect() as conn:
        try:
            conn.execute(sa.text("SELECT count(*) FROM audit_events"))
            outcomes.append("SELECT ok")
        except DBAPIError as exc:
            ok = False
            outcomes.append(f"SELECT denied: {exc.orig}")
    engine.dispose()
    return _report("audit_events append-only for stf_v3_app", ok, "; ".join(outcomes))


def check_alembic(engine: sa.Engine) -> bool:
    """Verifies a single head and that the DB is at it."""
    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    with engine.connect() as conn:
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    ok = len(heads) == 1 and current == heads[0]
    return _report("single alembic head and DB at head", ok, f"heads={heads} current={current}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        0 if every check passed, else 1.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="owner-role SQLAlchemy URL")
    parser.add_argument("--app-url", help="runtime-role SQLAlchemy URL")
    args = parser.parse_args(argv)
    engine = sa.create_engine(args.url)
    results = [
        check_tables(engine),
        check_invariant(engine),
        check_append_only(args.app_url),
        check_alembic(engine),
    ]
    engine.dispose()
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

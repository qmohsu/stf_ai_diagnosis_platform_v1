"""Startup-time Alembic state guardrails.

Catches two failure modes that have silently bitten production:

1. **Duplicate revision ids** (`Revision X is present more than
   once`): the chain has multiple heads and ``alembic upgrade
   head`` refuses to run.  The container boots anyway and serves
   500s when endpoints hit the un-applied schema changes.
   HARNESS-20 schema-fix migration #106 introduced this kind of
   collision on 2026-05-24; production `/goldens` 500'd until the
   ids were renamed via hotfix #109.

2. **Stale DB schema** (current revision < head): migrations
   merged into `main` but never run via `alembic upgrade head`
   on production.  Same end-user symptom: 500s on the columns
   the un-applied migration was supposed to add.  Same HARNESS-20
   timeline.

This module's job is to fail fast at FastAPI startup so deploy
verification surfaces the issue immediately, before users hit a
500.  The check costs a single connection and a small Alembic
script read; it runs once per container boot.

Usage from ``main.py``::

    from app.services.alembic_check import (
        verify_alembic_state,
        AlembicStateError,
    )

    try:
        verify_alembic_state()
    except AlembicStateError as exc:
        raise RuntimeError(
            f"STARTUP FATAL: {exc}"
        ) from exc

The check is intentionally synchronous + side-effect-free: no
DB writes, no Alembic upgrades.  Operators run upgrades
explicitly via the deploy procedure (see CLAUDE.md).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import structlog
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

logger = structlog.get_logger(__name__)


# ── Module constants ─────────────────────────────────────────


_ALEMBIC_INI_PATH = Path("/app/alembic.ini")
"""Container-side path to the Alembic config baked into the
diagnostic-api image (see Dockerfile's COPY).  Tests override
this via the ``ini_path`` kwarg."""


_ALEMBIC_SCRIPT_LOCATION = Path("/app/alembic")
"""Absolute path to the migrations directory inside the image.
Resolved explicitly here so the check works regardless of the
caller's cwd (Alembic's ini-resolution is cwd-sensitive)."""


class AlembicStateError(RuntimeError):
    """Raised when the Alembic chain or DB state is inconsistent.

    Subclass of ``RuntimeError`` so callers that just want to
    bail out can ``raise RuntimeError(...) from exc`` without
    losing the original details.
    """


# ── Helpers ──────────────────────────────────────────────────


def _load_script_directory(
    ini_path: Path, script_location: Path,
) -> ScriptDirectory:
    """Load the Alembic ScriptDirectory from a known config path.

    Resolves ``script_location`` to an absolute path so the load
    works irrespective of the caller's cwd.  Tests pass a tmp
    path; production uses the constants above.
    """
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(script_location))
    return ScriptDirectory.from_config(cfg)


def _read_db_revision(database_url: str) -> Optional[str]:
    """Return the current revision recorded in the DB, or None.

    None means the ``alembic_version`` table is missing entirely
    (fresh DB, no migrations ever applied).  An empty table also
    returns None.
    """
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


def _database_url_from_settings() -> str:
    """Build a SQLAlchemy URL from app.config settings.

    Lives behind a function so tests can monkeypatch ``settings``
    without import-time side effects, and so the check module
    doesn't pin the settings shape.
    """
    from app.config import settings  # local import on purpose

    return (
        f"postgresql+psycopg2://"
        f"{settings.db_user}:{settings.db_password}@"
        f"{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )


# ── Public entry point ───────────────────────────────────────


def verify_alembic_state(
    ini_path: Optional[Path] = None,
    script_location: Optional[Path] = None,
    database_url: Optional[str] = None,
) -> None:
    """Verify single Alembic head AND DB is at that head.

    Two checks, both fail-fast:

    1. ``ScriptDirectory.get_heads()`` returns exactly one
       revision id.  Multiple heads ⇒ duplicate-revision conflict
       (see module docstring); zero heads ⇒ no migrations
       authored.

    2. The DB's recorded revision (from the ``alembic_version``
       table) equals that single head.  Mismatch ⇒ migrations
       merged but not applied.  None (missing table) ⇒ fresh DB
       that needs ``alembic upgrade head`` once before serving
       traffic.

    Args:
        ini_path: Override the alembic.ini path (test hook).
        script_location: Override the migrations directory (test
            hook).
        database_url: Override the DB URL (test hook).  When
            ``None``, derived from ``app.config.settings``.

    Raises:
        AlembicStateError: If either check fails.  Message
            describes the specific failure so operators can
            unstuck the deploy without spelunking.
    """
    eff_ini = ini_path or _ALEMBIC_INI_PATH
    eff_script = script_location or _ALEMBIC_SCRIPT_LOCATION

    if not eff_ini.is_file():
        # If the ini isn't there we're probably running outside
        # the container (tests, dev shell).  Skip the check so
        # local dev isn't blocked; production deploys always
        # have the file.
        logger.warning(
            "alembic_check.skipped_no_ini",
            ini_path=str(eff_ini),
        )
        return

    script = _load_script_directory(eff_ini, eff_script)
    heads: List[str] = script.get_heads()

    if len(heads) == 0:
        raise AlembicStateError(
            "Alembic has zero heads.  The migrations directory "
            "is empty or unreadable.  Check that the Dockerfile "
            "COPY for /app/alembic/ landed correctly."
        )
    if len(heads) > 1:
        raise AlembicStateError(
            f"Alembic has {len(heads)} heads (expected 1): "
            f"{heads!r}.  This usually means two migration files "
            f"declared the same `revision = ...` id, or a new "
            f"branch was created without merging.  Run "
            f"`alembic heads --verbose` to see the conflicting "
            f"files; fix the revision id and redeploy.  Refusing "
            f"to start — see CLAUDE.md 'CRITICAL Alembic "
            f"gotchas' for the duplicate-id case."
        )

    expected_head = heads[0]
    eff_url = database_url or _database_url_from_settings()
    db_rev = _read_db_revision(eff_url)

    if db_rev is None:
        raise AlembicStateError(
            f"Alembic head is {expected_head!r} but the database "
            f"has no recorded revision (alembic_version table is "
            f"empty or missing).  Run `alembic upgrade head` "
            f"inside the diagnostic-api container before "
            f"starting traffic.  Refusing to start."
        )

    if db_rev != expected_head:
        raise AlembicStateError(
            f"Alembic head is {expected_head!r} but the database "
            f"is at {db_rev!r}.  Pending migrations were not "
            f"applied.  Run `podman exec stf-diagnostic-api "
            f"alembic upgrade head` per CLAUDE.md deploy step 7, "
            f"then restart the container per step 8.  Refusing "
            f"to start."
        )

    logger.info(
        "alembic_check.ok",
        head=expected_head,
        db_revision=db_rev,
    )

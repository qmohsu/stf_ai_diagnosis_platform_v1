"""Unit tests for the Alembic startup guardrail
(``app.services.alembic_check``).

Tests use a real on-disk Alembic config + scripts directory in
``tmp_path`` and a real SQLite DB so the actual
``ScriptDirectory.from_config`` and
``MigrationContext.get_current_revision`` paths run — no
monkey-patching of Alembic internals (which would re-implement
the very thing we're trying to validate).

Coverage:

- Happy path: single head, DB at head → no exception.
- Multi-head (duplicate revision ids) → AlembicStateError with a
  message that names the heads.
- Zero heads (empty migrations dir) → AlembicStateError.
- Stale DB (DB at older revision, head ahead) →
  AlembicStateError.
- Missing alembic_version table → AlembicStateError.
- Missing ini file → logs a warning and returns silently (the
  dev-shell escape hatch).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.services.alembic_check import (
    AlembicStateError,
    verify_alembic_state,
)


def _write_alembic_setup(
    tmp_path: Path,
    revisions: list[tuple[str, str, str | None]],
) -> tuple[Path, Path]:
    """Create a minimal alembic.ini + versions/ tree on disk.

    Each revision tuple is ``(revision_id, filename_stem,
    down_revision)``.  Two tuples with the same ``revision_id``
    produce the duplicate-revision conflict.

    Returns ``(ini_path, script_dir)`` for passing to
    ``verify_alembic_state``.
    """
    script_dir = tmp_path / "alembic"
    versions_dir = script_dir / "versions"
    versions_dir.mkdir(parents=True)

    # Minimal alembic.ini.
    ini_path = tmp_path / "alembic.ini"
    ini_path.write_text(
        f"""\
[alembic]
script_location = {script_dir}
""",
        encoding="utf-8",
    )

    # Minimal env.py — ScriptDirectory doesn't need it to load
    # but Alembic's API surface is happier with it present.
    (script_dir / "env.py").write_text(
        "# minimal env.py for tests\n", encoding="utf-8",
    )
    (script_dir / "script.py.mako").write_text(
        '"""${message}"""\n\nrevision = "${up_revision}"\n'
        'down_revision = ${repr(down_revision)}\n',
        encoding="utf-8",
    )

    for rev, stem, down in revisions:
        (versions_dir / f"{stem}.py").write_text(
            textwrap.dedent(
                f'''\
                """Test migration {stem}."""

                revision = "{rev}"
                down_revision = {repr(down)}
                branch_labels = None
                depends_on = None


                def upgrade() -> None:
                    pass


                def downgrade() -> None:
                    pass
                '''
            ),
            encoding="utf-8",
        )

    return ini_path, script_dir


def _make_sqlite_db_with_revision(
    tmp_path: Path,
    revision: str | None,
) -> str:
    """Create an empty SQLite DB optionally pre-stamped with a
    given Alembic revision.  Returns the database_url."""
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    if revision is not None:
        from sqlalchemy import create_engine, text
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL "
                    "PRIMARY KEY)"
                ),
            )
            conn.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    f"VALUES ('{revision}')"
                ),
            )
        engine.dispose()
    return url


# ── Happy path ───────────────────────────────────────────────


def test_happy_path_single_head_db_at_head(tmp_path: Path) -> None:
    """One revision in the script dir, DB stamped with the same
    revision → check passes silently."""
    ini, script_dir = _write_alembic_setup(
        tmp_path,
        revisions=[("abc123", "abc123_initial", None)],
    )
    url = _make_sqlite_db_with_revision(tmp_path, "abc123")
    # No exception expected.
    verify_alembic_state(
        ini_path=ini,
        script_location=script_dir,
        database_url=url,
    )


def test_happy_path_multi_revision_chain(tmp_path: Path) -> None:
    """Linear chain of three revisions, DB at the head."""
    ini, script_dir = _write_alembic_setup(
        tmp_path,
        revisions=[
            ("a", "a_initial", None),
            ("b", "b_next", "a"),
            ("c", "c_head", "b"),
        ],
    )
    url = _make_sqlite_db_with_revision(tmp_path, "c")
    verify_alembic_state(
        ini_path=ini,
        script_location=script_dir,
        database_url=url,
    )


# ── Failure modes ────────────────────────────────────────────


def test_multi_head_raises(tmp_path: Path) -> None:
    """Two head revisions (e.g. two migrations both rooted at
    the same down_revision) → AlembicStateError with both heads
    in the message."""
    ini, script_dir = _write_alembic_setup(
        tmp_path,
        revisions=[
            ("root", "root", None),
            ("branch_a", "branch_a", "root"),
            ("branch_b", "branch_b", "root"),
        ],
    )
    url = _make_sqlite_db_with_revision(tmp_path, "root")
    with pytest.raises(AlembicStateError) as exc:
        verify_alembic_state(
            ini_path=ini,
            script_location=script_dir,
            database_url=url,
        )
    msg = str(exc.value)
    assert "2 heads" in msg
    assert "branch_a" in msg
    assert "branch_b" in msg
    assert "expected 1" in msg


def test_zero_heads_raises(tmp_path: Path) -> None:
    """Empty migrations dir → AlembicStateError."""
    ini, script_dir = _write_alembic_setup(tmp_path, revisions=[])
    url = _make_sqlite_db_with_revision(tmp_path, None)
    with pytest.raises(AlembicStateError) as exc:
        verify_alembic_state(
            ini_path=ini,
            script_location=script_dir,
            database_url=url,
        )
    assert "zero heads" in str(exc.value)


def test_stale_db_raises(tmp_path: Path) -> None:
    """DB at an older revision than the head → AlembicStateError
    naming both revisions + pointing at the deploy procedure."""
    ini, script_dir = _write_alembic_setup(
        tmp_path,
        revisions=[
            ("old", "old_initial", None),
            ("new", "new_head", "old"),
        ],
    )
    url = _make_sqlite_db_with_revision(tmp_path, "old")
    with pytest.raises(AlembicStateError) as exc:
        verify_alembic_state(
            ini_path=ini,
            script_location=script_dir,
            database_url=url,
        )
    msg = str(exc.value)
    assert "'new'" in msg
    assert "'old'" in msg
    assert "alembic upgrade head" in msg


def test_missing_alembic_version_table_raises(
    tmp_path: Path,
) -> None:
    """alembic_version table absent → AlembicStateError telling
    operators to run the initial upgrade."""
    ini, script_dir = _write_alembic_setup(
        tmp_path,
        revisions=[("first", "first_head", None)],
    )
    # DB exists but no alembic_version table.
    url = _make_sqlite_db_with_revision(tmp_path, None)
    with pytest.raises(AlembicStateError) as exc:
        verify_alembic_state(
            ini_path=ini,
            script_location=script_dir,
            database_url=url,
        )
    msg = str(exc.value)
    assert "no recorded revision" in msg
    assert "alembic upgrade head" in msg


# ── Dev-shell escape hatch ───────────────────────────────────


def test_missing_ini_file_skips_check(tmp_path: Path) -> None:
    """When the ini path doesn't exist (e.g. running unit tests
    outside the container), the check no-ops silently rather
    than blocking local development."""
    nonexistent = tmp_path / "does_not_exist.ini"
    # Should not raise even though everything else is bogus.
    verify_alembic_state(
        ini_path=nonexistent,
        script_location=tmp_path / "alembic",
        database_url="sqlite:///nonexistent.db",
    )

"""Alembic environment for the V3 database.

The URL comes from ``STF_V3_DATABASE_URL`` (via ``stf_v3.settings``), so the
same configuration drives the app and the migrations.  procrastinate's own
tables (``procrastinate_*``) are excluded from autogenerate and ``alembic
check`` because they are created from the library's bundled schema.

Author: Xiangzhu Yan
"""

from logging.config import fileConfig
from typing import Any, Optional

from alembic import context
from sqlalchemy import engine_from_config, pool

from stf_v3.metadata import PROCRASTINATE_PREFIX, metadata
from stf_v3.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = metadata


def _include_name(
    name: Optional[str], type_: str, parent_names: Any
) -> bool:
    """Excludes procrastinate-owned tables from reflection.

    Args:
        name: Object name being considered.
        type_: Object type (``table``, ``index`` ...).
        parent_names: Alembic-supplied parent name mapping.

    Returns:
        False for procrastinate tables, True otherwise.
    """
    del parent_names
    if type_ == "table" and name is not None:
        return not name.startswith(PROCRASTINATE_PREFIX)
    return True


def run_migrations_offline() -> None:
    """Runs migrations in 'offline' mode (emits SQL only)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Runs migrations against the configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=_include_name,
            compare_type=True,
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

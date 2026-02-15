from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
import os
import sys

# Add the project root directory to the python path
sys.path.append(os.getcwd())

from app.config import settings
from app.db.base import Base
# Import models so they are registered with Base metadata
from app.models_db import (
    User, Vehicle, DiagnosticSession, DiagnosticFeedback,
    OBDAnalysisSession, OBDSummaryFeedback, OBDDetailedFeedback,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Overwrite the sqlalchemy.url with the one from settings
# But we need to ensure we use localhost if we are running outside docker
# For migration generation, we might want to manually override or trust the settings
# If settings.db_host is 'postgres' (docker service name), it won't work on host
# We will rely on env var overrides or let the user handle it.
# For now, let's just use settings.database_url
# config.set_main_option("sqlalchemy.url", settings.database_url)

# Wait, settings.database_url will use "postgres" if DB_HOST is not set.
# We should probably force it to localhost if we detect we are on host?
# Or clearer: just use settings.database_url and assume the user sets DB_HOST=localhost
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Runtime settings for the V3 backend.

Every external connection (database, LLM, storage) is configured here and
nowhere else, so V3 shares infrastructure with V1/V2 only through
configuration (dev plan D3, constraint B).

Author: Xiangzhu Yan
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``STF_V3_``).

    Attributes:
        database_url: SQLAlchemy URL for the ``stf_v3`` database using the
            psycopg 3 driver, e.g.
            ``postgresql+psycopg://stf_v3:pw@127.0.0.1:5432/stf_v3``.
            The same URL string serves the async engine (app) and the sync
            engine (Alembic); psycopg 3 supports both.
        db_pool_size: Connection pool size for the async engine.
        log_level: structlog / uvicorn log level.
    """

    model_config = SettingsConfigDict(
        env_prefix="STF_V3_", env_file=".env", extra="ignore"
    )

    database_url: str = Field(
        default="postgresql+psycopg://stf_v3:stf_v3@127.0.0.1:5432/stf_v3",
    )
    db_pool_size: int = 5
    log_level: str = "INFO"


settings = Settings()

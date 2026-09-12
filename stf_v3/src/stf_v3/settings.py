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
        jwt_secret: HMAC secret for access tokens.  Must be overridden in
            every real deployment (startup refuses the default).
        jwt_lifetime_seconds: Access-token lifetime (default 12 h).
        log_level: structlog / uvicorn log level.
        environment: ``dev`` | ``test`` | ``prod``; ``prod`` enforces a
            non-default JWT secret.
    """

    model_config = SettingsConfigDict(
        env_prefix="STF_V3_", env_file=".env", extra="ignore"
    )

    database_url: str = Field(
        default="postgresql+psycopg://stf_v3:stf_v3@127.0.0.1:5432/stf_v3",
    )
    db_pool_size: int = 5
    jwt_secret: str = "change-me-in-deployment"
    jwt_lifetime_seconds: int = 12 * 3600
    log_level: str = "INFO"
    environment: str = "dev"
    git_commit: str = "unknown"   # baked into the image by the Dockerfile


settings = Settings()

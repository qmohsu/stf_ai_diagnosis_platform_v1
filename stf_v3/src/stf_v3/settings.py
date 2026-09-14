"""Runtime settings for the V3 backend.

Every external connection (database, LLM, storage) is configured here and
nowhere else, so V3 shares infrastructure with V1/V2 only through
configuration (dev plan D3, constraint B).

Author: Xiangzhu Yan
"""

from pydantic import AliasChoices, Field
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
        obd_log_storage_path: Root directory for uploaded raw logs
            (``<root>/<vehicle_id>/<log_id>.<ext>``).
        max_upload_bytes: Reject uploads larger than this (413).
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
    # PROD-05: raw log storage root (a named volume in Compose) and the
    # per-file upload ceiling (50 MB; real logs are KB–MB).
    obd_log_storage_path: str = "./data/obd_logs"
    max_upload_bytes: int = 50 * 1024 * 1024
    # PROD-06: manual library (public) + one-step ingest pipeline.  The
    # storage root is a named volume shared by api, worker and the host
    # GPU worker; ``<root>/uploads/<id>.pdf`` holds sources and
    # ``<root>/<manual dir>/index/`` the index-track artefacts.
    manual_storage_path: str = "./data/manuals"
    manual_max_upload_bytes: int = 200 * 1024 * 1024
    manual_max_pages: int = 800
    manual_min_free_gb: int = 30          # refuse to start a conversion below
    manual_ingest_timeout_s: int = 3 * 3600
    mineru_bin: str = "mineru"            # host GPU worker: absolute path
    mineru_timeout_s: int = 3600
    manual_work_dir: str = "./data/manual_builds"   # persistent work dirs
    summary_model: str = "deepseek/deepseek-v3.2"
    # Accepts STF_V3_OPENROUTER_API_KEY or the server's existing
    # OPENROUTER_API_KEY (single source: infra/.env).
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "STF_V3_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"
        ),
    )
    repo_dir: str = ""                    # host worker: git checkout for commit stamp


settings = Settings()

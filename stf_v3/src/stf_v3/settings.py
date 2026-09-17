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
        llm_base_url / llm_model / llm_api_key: the single model source
            (PROD-08); non-local addresses need ``llm_allow_cloud``.
        agent_* / subagent_* / tool_result_max_tokens /
            compact_threshold_tokens: agent budgets (all overridable).
        manual_images_enabled: return manual images to the model.
        default_locale: report language when a run does not name one.
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

    # ── Agent runtime (PROD-08) ──────────────────────────────────────
    # The ONLY model source (design doc D7): an OpenAI-compatible endpoint.
    # Defaults to the server's Ollama; PROD-09 points it at vLLM.  A
    # non-local address is refused unless ``llm_allow_cloud`` is set
    # (FM-19 / FM-51: prompts carry the VIN, which must not leave the
    # backend un-pseudonymised).
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen3.5:27b-q8_0"
    llm_api_key: str = "ollama"            # dummy for Ollama / vLLM
    llm_allow_cloud: bool = False
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.3
    llm_request_timeout_s: float = 300.0   # one model request (read timeout)
    # Cloud comparison endpoint (PROD-09 wires it; off by default).
    cloud_llm_enabled: bool = False
    cloud_llm_base_url: str = "https://openrouter.ai/api/v1"
    cloud_llm_model: str = "deepseek/deepseek-v3.2"
    cloud_llm_api_key: str = ""
    # Budgets (V2 magnitudes; every gate is a setting, FM-28).
    agent_wall_clock_s: float = 1200.0
    agent_request_limit: int = 80
    agent_tool_calls_limit: int = 120
    agent_total_tokens_limit: int = 600_000
    subagent_wall_clock_s: float = 240.0
    subagent_request_limit: int = 12
    subagent_max_tokens: int = 12_288
    subagent_temperature: float = 0.2
    tool_result_max_tokens: int = 2000
    compact_threshold_tokens: int = 60_000
    # Manual images in tool results: off until the model is known to
    # accept image parts (FM-42; PROD-09 decides per model).
    manual_images_enabled: bool = False
    # Report language when the caller does not specify one (D2).
    default_locale: str = "zh-TW"

    @property
    def llm_is_local(self) -> bool:
        """True when ``llm_base_url`` points at this machine / a private net."""
        return is_local_url(self.llm_base_url)


def is_local_url(url: str) -> bool:
    """Whether a URL's host is loopback or a private (RFC 1918) address.

    Used to decide if the VIN may appear in prompts and whether the
    endpoint is allowed at all without ``llm_allow_cloud``.
    """
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal", "host.containers.internal"):
        return True
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


settings = Settings()

"""Runtime settings for the V3 backend.

Every external connection (database, LLM, storage) is configured here and
nowhere else, so V3 shares infrastructure with V1/V2 only through
configuration (dev plan D3, constraint B).

Author: Xiangzhu Yan
"""

from typing import Optional

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

    # ── Agent runtime (PROD-08) · model adapter (PROD-09) ───────────
    # The ONLY model source (design doc D7): an OpenAI-compatible endpoint.
    # Defaults to the server's vLLM (Qwen3.6-27B-FP8, #237); a non-local
    # address is refused unless ``llm_allow_cloud`` is set (FM-19 / FM-51:
    # prompts carry the VIN, which must not leave the backend
    # un-pseudonymised).  ``llm_profile`` picks the adapter profile
    # (thinking off, no thinking send-back, non-strict tools …):
    # auto = by URL + model name (see diagnosis.agent.model).
    llm_base_url: str = "http://127.0.0.1:8010/v1"
    llm_model: str = "Qwen/Qwen3.6-27B-FP8"
    llm_api_key: str = "none"              # dummy for vLLM / Ollama
    llm_profile: str = "auto"              # auto | qwen-vllm | qwen-ollama | generic
    llm_allow_cloud: bool = False
    # Per-request settings: None = the adapter profile's default (FM-8);
    # an explicit value (env) always wins.
    llm_max_tokens: Optional[int] = None
    llm_temperature: Optional[float] = None
    llm_request_timeout_s: Optional[float] = None   # one model request (read timeout)
    # Qwen thinking on the vLLM profile (PROD-09: off for speed).  Only the
    # PROD-10 thinking-on comparison run turns it on (FM-45); never the
    # product default.
    llm_thinking: bool = False
    # Cloud comparison endpoint (PROD-09 D3): comparison runs only, never
    # the product path.  Empty key → the OpenRouter key above (FM-32).
    cloud_llm_enabled: bool = False
    cloud_llm_base_url: str = "https://openrouter.ai/api/v1"
    cloud_llm_model: str = "deepseek/deepseek-v3.2"
    cloud_llm_api_key: str = ""
    # OpenRouter hosting provider to pin for the qwen-openrouter profile
    # (e.g. "DeepInfra"); empty = OpenRouter picks per request.  Comparison
    # runs only (PROD-10 D5 follow-up).
    cloud_llm_provider: str = ""
    # Budgets (every gate is a setting, FM-28).  None = the adapter
    # profile's default: vLLM, Ollama and cloud differ (FM-8).
    agent_wall_clock_s: Optional[float] = None
    agent_request_limit: Optional[int] = None
    agent_tool_calls_limit: Optional[int] = None
    agent_total_tokens_limit: Optional[int] = None
    subagent_wall_clock_s: Optional[float] = None
    subagent_request_limit: Optional[int] = None
    subagent_max_tokens: Optional[int] = None
    subagent_temperature: Optional[float] = None
    tool_result_max_tokens: int = 2000
    # Sub-agents see tool output whole, as V2's sub-agents did (PROD-10: the
    # 2000 cut hid half of the manual TOC and long sections); this only
    # guards against a runaway result.
    subagent_tool_result_max_tokens: int = 16_000
    compact_threshold_tokens: int = 60_000
    # After a wall-clock / usage gate: one tool-less turn (at most this
    # long) to write the report from the evidence so far; 0 = off (PROD-11).
    agent_wrapup_s: float = 180.0
    # Manual images in tool results: off until the model is known to
    # accept image parts (FM-42; PROD-09 decides per model).
    manual_images_enabled: bool = False
    # Report language when the caller does not specify one (D2).
    default_locale: str = "zh-TW"

    # ── Diagnosis jobs, SSE and the on-demand model (PROD-11) ───────
    # D2: a diagnosis waits at most this long for the model, counted from
    # the click (FM-22); a ``waiting`` event every ``…_wait_event_s``.
    diagnosis_model_wait_s: int = 3600
    diagnosis_wait_event_s: int = 60
    diagnosis_wait_poll_s: float = 10.0
    diagnosis_cancel_poll_s: float = 2.0          # FM-49: flag read off-loop
    diagnosis_event_flush_s: float = 0.5          # FM-1: events visible within ~1 s
    diagnosis_queue_stale_s: int = 300            # FM-9: queued without a job
    # D1: the host controller starts vLLM on demand (both GPUs free) and
    # stops it after ``llm_idle_stop_s`` idle, only if it started it.
    llm_autostart: bool = True
    llm_idle_stop_s: int = 1800
    llm_start_timeout_s: int = 1500               # cold start ≈ 10–12 min
    llm_start_cooldown_s: int = 900               # FM-24: after a failed start
    llm_gpu_free_mib: int = 2000                  # a card counts as free below this
    vllm_ctl_path: str = ""                       # default: <repo_dir>/infra/vllm_ctl.sh
    llm_ctl_scope: bool = True                    # start vLLM in its own systemd scope
    eval_lock_path: str = "~/stf_v3_evals/.lock"  # FM-26: never stop under an eval
    # SSE (blueprint §3.7): poll the black box, keep the proxy chain alive.
    sse_poll_s: float = 0.25
    sse_keepalive_s: float = 15.0
    sse_max_stream_s: int = 7200                  # FM-36: no endless stream
    sse_gap_wait_s: float = 5.0                   # FM-19: wait for a missing seq

    @property
    def llm_is_local(self) -> bool:
        """True when ``llm_base_url`` points at this machine / a private net."""
        return is_local_url(self.llm_base_url)

    @property
    def cloud_api_key(self) -> str:
        """The cloud comparison key: its own field, else the OpenRouter key (FM-32)."""
        return self.cloud_llm_api_key or self.openrouter_api_key


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

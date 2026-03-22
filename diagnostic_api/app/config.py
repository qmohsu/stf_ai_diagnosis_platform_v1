"""Configuration module for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All sensitive configuration is loaded from environment variables
    to avoid hardcoding secrets in the codebase.

    TODO(12): Replace manual os.getenv() calls with plain defaults and let
    pydantic-settings handle env-var binding automatically. The current
    pattern bypasses Pydantic type coercion and evaluates at import time.
    """

    # API Metadata
    app_name: str = "STF Diagnostic API"
    app_version: str = "0.1.0"
    debug_mode: bool = False

    # Database Configuration
    db_host: str = os.getenv("DB_HOST", "postgres")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "stf_diagnosis")
    db_user: str = os.getenv("DB_USER", "stf_app_user")
    db_password: str = os.getenv("DB_PASSWORD", "")

    # LLM Configuration
    llm_endpoint: str = os.getenv("LLM_ENDPOINT", "http://ollama:11434")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.5:9b")

    # Embedding Configuration
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    embedding_endpoint: str = os.getenv("EMBEDDING_ENDPOINT", "")

    # Vision Configuration
    vision_model: str = os.getenv("VISION_MODEL", "llava")

    # Premium LLM Configuration (OpenRouter Cloud API — opt-in)
    premium_llm_enabled: bool = (
        os.getenv("PREMIUM_LLM_ENABLED", "false").lower() == "true"
    )
    premium_llm_api_key: str = os.getenv("PREMIUM_LLM_API_KEY", "")
    premium_llm_base_url: str = os.getenv(
        "PREMIUM_LLM_BASE_URL",
        "https://openrouter.ai/api/v1",
    )
    premium_llm_model: str = os.getenv(
        "PREMIUM_LLM_MODEL", "anthropic/claude-sonnet-4.6"
    )
    premium_llm_curated_models: str = os.getenv(
        "PREMIUM_LLM_CURATED_MODELS",
        "anthropic/claude-opus-4.6,"
        "anthropic/claude-sonnet-4.6,"
        "google/gemini-3.1-pro-preview,"
        "google/gemini-3-flash-preview,"
        "openai/gpt-5.2,"
        "openai/gpt-5-mini,"
        "deepseek/deepseek-v3.2,"
        "deepseek/deepseek-chat,"
        "qwen/qwen3.5-plus-02-15,"
        "qwen/qwen3.5-flash-02-23",
    )

    @property
    def premium_llm_model_list(self) -> list[str]:
        """Parse curated model list from comma-separated string.

        Returns:
            List of OpenRouter model ID strings.
        """
        return [
            m.strip()
            for m in self.premium_llm_curated_models.split(",")
            if m.strip()
        ]

    # Logging Configuration
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")
    log_file: str = os.getenv("LOG_FILE", "/app/logs/diagnostic_api.log")

    # Security Configuration
    strict_mode: bool = os.getenv("STRICT_MODE", "true").lower() == "true"
    allow_external_apis: bool = (
        os.getenv("ALLOW_EXTERNAL_APIS", "false").lower() == "true"
    )

    # JWT / Authentication
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_access_token_expire_minutes: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )

    def validate_jwt_secret(self) -> None:
        """Verify JWT_SECRET_KEY is set and strong enough.

        Raises:
            SystemExit: If secret is missing or shorter than
                32 characters.
        """
        if not self.jwt_secret_key or len(
            self.jwt_secret_key
        ) < 32:
            raise SystemExit(
                "FATAL: JWT_SECRET_KEY must be set and at "
                "least 32 characters. Generate one with: "
                "openssl rand -hex 32"
            )

    @property
    def database_url(self) -> str:
        """Construct database connection URL.

        Returns:
            Database connection string for SQLAlchemy.
        """
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    class Config:
        """Pydantic configuration."""

        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()

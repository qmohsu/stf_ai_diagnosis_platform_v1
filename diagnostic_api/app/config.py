"""Configuration module for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

import os
from typing import Optional

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
    llm_model: str = os.getenv("LLM_MODEL", "qwen3:14b")

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
        "PREMIUM_LLM_MODEL", "anthropic/claude-sonnet-4"
    )
    premium_llm_curated_models: str = os.getenv(
        "PREMIUM_LLM_CURATED_MODELS",
        "anthropic/claude-sonnet-4,"
        "openai/gpt-4o,"
        "google/gemini-2.5-pro,"
        "meta-llama/llama-4-maverick",
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

    # Weaviate Configuration
    weaviate_url: str = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
    weaviate_api_key: Optional[str] = os.getenv("WEAVIATE_API_KEY")

    # Logging Configuration
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")
    log_file: str = os.getenv("LOG_FILE", "/app/logs/diagnostic_api.log")

    # Security Configuration
    strict_mode: bool = os.getenv("STRICT_MODE", "true").lower() == "true"
    redact_pii: bool = os.getenv("REDACT_PII", "true").lower() == "true"
    allow_external_apis: bool = (
        os.getenv("ALLOW_EXTERNAL_APIS", "false").lower() == "true"
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

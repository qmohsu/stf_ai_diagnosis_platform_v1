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
    llm_model: str = os.getenv("LLM_MODEL", "llama3:8b")

    # Embedding Configuration
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    embedding_endpoint: str = os.getenv("EMBEDDING_ENDPOINT", "")

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

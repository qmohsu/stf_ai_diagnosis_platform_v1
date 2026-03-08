"""Shared pytest fixtures and markers for diagnostic_api tests."""

import os
import uuid
from unittest.mock import MagicMock

import pytest

# Ensure a valid JWT secret exists before app import triggers
# startup validation (min 32 chars required).
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-jwt-secret-key-not-for-production-use",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require external services "
        "(Ollama, Weaviate, etc.)",
    )


MOCK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def make_mock_user(
    user_id: uuid.UUID = MOCK_USER_ID,
    username: str = "testuser",
) -> MagicMock:
    """Create a mock User object for auth dependency overrides."""
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.is_active = True
    return user

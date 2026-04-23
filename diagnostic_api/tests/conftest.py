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


def pytest_addoption(parser):
    parser.addoption(
        "--run-eval",
        action="store_true",
        default=False,
        help=(
            "run the manual-agent eval suite "
            "(slow, requires LLM access); otherwise "
            "eval-marked tests are skipped"
        ),
    )
    parser.addoption(
        "--mock-judge",
        action="store_true",
        default=False,
        help=(
            "use a stub judge that returns a perfect Grade "
            "(for --run-eval plumbing verification without "
            "API keys)"
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require external services "
        "(Ollama, PostgreSQL, etc.)",
    )
    config.addinivalue_line(
        "markers",
        "e2e_real_llm: marks tests that require real LLM access "
        "(skip with: pytest -m 'not e2e_real_llm')",
    )
    config.addinivalue_line(
        "markers",
        "eval: marks tests in the manual-agent evaluation suite "
        "(run with: pytest --run-eval)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip eval-marked tests unless --run-eval is passed."""
    if config.getoption("--run-eval"):
        return
    skip_eval = pytest.mark.skip(
        reason="use --run-eval to run eval suite",
    )
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip_eval)


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

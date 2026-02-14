"""Shared pytest fixtures and markers for diagnostic_api tests."""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require external services (Ollama, Weaviate, etc.)",
    )

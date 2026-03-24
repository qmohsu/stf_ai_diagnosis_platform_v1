"""Tests for premium model availability cache and probing.

Covers:
  - mark_model_blocked / is_model_available / get_available_models
  - probe_model: success, 403, network error (fail-open)
  - refresh_availability: updates blocked set
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import openai
import pytest

from app.expert.model_availability import (
    get_available_models,
    get_blocked_models,
    is_cache_stale,
    is_model_available,
    mark_model_blocked,
    probe_model,
    reset_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset module-level cache before each test."""
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# mark / is_available / get_available / get_blocked
# ---------------------------------------------------------------------------


class TestBlockedSet:
    """Tests for the in-memory blocked model set."""

    def test_mark_model_blocked_adds_to_set(self):
        """Marking a model adds it to the blocked set."""
        assert is_model_available("anthropic/claude-sonnet-4.6")
        mark_model_blocked("anthropic/claude-sonnet-4.6")
        assert not is_model_available(
            "anthropic/claude-sonnet-4.6"
        )

    def test_mark_model_blocked_is_idempotent(self):
        """Marking the same model twice does not error."""
        mark_model_blocked("openai/gpt-5.2")
        mark_model_blocked("openai/gpt-5.2")
        assert not is_model_available("openai/gpt-5.2")

    def test_get_available_models_filters_blocked(self):
        """get_available_models returns only non-blocked models."""
        curated = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
            "openai/gpt-5.2",
        ]
        mark_model_blocked("anthropic/claude-sonnet-4.6")
        mark_model_blocked("openai/gpt-5.2")

        available = get_available_models(curated)
        assert available == ["deepseek/deepseek-v3.2"]

    def test_get_blocked_models_returns_blocked_subset(self):
        """get_blocked_models returns only blocked models."""
        curated = [
            "anthropic/claude-sonnet-4.6",
            "deepseek/deepseek-v3.2",
        ]
        mark_model_blocked("anthropic/claude-sonnet-4.6")

        blocked = get_blocked_models(curated)
        assert blocked == ["anthropic/claude-sonnet-4.6"]

    def test_is_cache_stale_initially(self):
        """Cache is stale before any probe has run."""
        assert is_cache_stale()


# ---------------------------------------------------------------------------
# probe_model
# ---------------------------------------------------------------------------


class TestProbeModel:
    """Tests for the probe_model async function."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        """Successful completion means the model is available."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=AsyncMock()
        )

        result = await probe_model(
            mock_client, "deepseek/deepseek-v3.2"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_permission_denied(self):
        """PermissionDeniedError (403) means the model is blocked."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.PermissionDeniedError(
                message="region blocked",
                response=AsyncMock(status_code=403),
                body=None,
            ),
        )

        result = await probe_model(
            mock_client, "anthropic/claude-sonnet-4.6"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_fails_open_on_network_error(self):
        """Non-403 errors are treated as available (fail-open)."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(
                request=AsyncMock(),
            ),
        )

        result = await probe_model(
            mock_client, "deepseek/deepseek-v3.2"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_fails_open_on_rate_limit(self):
        """Rate-limit errors are treated as available."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.RateLimitError(
                message="rate limited",
                response=AsyncMock(status_code=429),
                body=None,
            ),
        )

        result = await probe_model(
            mock_client, "qwen/qwen3.5-plus-02-15"
        )
        assert result is True

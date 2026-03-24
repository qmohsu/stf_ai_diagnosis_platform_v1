"""Model availability cache for region-blocked OpenRouter models.

Tracks which premium LLM models are accessible from the server's
location.  Models that return HTTP 403 (PermissionDeniedError) are
marked as blocked and filtered out of the curated list.

Two mechanisms update the cache:
  1. **Reactive** — ``mark_model_blocked()`` is called by the
     streaming endpoint when a 403 is encountered at runtime.
  2. **Proactive** — ``refresh_availability()`` sends a minimal
     1-token probe to every curated model and caches results.
     Triggered lazily on the first ``/premium/models`` request,
     then at most once per ``_CACHE_TTL_SECONDS``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import openai
import structlog

logger = structlog.get_logger()

# ── Module-level singleton state ──────────────────────────────────

_blocked: set[str] = set()
_last_probe_ts: float = 0.0
_CACHE_TTL_SECONDS: int = 3600  # 1 hour
_probe_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return (and lazily create) the module-level asyncio lock."""
    global _probe_lock
    if _probe_lock is None:
        _probe_lock = asyncio.Lock()
    return _probe_lock


# ── Public helpers ────────────────────────────────────────────────


def mark_model_blocked(model: str) -> None:
    """Record *model* as region-blocked (403).

    Args:
        model: OpenRouter model ID, e.g. ``"anthropic/claude-sonnet-4.6"``.
    """
    if model not in _blocked:
        _blocked.add(model)
        logger.warning(
            "model_marked_blocked",
            model=model,
        )


def is_model_available(model: str) -> bool:
    """Return ``True`` if *model* is not in the blocked set."""
    return model not in _blocked


def get_available_models(curated: list[str]) -> list[str]:
    """Filter *curated* list, keeping only non-blocked models.

    Args:
        curated: Full admin-curated model ID list.

    Returns:
        Subset of *curated* that are not blocked.
    """
    return [m for m in curated if m not in _blocked]


def get_blocked_models(curated: list[str]) -> list[str]:
    """Return models from *curated* that are currently blocked.

    Args:
        curated: Full admin-curated model ID list.

    Returns:
        Subset of *curated* that are blocked.
    """
    return [m for m in curated if m in _blocked]


def is_cache_stale() -> bool:
    """Return ``True`` if the probe cache has never run or is expired."""
    if _last_probe_ts == 0.0:
        return True
    return (time.monotonic() - _last_probe_ts) > _CACHE_TTL_SECONDS


def reset_cache() -> None:
    """Clear all cached state.  Intended for tests only."""
    global _last_probe_ts
    _blocked.clear()
    _last_probe_ts = 0.0


# ── Probing ───────────────────────────────────────────────────────

_PROBE_SEMAPHORE_LIMIT: int = 3


async def probe_model(
    client: openai.AsyncOpenAI,
    model: str,
) -> bool:
    """Send a minimal completion to *model* and return availability.

    Args:
        client: Pre-configured AsyncOpenAI pointing at OpenRouter.
        model: OpenRouter model ID to probe.

    Returns:
        ``True`` if the model responded successfully, ``False`` if
        it returned 403 (region-blocked).  Returns ``True`` on any
        *other* error (fail-open) to avoid false negatives.
    """
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return True

    except openai.PermissionDeniedError:
        logger.info(
            "probe_model_blocked",
            model=model,
        )
        return False

    except Exception as exc:
        # Fail-open: treat network errors, rate-limits, etc.
        # as "available" to avoid false negatives.
        logger.debug(
            "probe_model_error_fail_open",
            model=model,
            error=str(exc),
        )
        return True


async def probe_models(
    api_key: str,
    base_url: str,
    models: list[str],
) -> dict[str, bool]:
    """Probe all *models* concurrently and return availability map.

    Uses a semaphore to limit concurrency to
    ``_PROBE_SEMAPHORE_LIMIT``.

    Args:
        api_key: OpenRouter API key.
        base_url: OpenRouter base URL.
        models: List of model IDs to probe.

    Returns:
        Dict mapping each model ID to ``True`` (available) or
        ``False`` (blocked).
    """
    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    sem = asyncio.Semaphore(_PROBE_SEMAPHORE_LIMIT)

    async def _probe_with_sem(m: str) -> tuple[str, bool]:
        async with sem:
            ok = await probe_model(client, m)
            return m, ok

    results = await asyncio.gather(
        *[_probe_with_sem(m) for m in models],
    )
    return dict(results)


async def refresh_availability(
    api_key: str,
    base_url: str,
    models: list[str],
) -> None:
    """Probe all *models* and update the blocked set + timestamp.

    Acquires ``_probe_lock`` so concurrent callers don't duplicate
    work.  If another refresh completed within the TTL window while
    we waited, the call is skipped.

    Args:
        api_key: OpenRouter API key.
        base_url: OpenRouter base URL.
        models: List of model IDs to probe.
    """
    global _last_probe_ts
    lock = _get_lock()

    async with lock:
        # Double-check: another coroutine may have refreshed while
        # we waited on the lock.
        if not is_cache_stale():
            return

        logger.info(
            "model_availability_probe_start",
            model_count=len(models),
        )
        availability = await probe_models(
            api_key, base_url, models,
        )

        # Reset blocked set and rebuild from probe results.
        _blocked.clear()
        for model, available in availability.items():
            if not available:
                _blocked.add(model)

        _last_probe_ts = time.monotonic()

        available_list = [
            m for m, ok in availability.items() if ok
        ]
        blocked_list = [
            m for m, ok in availability.items() if not ok
        ]
        logger.info(
            "model_availability_probe_done",
            available=available_list,
            blocked=blocked_list,
        )

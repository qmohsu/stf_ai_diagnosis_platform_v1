"""Tests for the timer-based SSE keep-alive and LLM pre-warm.

Covers the Issue #128 fixes:
  - ``_with_keepalive`` injects ``": ping"`` comments during the
    silent gap before the first upstream frame (e.g. an Ollama
    cold-load after a deploy), passes real frames through unchanged,
    and terminates cleanly.
  - ``prewarm_local_model`` posts a minimal warm-up request and is
    non-fatal on failure.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v2.endpoints.obd_analysis import _with_keepalive
from app.expert.client import prewarm_local_model

_PING = ": ping\n\n"


async def _collect(source: AsyncIterator[str]) -> List[str]:
    """Drain an async iterator into a list."""
    out: List[str] = []
    async for frame in source:
        out.append(frame)
    return out


# ── _with_keepalive ─────────────────────────────────────────────────


class TestWithKeepalive:
    """Behaviour of the shared timer-based SSE keep-alive wrapper."""

    @pytest.mark.asyncio
    async def test_passes_frames_through_unchanged(self) -> None:
        """Frames from a fast source are yielded verbatim, no pings.

        A source that never goes silent longer than the interval
        should produce exactly its own frames.
        """
        async def source() -> AsyncIterator[str]:
            yield "event: token\ndata: \"a\"\n\n"
            yield "event: token\ndata: \"b\"\n\n"

        frames = await _collect(
            _with_keepalive(source(), interval=10.0),
        )

        assert frames == [
            "event: token\ndata: \"a\"\n\n",
            "event: token\ndata: \"b\"\n\n",
        ]
        assert _PING not in frames

    @pytest.mark.asyncio
    async def test_pings_during_silent_first_chunk(self) -> None:
        """A slow first frame triggers at least one keep-alive ping.

        Simulates the Issue #128 cold-load: the source stays silent
        well past the interval before emitting its first real frame.
        The wrapper must inject pings during the gap and then deliver
        the real frame intact.
        """
        async def slow_source() -> AsyncIterator[str]:
            await asyncio.sleep(0.06)
            yield "event: done\ndata: {}\n\n"

        frames = await _collect(
            _with_keepalive(slow_source(), interval=0.02),
        )

        assert frames.count(_PING) >= 1
        # Real frame is preserved and arrives after the ping(s).
        assert frames[-1] == "event: done\ndata: {}\n\n"
        assert frames.index(_PING) < frames.index(
            "event: done\ndata: {}\n\n",
        )

    @pytest.mark.asyncio
    async def test_empty_source_emits_nothing_terminal(self) -> None:
        """An immediately-exhausted source ends without a trailing ping."""
        async def empty_source() -> AsyncIterator[str]:
            return
            yield  # pragma: no cover - makes this an async generator

        frames = await _collect(
            _with_keepalive(empty_source(), interval=0.02),
        )

        assert frames == []

    @pytest.mark.asyncio
    async def test_cancels_pending_anext_on_early_break(self) -> None:
        """Breaking out early cancels the in-flight ``__anext__``.

        The ``finally`` block must cancel a still-running pending
        future so a slow upstream doesn't leak a task after the
        client disconnects.
        """
        entered = asyncio.Event()

        async def hanging_source() -> AsyncIterator[str]:
            yield _PING  # first frame so we can break after it
            entered.set()
            await asyncio.sleep(3600)  # would hang forever
            yield "never"  # pragma: no cover

        gen = _with_keepalive(hanging_source(), interval=0.02)
        # Pull the first frame, then close the wrapper (as Starlette
        # does when the client disconnects).
        first = await gen.__anext__()
        assert first == _PING
        await gen.aclose()
        # No assertion on internals — the test passes if aclose()
        # returns without hanging or raising.


# ── prewarm_local_model ─────────────────────────────────────────────


def _mock_httpx_client(post_side_effect=None, post_return=None):
    """Build a mock ``httpx.AsyncClient`` usable as an async CM.

    Args:
        post_side_effect: Exception to raise from ``.post``.
        post_return: Response object ``.post`` should return.

    Returns:
        A tuple of (client_class_mock, post_asyncmock).
    """
    response = post_return or MagicMock()
    response.raise_for_status = MagicMock()
    post = AsyncMock(
        side_effect=post_side_effect,
        return_value=None if post_side_effect else response,
    )
    instance = MagicMock()
    instance.post = post
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    client_cls = MagicMock(return_value=instance)
    return client_cls, post


class TestPrewarmLocalModel:
    """Behaviour of the startup Ollama warm-up helper."""

    @pytest.mark.asyncio
    async def test_success_posts_keepalive_payload(self) -> None:
        """A successful warm-up returns True and posts the right body.

        Verifies the native ``/api/generate`` URL, the model tag, and
        a ``keep_alive`` that keeps the model resident.
        """
        client_cls, post = _mock_httpx_client()
        with patch(
            "app.expert.client.httpx.AsyncClient", client_cls,
        ):
            ok = await prewarm_local_model(
                endpoint="http://127.0.0.1:11434/",
                model="qwen3.5:27b-q8_0",
            )

        assert ok is True
        post.assert_awaited_once()
        called_url = post.await_args.args[0]
        called_json = post.await_args.kwargs["json"]
        # Trailing slash on endpoint must be normalised away.
        assert called_url == "http://127.0.0.1:11434/api/generate"
        assert called_json["model"] == "qwen3.5:27b-q8_0"
        assert called_json["keep_alive"] == "-1"
        assert called_json["stream"] is False

    @pytest.mark.asyncio
    async def test_failure_is_non_fatal(self) -> None:
        """A connection error returns False instead of raising.

        The warm-up is best-effort; the SSE keep-alive is the
        backstop, so a failure here must never propagate.
        """
        client_cls, _ = _mock_httpx_client(
            post_side_effect=RuntimeError("connection refused"),
        )
        with patch(
            "app.expert.client.httpx.AsyncClient", client_cls,
        ):
            ok = await prewarm_local_model(
                endpoint="http://127.0.0.1:11434",
                model="qwen3.5:27b-q8_0",
            )

        assert ok is False

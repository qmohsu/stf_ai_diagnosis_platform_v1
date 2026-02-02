"""Tests for obd_agent.api_poster."""

from __future__ import annotations

import pytest
import httpx
import respx

from obd_agent.api_poster import APIPoster
from obd_agent.config import AgentSettings
from obd_agent.schemas import AdapterInfo, OBDSnapshot

_URL = "http://test-api:8000/v1/telemetry/obd_snapshot"


def _make_snapshot() -> OBDSnapshot:
    return OBDSnapshot(
        vehicle_id="V-TEST-001",
        adapter=AdapterInfo(type="ELM327", port="sim"),
    )


def _make_settings(**overrides) -> AgentSettings:
    defaults = dict(
        obd_port="sim",
        vehicle_id="V-TEST-001",
        diagnostic_api_base_url="http://test-api:8000",
        max_retry_attempts=2,
        offline_buffer_max=5,
        dry_run=False,
    )
    defaults.update(overrides)
    return AgentSettings(**defaults)


@pytest.mark.asyncio
@respx.mock
async def test_post_success() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    poster = APIPoster(_make_settings())
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is True
        assert poster.buffer_size == 0
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_404_does_not_buffer() -> None:
    """404 means endpoint not built yet -- don't buffer."""
    respx.post(_URL).mock(return_value=httpx.Response(404))
    poster = APIPoster(_make_settings())
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is True
        assert poster.buffer_size == 0
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_501_does_not_buffer() -> None:
    """501 means endpoint not implemented -- don't buffer."""
    respx.post(_URL).mock(return_value=httpx.Response(501))
    poster = APIPoster(_make_settings())
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is True
        assert poster.buffer_size == 0
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_server_error_retries_and_buffers() -> None:
    """500 errors should retry exactly max_retry_attempts times, then buffer."""
    route = respx.post(_URL).mock(return_value=httpx.Response(500))
    settings = _make_settings(max_retry_attempts=2)
    poster = APIPoster(settings)
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is False
        assert poster.buffer_size == 1
        assert route.call_count == 2  # exactly max_retry_attempts
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_client_error_does_not_retry() -> None:
    """4xx errors (other than 404/501) should fail immediately, no retry."""
    route = respx.post(_URL).mock(return_value=httpx.Response(422))
    settings = _make_settings(max_retry_attempts=3)
    poster = APIPoster(settings)
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is False
        assert route.call_count == 1  # no retries for client errors
        assert poster.buffer_size == 0  # not buffered (payload is wrong)
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_buffer_drains_on_success() -> None:
    """Buffered snapshots should be drained when API recovers."""
    call_count = 0

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # First 2 calls: fail (retries for first snapshot)
        # After that: succeed
        if call_count <= 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    respx.post(_URL).mock(side_effect=_side_effect)
    settings = _make_settings(max_retry_attempts=2)
    poster = APIPoster(settings)
    await poster.start()
    try:
        # First post fails -> buffers
        r1 = await poster.post_snapshot(_make_snapshot())
        assert r1 is False
        assert poster.buffer_size == 1

        # Second post succeeds; buffer drains first
        r2 = await poster.post_snapshot(_make_snapshot())
        assert r2 is True
        assert poster.buffer_size == 0
    finally:
        await poster.close()


@pytest.mark.asyncio
async def test_dry_run_skips_http() -> None:
    """Dry-run mode should never make HTTP requests."""
    settings = _make_settings(dry_run=True)
    poster = APIPoster(settings)
    await poster.start()
    try:
        result = await poster.post_snapshot(_make_snapshot())
        assert result is True
        assert poster.buffer_size == 0
    finally:
        await poster.close()


@pytest.mark.asyncio
@respx.mock
async def test_buffer_overflow_drops_oldest() -> None:
    """When buffer is full, oldest snapshot is silently dropped (deque maxlen)."""
    respx.post(_URL).mock(return_value=httpx.Response(500))
    settings = _make_settings(max_retry_attempts=1, offline_buffer_max=3)
    poster = APIPoster(settings)
    await poster.start()
    try:
        for _ in range(5):
            await poster.post_snapshot(_make_snapshot())
        # deque(maxlen=3) keeps only the 3 most recent
        assert poster.buffer_size == 3
    finally:
        await poster.close()

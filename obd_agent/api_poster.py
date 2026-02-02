"""HTTP client that POSTs ``OBDSnapshot`` to diagnostic_api.

Features:
* Exponential-backoff retry (configurable max attempts).
* Offline buffer (bounded deque) drained on next successful POST.
* Graceful handling of 404/501 (endpoint not yet built).
* ``dry_run`` mode: validate locally, never POST.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque

import httpx
import structlog

from obd_agent.config import AgentSettings
from obd_agent.schemas import OBDSnapshot

logger = structlog.get_logger(__name__)

_ENDPOINT_PATH = "/v1/telemetry/obd_snapshot"


class APIPoster:
    """Sends OBD snapshots to the diagnostic API."""

    def __init__(self, settings: AgentSettings) -> None:
        self._base_url = settings.diagnostic_api_base_url.rstrip("/")
        self._max_retries = settings.max_retry_attempts
        self._dry_run = settings.dry_run
        # TODO: Persist buffer to disk (JSONL) so snapshots survive process
        # restarts.  Current in-memory deque loses data on crash/restart.
        self._buffer: Deque[str] = deque(maxlen=settings.offline_buffer_max)
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if not self._dry_run:
            self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- public API ---------------------------------------------------------

    async def post_snapshot(self, snapshot: OBDSnapshot) -> bool:
        """Post *snapshot* to the API.  Returns ``True`` on success.

        In dry-run mode the snapshot is validated and logged but never
        sent over the network.
        """
        payload = snapshot.model_dump_json()

        if self._dry_run:
            logger.info(
                "dry_run_snapshot",
                vehicle_id=snapshot.vehicle_id,
                dtc_count=len(snapshot.dtc),
                payload_bytes=len(payload),
            )
            return True

        # Attempt to drain any buffered snapshots first.
        await self._drain_buffer()

        result = await self._send_with_retry(payload)
        if result == "ok":
            return True
        if result == "buffer":
            self._buffer.append(payload)
            logger.warning(
                "snapshot_buffered",
                buffer_size=len(self._buffer),
                vehicle_id=snapshot.vehicle_id,
            )
        # result == "discard" -- client error, don't buffer
        return False

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    # -- internal -----------------------------------------------------------

    async def _drain_buffer(self) -> None:
        """Try to send buffered snapshots (FIFO order)."""
        sent = 0
        while self._buffer:
            oldest = self._buffer[0]
            result = await self._send_with_retry(oldest)
            if result != "ok":
                break
            self._buffer.popleft()
            sent += 1
        if sent:
            logger.info("buffer_drained", sent=sent, remaining=len(self._buffer))

    async def _send_with_retry(self, payload_json: str) -> str:
        """POST *payload_json* with exponential backoff.

        Returns:
            ``"ok"``      -- success or tolerated status (404/501).
            ``"buffer"``  -- retryable failure (5xx / network error).
            ``"discard"`` -- non-retryable client error (4xx).
        """
        url = f"{self._base_url}{_ENDPOINT_PATH}"

        for attempt in range(1, self._max_retries + 1):
            try:
                if self._client is None:
                    raise RuntimeError(
                        "APIPoster.start() must be called before sending"
                    )
                response = await self._client.post(
                    url,
                    content=payload_json,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code in (404, 501):
                    # Endpoint not yet built -- expected during early dev.
                    logger.info(
                        "endpoint_not_ready",
                        status=response.status_code,
                        url=url,
                    )
                    return "ok"

                if 400 <= response.status_code < 500:
                    # Client error -- retrying won't help, don't buffer.
                    logger.error(
                        "client_error",
                        status=response.status_code,
                        body=response.text[:500],
                    )
                    return "discard"

                response.raise_for_status()
                logger.info("snapshot_posted", status=response.status_code)
                return "ok"

            except httpx.HTTPStatusError as exc:
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "post_server_error",
                    attempt=attempt,
                    max_retries=self._max_retries,
                    status=exc.response.status_code,
                    retry_in=wait,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(wait)

            except httpx.RequestError as exc:
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "post_network_error",
                    attempt=attempt,
                    max_retries=self._max_retries,
                    error=str(exc),
                    retry_in=wait,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(wait)

        return "buffer"

"""Thread-safe in-memory cache for OBD analysis sessions.

Sessions are held here (24h TTL) until expert feedback promotes them to
Postgres.  A background asyncio task sweeps expired entries every 10 min.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

_DEFAULT_TTL_SECONDS: int = 86_400        # 24 hours
_SWEEP_INTERVAL_SECONDS: float = 600.0    # 10 minutes


@dataclass(frozen=True)
class CachedSession:
    """Immutable snapshot of an OBD analysis session stored in the cache."""

    session_id: str
    status: str
    vehicle_id: Optional[str]
    input_text_hash: str
    input_size_bytes: int
    raw_input_text: str
    result_payload: Optional[dict]
    parsed_summary_payload: Optional[dict]
    error_message: Optional[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_DEFAULT_MAX_SIZE: int = 500  # max cached sessions


class OBDSessionCache:
    """Thread-safe TTL cache for OBD analysis sessions."""

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: Dict[str, tuple[float, CachedSession]] = {}  # sid → (expire_ts, entry)
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def put(self, entry: CachedSession) -> None:
        """Insert or overwrite a session in the cache.

        If the cache is at capacity, the oldest entry is evicted.
        """
        expire_at = time.monotonic() + self._ttl
        with self._lock:
            if entry.session_id not in self._store and len(self._store) >= self._max_size:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[entry.session_id] = (expire_at, entry)

    def get(self, session_id: str) -> Optional[CachedSession]:
        """Return a cached session or *None* (lazy-evicts if expired)."""
        with self._lock:
            item = self._store.get(session_id)
            if item is None:
                return None
            expire_at, entry = item
            if time.monotonic() > expire_at:
                del self._store[session_id]
                return None
            return entry

    def pop(self, session_id: str) -> Optional[CachedSession]:
        """Atomically remove and return a cached session."""
        with self._lock:
            item = self._store.pop(session_id, None)
            if item is None:
                return None
            expire_at, entry = item
            if time.monotonic() > expire_at:
                return None
            return entry

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def sweep_expired(self) -> int:
        """Bulk-remove expired entries.  Returns count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (exp, _) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        return len(expired)

    async def start_cleanup_loop(self) -> None:
        """Start an asyncio background task that sweeps every 10 min."""
        if self._cleanup_task is not None:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
                self.sweep_expired()

        self._cleanup_task = asyncio.create_task(_loop())

    async def stop_cleanup_loop(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

"""In-memory session cache — singleton instance used across the app."""

from app.cache.obd_session_cache import CachedSession, OBDSessionCache

obd_cache = OBDSessionCache()

__all__ = ["obd_cache", "CachedSession", "OBDSessionCache"]

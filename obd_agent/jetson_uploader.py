"""Reference HTTP client for end-of-trip OBD log uploads.

This is the recommended Jetson-side integration pattern: log a complete
trip to a CSV/TSV file on disk, then push the whole file to the
diagnostic API once the trip ends.  The backend pipeline handles the
1 Hz time-series windowing, anomaly detection, and clue generation —
the device just needs to deliver the bytes.

This is the platform's sole edge ingestion path (GitHub issue #76).
The legacy per-snapshot transport (``api_poster.APIPoster``, targeting
the never-deployed ``/v1/telemetry/obd_snapshot``) was removed under
APP-53 cleanup.

Typical usage from a shell::

    python -m obd_agent.jetson_uploader \\
        --base-url https://stf-diagnosis.dev \\
        --username perry \\
        --password '...' \\
        --manufacturer Toyota \\
        --model Hiace \\
        --log-file /var/log/obd/trip_20260505_164119.csv

The vehicle make/model are required (APP-60) — a model cannot be derived
from an OBD log.  The device is installed in one fixed vehicle, so set
them once via ``--manufacturer`` / ``--model`` or the ``STF_MANUFACTURER``
/ ``STF_MODEL`` env vars; they are sent as query params on every upload.

The script writes the resulting ``session_id`` to stdout on success
and exits non-zero on failure.  Token caching is intentionally not
implemented; long-lived deployments should re-issue ``/auth/login``
once per upload, which is cheap.

V3 dual push (PROD-07)
----------------------
When a V3 env file exists (default ``~/.config/stf/v3_uploader.env``,
override with ``--v3-env-file`` or ``STF_V3_ENV_FILE``) the same trip
file is ALSO pushed to the V3 device endpoint (``POST /v3/ingest/device``
with ``X-Device-Token``), *after* the V2 upload above.  The V2 leg is
byte-for-byte what it was before PROD-07; the two legs succeed or fail
independently.  Without the env file the script behaves exactly as
before (V2 only) — that is also the rollback path.

Env file keys (``KEY=VALUE`` lines, ``#`` comments; the token is never
accepted as a CLI flag so it cannot land in shell history)::

    STF_V3_BASE_URL=https://stf-diagnosis.dev
    STF_V3_DEVICE_TOKEN=<issued by a workshop manager, shown once>
    STF_V3_VEHICLE_ID=<uuid of the vehicle this device sits in>   # optional
    STF_V3_SPOOL_DIR=/path/for/pending/uploads                   # optional

V3 leg behaviour:

* transport errors / 5xx → retried 3× (5 s, 15 s, 45 s); still failing
  → the file is copied into ``<spool>/pending/`` and retried by the next
  run (``--drain`` pushes the backlog only; suitable for a cron entry).
* ``200`` (already stored) and ``201`` both count as success; a pending
  copy is deleted.
* ``413`` / ``422`` (the file itself is refused) → moved to
  ``<spool>/rejected/`` with a ``.error.txt`` beside it; never retried.
* ``401`` (token invalid or revoked) is a configuration problem: the
  file stays pending, the run stops, exit code 2.
* the response's ``vehicle_id`` is compared with ``STF_V3_VEHICLE_ID``
  when set; a mismatch is reported (exit 2) and a copy kept in
  ``rejected/`` so nothing is silently filed under the wrong car.

Exit codes: ``0`` = V2 succeeded and the V3 leg stored, deduplicated,
spooled or is disabled; ``1`` = V2 failed (V3 outcome still logged);
``2`` = V2 succeeded but the V3 leg was rejected, misconfigured or hit a
vehicle mismatch.  ``--self-check`` verifies config + reachability
without uploading; ``--drain`` only pushes the backlog.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import shutil
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: float = 60.0
_LOGIN_PATH: str = "/auth/login"
_ANALYZE_PATH: str = "/v2/obd/analyze"

# ---- V3 (PROD-07) constants -------------------------------------------
_V3_INGEST_PATH: str = "/v3/ingest/device"
_V3_HEALTH_PATH: str = "/v3/health"
_V3_RETRY_DELAYS: tuple = (5.0, 15.0, 45.0)
_V3_DRAIN_BACKOFF_MINUTES: tuple = (10, 20, 40, 60)
_V3_SPOOL_MAX_FILES: int = 200
_V3_SPOOL_MAX_BYTES: int = 500 * 1024 * 1024
_V3_LOCAL_FAILURES_MAX: int = 3
_V3_WRITE_TIMEOUT_CAP_S: float = 600.0
_V3_LOCK_STALE_S: float = 3600.0
_DEFAULT_V3_ENV_FILE = Path.home() / ".config" / "stf" / "v3_uploader.env"

OUTCOME_DISABLED = "disabled"
OUTCOME_STORED = "stored"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_SPOOLED = "spooled"
OUTCOME_REJECTED = "rejected"
OUTCOME_CONFIG_ERROR = "config_error"
OUTCOME_VEHICLE_MISMATCH = "vehicle_mismatch"
OUTCOME_SPOOL_FULL = "spool_full"

_V3_FAILURE_OUTCOMES = frozenset(
    {OUTCOME_REJECTED, OUTCOME_CONFIG_ERROR, OUTCOME_VEHICLE_MISMATCH, OUTCOME_SPOOL_FULL}
)


class UploadError(Exception):
    """Raised when an upload step fails for a reason worth surfacing."""


# =====================================================================
# V2 leg — unchanged since APP-60 (PROD-07 must not alter a byte here)
# =====================================================================


def login(
    client: httpx.Client,
    base_url: str,
    username: str,
    password: str,
) -> str:
    """Exchange username + password for a JWT access token.

    Args:
        client: An open ``httpx.Client``.
        base_url: Base URL of the diagnostic API
            (e.g. ``https://stf-diagnosis.dev``).
        username: Account username.
        password: Account password.

    Returns:
        The bearer access token.

    Raises:
        UploadError: If authentication fails.
    """
    url = base_url.rstrip("/") + _LOGIN_PATH
    response = client.post(
        url,
        data={"username": username, "password": password},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    if response.status_code != 200:
        raise UploadError(
            f"Login failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    body = response.json()
    token = body.get("access_token")
    if not token or not isinstance(token, str):
        raise UploadError(
            "Login response missing 'access_token'."
        )
    return token


def upload_log(
    client: httpx.Client,
    base_url: str,
    token: str,
    log_path: Path,
    manufacturer: str,
    vehicle_model: str,
) -> str:
    """POST a trip log file to ``/v2/obd/analyze``.

    The whole file is sent as the request body — the backend
    auto-detects the format (native TSV, OBDWIZ CSVLog, obd_maxlog,
    Yamaha dual-channel CSV, or generic CSV) and runs the full
    pipeline.

    Args:
        client: An open ``httpx.Client``.
        base_url: Base URL of the diagnostic API.
        token: JWT access token from :func:`login`.
        log_path: Path to the trip log file on disk.

    Returns:
        The ``session_id`` returned by the API.

    Raises:
        UploadError: If the upload fails or the response is malformed.
        FileNotFoundError: If *log_path* does not exist.
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    body = log_path.read_bytes()
    url = base_url.rstrip("/") + _ANALYZE_PATH
    # APP-60: manufacturer + vehicle_model are required query params.
    response = client.post(
        url,
        content=body,
        params={
            "manufacturer": manufacturer,
            "vehicle_model": vehicle_model,
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

    if response.status_code != 200:
        raise UploadError(
            f"Upload failed: {response.status_code} "
            f"{response.text[:500]}"
        )

    payload = response.json()
    session_id = payload.get("session_id")
    if not session_id or not isinstance(session_id, str):
        raise UploadError(
            "Upload response missing 'session_id'."
        )
    return session_id


def upload_trip(
    base_url: str,
    username: str,
    password: str,
    log_path: Path,
    manufacturer: str,
    vehicle_model: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Single-call helper: log in, upload, return ``session_id``.

    Args:
        base_url: Base URL of the diagnostic API.
        username: Account username.
        password: Account password.
        log_path: Path to the trip log file on disk.
        manufacturer: Vehicle manufacturer the device is installed in
            (required by the API — APP-60).
        vehicle_model: Vehicle model (required by the API — APP-60).
        timeout_seconds: Per-request HTTP timeout.

    Returns:
        The ``session_id`` returned by the API.
    """
    with httpx.Client(timeout=timeout_seconds) as client:
        token = login(client, base_url, username, password)
        return upload_log(
            client, base_url, token, log_path,
            manufacturer, vehicle_model,
        )


# =====================================================================
# V3 leg (PROD-07)
# =====================================================================


class V3ConfigError(Exception):
    """The V3 env file exists but is unusable (missing keys, bad values)."""


class SpoolFull(Exception):
    """The pending directory reached its file/byte cap."""


@dataclass(frozen=True)
class V3Config:
    """Settings read from the V3 env file.

    Attributes:
        base_url: V3 base URL (same host as V2 in the current deployment).
        device_token: Per-vehicle device credential (never logged).
        expected_vehicle_id: Optional vehicle UUID; when set every V3
            response is checked against it.
        spool_dir: Root of ``pending/``, ``rejected/``, the run log and
            the backoff state file.
        env_file: Where the config came from (for log lines only).
    """

    base_url: str
    device_token: str
    expected_vehicle_id: Optional[str]
    spool_dir: Path
    env_file: Path


@dataclass
class V3Result:
    """Outcome of one file on the V3 leg."""

    outcome: str
    status_code: Optional[int] = None
    detail: str = ""
    log_id: Optional[str] = None
    vehicle_id: Optional[str] = None


@dataclass
class DrainSummary:
    """What a backlog pass did."""

    uploaded: int = 0
    rejected: int = 0
    skipped_backoff: bool = False
    stopped: Optional[str] = None      # None | "network" | "config"
    outcomes: List[V3Result] = field(default_factory=list)


def _parse_env_file(text: str) -> Dict[str, str]:
    """Parses ``KEY=VALUE`` lines (``export`` prefix, quotes and ``#``
    comments tolerated)."""
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_v3_config(env_file: Path) -> Optional[V3Config]:
    """Reads the V3 env file.

    Returns:
        ``None`` when the file does not exist (V3 leg disabled — the
        documented rollback), else a :class:`V3Config`.

    Raises:
        V3ConfigError: File present but ``STF_V3_BASE_URL`` /
            ``STF_V3_DEVICE_TOKEN`` missing, or the vehicle id malformed.
    """
    if not env_file.exists():
        return None
    values = _parse_env_file(env_file.read_text(encoding="utf-8"))
    base_url = values.get("STF_V3_BASE_URL", "").strip().rstrip("/")
    token = values.get("STF_V3_DEVICE_TOKEN", "").strip()
    if not base_url or not token:
        raise V3ConfigError(
            f"{env_file}: STF_V3_BASE_URL and STF_V3_DEVICE_TOKEN are required"
        )
    expected = values.get("STF_V3_VEHICLE_ID", "").strip() or None
    if expected is not None:
        try:
            expected = str(uuid.UUID(expected))
        except ValueError as exc:
            raise V3ConfigError(
                f"{env_file}: STF_V3_VEHICLE_ID is not a UUID"
            ) from exc
    spool = values.get("STF_V3_SPOOL_DIR", "").strip()
    spool_dir = Path(spool).expanduser() if spool else env_file.parent / "spool"
    _warn_if_world_readable(env_file)
    return V3Config(
        base_url=base_url,
        device_token=token,
        expected_vehicle_id=expected,
        spool_dir=spool_dir,
        env_file=env_file,
    )


def _warn_if_world_readable(env_file: Path) -> None:
    """FM-22: the env file holds the device token; warn on loose modes."""
    if os.name != "posix":
        return
    mode = stat.S_IMODE(env_file.stat().st_mode)
    if mode & 0o077:
        logger.warning(
            "v3_env_file_permissions: %s is mode %o; run: chmod 600 %s",
            env_file, mode, env_file,
        )


def v3_timeout(size_bytes: int) -> httpx.Timeout:
    """Write timeout grows with the file (FM-36): 60 s + 10 s per MB,
    capped at 10 min.  Connect/read stay at the default 60 s."""
    write = min(
        _V3_WRITE_TIMEOUT_CAP_S,
        _DEFAULT_TIMEOUT_SECONDS + 10.0 * (size_bytes / (1024 * 1024)),
    )
    return httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS, write=write)


class Spool:
    """On-device backlog for the V3 leg.

    Layout under ``root``: ``pending/`` (files still to push),
    ``rejected/`` (files the server refused + ``<name>.error.txt``),
    ``uploader.log`` (rotating run log), ``state.json`` (drain backoff +
    per-file local failure counts) and ``lock``.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.pending_dir = root / "pending"
        self.rejected_dir = root / "rejected"
        self.log_path = root / "uploader.log"
        self.state_path = root / "state.json"
        self.lock_path = root / "lock"

    def ensure(self) -> None:
        """Creates the directories (idempotent)."""
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)

    # -- pending ------------------------------------------------------

    def pending(self) -> List[Path]:
        """Completed pending files, oldest name first (temp names hidden)."""
        return sorted(
            p for p in self.pending_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )

    def usage(self) -> tuple:
        """Returns ``(file_count, total_bytes)`` of the pending dir."""
        files = self.pending()
        return len(files), sum(p.stat().st_size for p in files)

    def add(self, source: Path) -> Path:
        """Copies ``source`` into ``pending/`` atomically (FM-1).

        The copy is written under a dot-prefixed temp name and renamed
        only when complete, so a power cut never leaves a half file that
        a later drain could upload.  The original is never touched.

        Raises:
            SpoolFull: Cap reached (FM-12); the original stays where the
                logger wrote it.
        """
        self.ensure()
        count, total = self.usage()
        size = source.stat().st_size
        if count >= _V3_SPOOL_MAX_FILES or total + size > _V3_SPOOL_MAX_BYTES:
            raise SpoolFull(
                f"pending dir holds {count} files / {total} bytes; "
                f"cap {_V3_SPOOL_MAX_FILES} files / {_V3_SPOOL_MAX_BYTES} bytes"
            )
        target = self.pending_dir / self._unique_name(source.name)
        tmp = self.pending_dir / (".tmp-" + target.name)
        shutil.copyfile(str(source), str(tmp))
        os.replace(str(tmp), str(target))
        return target

    def _unique_name(self, name: str) -> str:
        candidate = name
        n = 1
        while (self.pending_dir / candidate).exists():
            n += 1
            stem, dot, ext = name.rpartition(".")
            candidate = f"{stem}-{n}.{ext}" if dot else f"{name}-{n}"
        return candidate

    def remove(self, path: Path) -> None:
        """Deletes a pending copy after the server confirmed it."""
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # -- rejected -----------------------------------------------------

    def reject(self, path: Path, reason: str, keep_source: bool = False) -> Path:
        """Moves (or copies, when ``keep_source``) a file into
        ``rejected/`` and writes ``<name>.error.txt`` beside it."""
        self.ensure()
        target = self.rejected_dir / path.name
        n = 1
        while target.exists():
            n += 1
            target = self.rejected_dir / f"{path.stem}-{n}{path.suffix}"
        if keep_source:
            shutil.copyfile(str(path), str(target))
        else:
            shutil.move(str(path), str(target))
        (self.rejected_dir / (target.name + ".error.txt")).write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n", encoding="utf-8"
        )
        return target

    # -- state ---------------------------------------------------------

    def load_state(self) -> Dict[str, object]:
        """Reads ``state.json`` (missing/corrupt → empty state)."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def save_state(self, state: Dict[str, object]) -> None:
        """Writes ``state.json`` atomically."""
        self.ensure()
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(self.state_path))


class RunLock:
    """One uploader at a time per spool (FM-9).

    Uses ``flock`` where available (auto-released if the process dies);
    elsewhere an exclusive-create lock file with a staleness cutoff.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Returns True when the lock was obtained, False if held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl  # type: ignore[import-not-found]
        except ImportError:
            fcntl = None
        if fcntl is not None:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
            self._fd = fd
            return True
        # Fallback (Windows dev boxes): O_EXCL + stale cutoff.
        try:
            age = time.time() - self.path.stat().st_mtime
            if age > _V3_LOCK_STALE_S:
                self.path.unlink()
        except FileNotFoundError:
            pass
        try:
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def release(self) -> None:
        """Releases the lock (no-op when not held)."""
        if self._fd is None:
            return
        os.close(self._fd)
        self._fd = None
        if os.name != "posix":
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _v3_headers(cfg: V3Config) -> Dict[str, str]:
    return {"X-Device-Token": cfg.device_token}


def _classify(cfg: V3Config, response: httpx.Response) -> V3Result:
    """Maps one V3 HTTP response to a :class:`V3Result`.

    5xx is reported as ``spooled`` (transient) and left to the caller to
    retry or spool; the caller decides, this only classifies.
    """
    code = response.status_code
    if code in (200, 201):
        try:
            body = response.json()
        except ValueError:
            body = {}
        log_id = body.get("id")
        vehicle_id = body.get("vehicle_id")
        if cfg.expected_vehicle_id and vehicle_id != cfg.expected_vehicle_id:
            return V3Result(
                OUTCOME_VEHICLE_MISMATCH, code,
                f"server filed the log under vehicle {vehicle_id}, "
                f"config expects {cfg.expected_vehicle_id}",
                log_id, vehicle_id,
            )
        outcome = OUTCOME_DUPLICATE if code == 200 else OUTCOME_STORED
        return V3Result(outcome, code, "", log_id, vehicle_id)
    if code == 401:
        return V3Result(
            OUTCOME_CONFIG_ERROR, code,
            "device token invalid or revoked (401); fix the env file",
        )
    if 400 <= code < 500:
        return V3Result(OUTCOME_REJECTED, code, response.text[:300])
    return V3Result(OUTCOME_SPOOLED, code, f"server error {code}")


def v3_upload_once(client: httpx.Client, cfg: V3Config, path: Path) -> V3Result:
    """One attempt at ``POST /v3/ingest/device`` (no retry).

    Raises:
        httpx.TransportError: Network-level failure (caller retries/spools).
    """
    data = path.read_bytes()
    response = client.post(
        cfg.base_url + _V3_INGEST_PATH,
        files={"file": (path.name, data, "application/octet-stream")},
        headers=_v3_headers(cfg),
        timeout=v3_timeout(len(data)),
    )
    return _classify(cfg, response)


def v3_upload_with_retry(
    client: httpx.Client,
    cfg: V3Config,
    path: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> V3Result:
    """Trip-mode upload: transport errors and 5xx are retried with
    ``_V3_RETRY_DELAYS``; any 4xx returns immediately (FM-11).

    Returns:
        The last :class:`V3Result`; a ``spooled`` outcome means every
        attempt failed transiently and the caller should spool the file.
    """
    last: Optional[V3Result] = None
    for attempt, delay in enumerate((None,) + _V3_RETRY_DELAYS):
        if delay is not None:
            logger.info("v3_retry: attempt %d in %.0fs", attempt + 1, delay)
            sleep(delay)
        try:
            last = v3_upload_once(client, cfg, path)
        except httpx.TransportError as exc:
            last = V3Result(OUTCOME_SPOOLED, None, f"network error: {exc}")
            continue
        if last.outcome != OUTCOME_SPOOLED:
            return last
    assert last is not None
    return last


def _backoff_state(state: Dict[str, object]) -> tuple:
    failures = int(state.get("consecutive_failures", 0) or 0)
    next_after = float(state.get("next_drain_after", 0) or 0)
    return failures, next_after


def drain(
    cfg: V3Config,
    spool: Spool,
    client: httpx.Client,
    now: Callable[[], float] = time.time,
    force: bool = False,
) -> DrainSummary:
    """Pushes the pending backlog, one file at a time (FM-8, FM-21).

    * each file is handled independently; a confirmed file is deleted at
      once, a refused one moved to ``rejected/``;
    * the first network error (or 5xx) stops the pass — the rest waits
      for the next run — and bumps the drain backoff
      (10 → 20 → 40 → 60 min, FM-11); a clean pass resets it;
    * ``401`` stops the pass with ``stopped="config"``, files untouched;
    * a local exception on a file counts as one failure; after
      ``_V3_LOCAL_FAILURES_MAX`` the file is moved to ``rejected/``.
    """
    summary = DrainSummary()
    spool.ensure()
    state = spool.load_state()
    failures, next_after = _backoff_state(state)
    if not force and now() < next_after:
        summary.skipped_backoff = True
        logger.info(
            "v3_drain_skipped: backing off until %s",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_after)),
        )
        return summary
    local_failures: Dict[str, int] = dict(state.get("local_failures", {}) or {})

    for path in spool.pending():
        try:
            result = v3_upload_once(client, cfg, path)
        except httpx.TransportError as exc:
            result = V3Result(OUTCOME_SPOOLED, None, f"network error: {exc}")
        except Exception as exc:  # noqa: BLE001 - local fault on this file
            n = local_failures.get(path.name, 0) + 1
            local_failures[path.name] = n
            logger.error("v3_drain_local_error: %s (%d/%d): %s",
                         path.name, n, _V3_LOCAL_FAILURES_MAX, exc)
            if n >= _V3_LOCAL_FAILURES_MAX:
                spool.reject(path, f"local error x{n}: {exc}")
                local_failures.pop(path.name, None)
                summary.rejected += 1
            continue
        summary.outcomes.append(result)
        if result.outcome == OUTCOME_SPOOLED:
            summary.stopped = "network"
            logger.warning("v3_drain_stopped: %s (%s stays pending)",
                           result.detail, path.name)
            break
        if result.outcome == OUTCOME_CONFIG_ERROR:
            summary.stopped = "config"
            logger.error("v3_drain_stopped: %s", result.detail)
            break
        if result.outcome in (OUTCOME_STORED, OUTCOME_DUPLICATE):
            spool.remove(path)
            local_failures.pop(path.name, None)
            summary.uploaded += 1
            logger.info("v3_%s: %s log_id=%s", result.outcome, path.name, result.log_id)
        elif result.outcome == OUTCOME_VEHICLE_MISMATCH:
            spool.reject(path, result.detail)
            summary.rejected += 1
            logger.error("v3_vehicle_mismatch: %s: %s", path.name, result.detail)
        else:  # rejected
            spool.reject(path, f"HTTP {result.status_code}: {result.detail}")
            local_failures.pop(path.name, None)
            summary.rejected += 1
            logger.warning("v3_rejected: %s HTTP %s: %s",
                           path.name, result.status_code, result.detail)

    if summary.stopped == "network":
        failures += 1
        minutes = _V3_DRAIN_BACKOFF_MINUTES[
            min(failures, len(_V3_DRAIN_BACKOFF_MINUTES)) - 1
        ]
        state["consecutive_failures"] = failures
        state["next_drain_after"] = now() + minutes * 60
    elif summary.stopped is None:
        state["consecutive_failures"] = 0
        state["next_drain_after"] = 0
    state["local_failures"] = local_failures
    spool.save_state(state)
    return summary


def v3_push_trip(
    cfg: V3Config,
    spool: Spool,
    log_path: Path,
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> V3Result:
    """Trip mode for the V3 leg: drain the backlog first, then push the
    new file (with retries).  When the drain hit a network error the new
    file is spooled straight away instead of burning three more retries.
    """
    summary = drain(cfg, spool, client, now=now)
    if summary.stopped == "network":
        return _spool_or_full(spool, log_path, "network down during drain")
    if summary.stopped == "config":
        result = _spool_or_full(spool, log_path, "token rejected during drain")
        if result.outcome == OUTCOME_SPOOLED:
            return V3Result(OUTCOME_CONFIG_ERROR, 401,
                            "device token invalid or revoked (401); file kept pending")
        return result

    result = v3_upload_with_retry(client, cfg, log_path, sleep=sleep)
    if result.outcome == OUTCOME_SPOOLED:
        return _spool_or_full(spool, log_path, result.detail)
    if result.outcome == OUTCOME_CONFIG_ERROR:
        spooled = _spool_or_full(spool, log_path, result.detail)
        return result if spooled.outcome == OUTCOME_SPOOLED else spooled
    if result.outcome in (OUTCOME_REJECTED, OUTCOME_VEHICLE_MISMATCH):
        spool.reject(log_path, f"HTTP {result.status_code}: {result.detail}",
                     keep_source=True)
    return result


def _spool_or_full(spool: Spool, log_path: Path, why: str) -> V3Result:
    try:
        target = spool.add(log_path)
    except SpoolFull as exc:
        logger.error("v3_spool_full: %s (%s not spooled)", exc, log_path.name)
        return V3Result(OUTCOME_SPOOL_FULL, None, str(exc))
    logger.warning("v3_spooled: %s -> %s (%s)", log_path.name, target, why)
    return V3Result(OUTCOME_SPOOLED, None, why)


def self_check(env_file: Path, client_factory: Callable[[], httpx.Client] = httpx.Client) -> int:
    """``--self-check``: config present, spool writable, V3 reachable.

    Never uploads.  Returns 0 when everything passes, else 1 (FM-15,
    FM-34).  The token itself cannot be verified without an upload; use
    a real trip log or a copy of an old one for that step (see the
    install guide).
    """
    try:
        cfg = load_v3_config(env_file)
    except V3ConfigError as exc:
        print(f"FAIL  v3 env file unusable: {exc}")
        return 1
    if cfg is None:
        print(f"FAIL  v3 env file not found: {env_file}")
        return 1
    print(f"OK    v3 env file: {cfg.env_file}")
    print(f"OK    v3 base url: {cfg.base_url}")
    print(f"OK    expected vehicle: {cfg.expected_vehicle_id or '(not set — recommended)'}")
    spool = Spool(cfg.spool_dir)
    try:
        spool.ensure()
        probe = spool.root / ".probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        print(f"FAIL  spool dir not writable: {cfg.spool_dir}: {exc}")
        return 1
    count, total = spool.usage()
    rejected = len([p for p in spool.rejected_dir.iterdir() if p.is_file()])
    print(f"OK    spool dir: {cfg.spool_dir} (pending {count} files / {total} bytes, "
          f"rejected {rejected} files)")
    try:
        with client_factory() as client:
            r = client.get(cfg.base_url + _V3_HEALTH_PATH, timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"FAIL  v3 unreachable: {exc}")
        return 1
    if r.status_code != 200:
        print(f"FAIL  v3 health returned {r.status_code}")
        return 1
    print("OK    v3 reachable (health 200)")
    return 0


def _install_run_log(spool: Spool) -> None:
    """Rotating per-device run log beside the spool (FM-18)."""
    spool.ensure()
    handler = logging.handlers.RotatingFileHandler(
        str(spool.log_path), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)


# =====================================================================
# CLI
# =====================================================================


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="obd_agent.jetson_uploader",
        description=(
            "Upload an end-of-trip OBD log file to the diagnostic API "
            "(V2), and to V3 when a V3 env file is present."
        ),
    )
    parser.add_argument(
        "--base-url",
        help="API base URL (e.g. https://stf-diagnosis.dev).",
    )
    parser.add_argument(
        "--username",
        help="Diagnostic API account username.",
    )
    parser.add_argument(
        "--password",
        help=(
            "Diagnostic API account password.  Pass via stdin or "
            "env-substitution to avoid leaking into shell history."
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to the OBD trip log file.",
    )
    # APP-60: vehicle make/model are required by the API.  The device
    # is installed in one fixed vehicle, so configure these once (flag
    # or STF_MANUFACTURER / STF_MODEL env) and they ride every upload.
    parser.add_argument(
        "--manufacturer",
        default=os.environ.get("STF_MANUFACTURER"),
        help=(
            "Vehicle manufacturer (e.g. 'Toyota').  Required; may also "
            "be set via the STF_MANUFACTURER env var."
        ),
    )
    parser.add_argument(
        "--model",
        dest="vehicle_model",
        default=os.environ.get("STF_MODEL"),
        help=(
            "Vehicle model (e.g. 'Hiace').  Required; may also be set "
            "via the STF_MODEL env var."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout per request, seconds (default: 60).",
    )
    # PROD-07: V3 leg.  The token lives ONLY in the env file.
    parser.add_argument(
        "--v3-env-file",
        type=Path,
        default=Path(os.environ.get("STF_V3_ENV_FILE") or _DEFAULT_V3_ENV_FILE),
        help=(
            "V3 env file (STF_V3_BASE_URL, STF_V3_DEVICE_TOKEN, ...).  "
            "Absent file = V3 leg disabled.  Default: "
            "$STF_V3_ENV_FILE or ~/.config/stf/v3_uploader.env."
        ),
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help="Only push the V3 backlog (no V2 upload, no --log-file); "
             "meant for a cron entry.",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Verify the V3 config, spool dir and reachability; never uploads.",
    )
    return parser.parse_args(argv)


def _require(args: argparse.Namespace, names: List[str]) -> bool:
    missing = [n for n in names if getattr(args, n.replace("-", "_"), None) in (None, "")]
    if missing:
        logger.error("missing_arguments: --%s required for a trip upload",
                     ", --".join(missing))
        return False
    return True


def _run_drain(env_file: Path) -> int:
    try:
        cfg = load_v3_config(env_file)
    except V3ConfigError as exc:
        logger.error("v3_config_error: %s", exc)
        return 2
    if cfg is None:
        logger.error("v3_disabled: env file not found: %s (nothing to drain)", env_file)
        return 2
    spool = Spool(cfg.spool_dir)
    _install_run_log(spool)
    lock = RunLock(spool.lock_path)
    if not lock.acquire():
        logger.info("v3_locked: another uploader is running; exiting")
        return 0
    try:
        with httpx.Client() as client:
            summary = drain(cfg, spool, client)
    finally:
        lock.release()
    logger.info("v3_drain_done: uploaded=%d rejected=%d stopped=%s skipped=%s",
                summary.uploaded, summary.rejected, summary.stopped,
                summary.skipped_backoff)
    return 2 if summary.stopped == "config" else 0


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` on success, ``1`` when the V2 upload failed, ``2`` when V2
        succeeded but the V3 leg was rejected / misconfigured.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)

    if args.self_check:
        return self_check(args.v3_env_file)
    if args.drain:
        return _run_drain(args.v3_env_file)

    if not _require(args, ["base-url", "username", "password", "log-file"]):
        return 1

    # APP-60: fail loudly and locally when the device's vehicle identity
    # is not configured, rather than sending a request the API will 422.
    manufacturer = (args.manufacturer or "").strip()
    vehicle_model = (args.vehicle_model or "").strip()
    if not manufacturer or not vehicle_model:
        logger.error(
            "vehicle_identity_missing: --manufacturer and --model "
            "(or STF_MANUFACTURER / STF_MODEL) are required.",
        )
        return 1

    # ---- V3 config first, so the very first log line says whether the
    # V3 leg is on (FM-34); the V2 leg below is unchanged.
    v3_cfg: Optional[V3Config] = None
    v3_result = V3Result(OUTCOME_DISABLED)
    try:
        v3_cfg = load_v3_config(args.v3_env_file)
    except V3ConfigError as exc:
        v3_result = V3Result(OUTCOME_CONFIG_ERROR, None, str(exc))
        logger.error("v3: enabled but unusable: %s", exc)
    if v3_cfg is not None:
        logger.info("v3: enabled base_url=%s expected_vehicle=%s spool=%s",
                    v3_cfg.base_url, v3_cfg.expected_vehicle_id or "-", v3_cfg.spool_dir)
        _install_run_log(Spool(v3_cfg.spool_dir))
    elif v3_result.outcome == OUTCOME_DISABLED:
        logger.info("v3: disabled (env file not found: %s)", args.v3_env_file)

    # ---- V2 leg (today's behaviour, byte for byte) ----------------------
    v2_ok = False
    session_id = ""
    try:
        session_id = upload_trip(
            base_url=args.base_url,
            username=args.username,
            password=args.password,
            log_path=args.log_file,
            manufacturer=manufacturer,
            vehicle_model=vehicle_model,
            timeout_seconds=args.timeout,
        )
        v2_ok = True
    except FileNotFoundError as exc:
        logger.error("log_file_missing: %s", exc)
        return 1
    except UploadError as exc:
        logger.error("upload_failed: %s", exc)
    except httpx.HTTPError as exc:
        logger.error("network_error: %s", exc)

    # ---- V3 leg ---------------------------------------------------------
    if v3_cfg is not None:
        spool = Spool(v3_cfg.spool_dir)
        lock = RunLock(spool.lock_path)
        if not lock.acquire():
            logger.warning("v3_locked: another uploader is running; spooling %s",
                           args.log_file.name)
            v3_result = _spool_or_full(spool, args.log_file, "lock held")
        else:
            try:
                with httpx.Client() as client:
                    v3_result = v3_push_trip(v3_cfg, spool, args.log_file, client)
            finally:
                lock.release()
        logger.info("v3_result: %s status=%s log_id=%s %s",
                    v3_result.outcome, v3_result.status_code,
                    v3_result.log_id, v3_result.detail)

    if not v2_ok:
        return 1
    print(session_id)
    return 2 if v3_result.outcome in _V3_FAILURE_OUTCOMES else 0


if __name__ == "__main__":
    sys.exit(main())

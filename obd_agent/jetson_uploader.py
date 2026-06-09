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
        --log-file /var/log/obd/trip_20260505_164119.csv

The script writes the resulting ``session_id`` to stdout on success
and exits non-zero on failure.  Token caching is intentionally not
implemented; long-lived deployments should re-issue ``/auth/login``
once per upload, which is cheap.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: float = 60.0
_LOGIN_PATH: str = "/auth/login"
_ANALYZE_PATH: str = "/v2/obd/analyze"


class UploadError(Exception):
    """Raised when an upload step fails for a reason worth surfacing."""


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
    response = client.post(
        url,
        content=body,
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
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Single-call helper: log in, upload, return ``session_id``.

    Args:
        base_url: Base URL of the diagnostic API.
        username: Account username.
        password: Account password.
        log_path: Path to the trip log file on disk.
        timeout_seconds: Per-request HTTP timeout.

    Returns:
        The ``session_id`` returned by the API.
    """
    with httpx.Client(timeout=timeout_seconds) as client:
        token = login(client, base_url, username, password)
        return upload_log(client, base_url, token, log_path)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="obd_agent.jetson_uploader",
        description=(
            "Upload an end-of-trip OBD log file to the diagnostic API."
        ),
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="API base URL (e.g. https://stf-diagnosis.dev).",
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Diagnostic API account username.",
    )
    parser.add_argument(
        "--password",
        required=True,
        help=(
            "Diagnostic API account password.  Pass via stdin or "
            "env-substitution to avoid leaking into shell history."
        ),
    )
    parser.add_argument(
        "--log-file",
        required=True,
        type=Path,
        help="Path to the OBD trip log file.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout per request, seconds (default: 60).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)

    try:
        session_id = upload_trip(
            base_url=args.base_url,
            username=args.username,
            password=args.password,
            log_path=args.log_file,
            timeout_seconds=args.timeout,
        )
    except FileNotFoundError as exc:
        logger.error("log_file_missing: %s", exc)
        return 1
    except UploadError as exc:
        logger.error("upload_failed: %s", exc)
        return 1
    except httpx.HTTPError as exc:
        logger.error("network_error: %s", exc)
        return 1

    print(session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

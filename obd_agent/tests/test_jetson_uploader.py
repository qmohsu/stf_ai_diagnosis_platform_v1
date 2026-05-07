"""Tests for the Jetson reference upload client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from obd_agent.jetson_uploader import (
    UploadError,
    login,
    main,
    upload_log,
    upload_trip,
)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.Client:
    """Build an ``httpx.Client`` backed by a custom request handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── login ────────────────────────────────────────────────────────────


class TestLogin:
    """JWT exchange via ``/auth/login``."""

    def test_login_returns_token(self) -> None:
        """Successful 200 response yields the access_token field."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/auth/login"
            assert (
                request.headers["content-type"]
                == "application/x-www-form-urlencoded"
            )
            assert b"username=perry" in request.content
            return httpx.Response(
                200, json={"access_token": "tok-xyz", "token_type": "bearer"},
            )

        with _make_client(handler) as client:
            token = login(
                client,
                "https://example.invalid",
                username="perry",
                password="secret",
            )
        assert token == "tok-xyz"

    def test_login_non_200_raises(self) -> None:
        """A 401 response surfaces as UploadError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="bad credentials")

        with _make_client(handler) as client:
            with pytest.raises(UploadError) as exc_info:
                login(
                    client,
                    "https://example.invalid",
                    username="perry",
                    password="wrong",
                )
        assert "401" in str(exc_info.value)

    def test_login_missing_token_raises(self) -> None:
        """200 without access_token still raises."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unrelated": True})

        with _make_client(handler) as client:
            with pytest.raises(UploadError):
                login(
                    client,
                    "https://example.invalid",
                    username="perry",
                    password="secret",
                )


# ── upload_log ───────────────────────────────────────────────────────


class TestUploadLog:
    """File-body POST to ``/v2/obd/analyze``."""

    def test_upload_returns_session_id(self, tmp_path: Path) -> None:
        """Successful 200 response yields the session_id field."""
        log = tmp_path / "trip.csv"
        log.write_text("Timestamp,A_KL_RPM\n2026-05-05 16:41:21,0\n")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/obd/analyze"
            assert (
                request.headers["authorization"] == "Bearer tok-xyz"
            )
            # Body contains the file bytes verbatim.
            assert b"A_KL_RPM" in request.content
            return httpx.Response(
                200,
                json={
                    "session_id": "abc-123",
                    "status": "COMPLETED",
                    "premium_llm_enabled": False,
                },
            )

        with _make_client(handler) as client:
            session_id = upload_log(
                client,
                "https://example.invalid",
                "tok-xyz",
                log,
            )
        assert session_id == "abc-123"

    def test_upload_missing_file_raises(self, tmp_path: Path) -> None:
        """A non-existent path raises FileNotFoundError before HTTP."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not be called")

        with _make_client(handler) as client:
            with pytest.raises(FileNotFoundError):
                upload_log(
                    client,
                    "https://example.invalid",
                    "tok-xyz",
                    tmp_path / "missing.csv",
                )

    def test_upload_non_200_raises(self, tmp_path: Path) -> None:
        """A 422 response surfaces as UploadError."""
        log = tmp_path / "trip.csv"
        log.write_text("garbage\n")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, text="cannot parse log")

        with _make_client(handler) as client:
            with pytest.raises(UploadError) as exc_info:
                upload_log(
                    client,
                    "https://example.invalid",
                    "tok-xyz",
                    log,
                )
        assert "422" in str(exc_info.value)


# ── upload_trip + main ───────────────────────────────────────────────


class TestUploadTrip:
    """End-to-end helper combining login and upload."""

    def test_main_prints_session_id(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The CLI prints the session_id on stdout and exits 0."""
        log = tmp_path / "trip.csv"
        log.write_text("Timestamp,A_KL_RPM\n2026-05-05 16:41:21,0\n")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/login":
                return httpx.Response(
                    200,
                    json={"access_token": "tok", "token_type": "bearer"},
                )
            if request.url.path == "/v2/obd/analyze":
                return httpx.Response(
                    200,
                    json={
                        "session_id": "sess-42",
                        "status": "COMPLETED",
                        "premium_llm_enabled": False,
                    },
                )
            return httpx.Response(404)

        # Patch httpx.Client used inside upload_trip to use the mock.
        original_client = httpx.Client

        def patched_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            return original_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(
            "obd_agent.jetson_uploader.httpx.Client", patched_client,
        )

        rc = main(
            [
                "--base-url", "https://example.invalid",
                "--username", "perry",
                "--password", "secret",
                "--log-file", str(log),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out.strip() == "sess-42"

    def test_upload_trip_returns_session_id(self, tmp_path: Path) -> None:
        """upload_trip integrates login + upload."""
        log = tmp_path / "trip.csv"
        log.write_text("Timestamp,A_KL_RPM\n2026-05-05 16:41:21,0\n")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/login":
                return httpx.Response(
                    200,
                    json={"access_token": "tok", "token_type": "bearer"},
                )
            return httpx.Response(
                200,
                json={
                    "session_id": "sess-7",
                    "status": "COMPLETED",
                    "premium_llm_enabled": False,
                },
            )

        # Stub httpx.Client so upload_trip uses the MockTransport.
        original_client = httpx.Client

        class _StubClient:
            def __init__(self, *args, **kwargs):
                self._inner = original_client(
                    transport=httpx.MockTransport(handler),
                )

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

        # Use the stub via upload_trip.
        import obd_agent.jetson_uploader as ju

        original_factory = ju.httpx.Client
        ju.httpx.Client = _StubClient  # type: ignore[assignment]
        try:
            session_id = upload_trip(
                "https://example.invalid",
                "perry",
                "secret",
                log,
            )
        finally:
            ju.httpx.Client = original_factory  # type: ignore[assignment]

        assert session_id == "sess-7"

    def test_main_returns_1_on_missing_file(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing log file produces exit code 1, no stdout."""
        rc = main(
            [
                "--base-url", "https://example.invalid",
                "--username", "perry",
                "--password", "secret",
                "--log-file", str(tmp_path / "missing.csv"),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert captured.out == ""


# Ensure the JSON-shape sanity tests at module scope keep passing.
def test_module_constants_use_https_friendly_paths() -> None:
    """Login and analyze paths are the production routes."""
    from obd_agent.jetson_uploader import _ANALYZE_PATH, _LOGIN_PATH

    assert _LOGIN_PATH == "/auth/login"
    assert _ANALYZE_PATH == "/v2/obd/analyze"
    # Round-trip JSON parse just to keep import-time imports honest.
    json.dumps({"login": _LOGIN_PATH, "analyze": _ANALYZE_PATH})

"""PROD-07: V3 dual-push tests for the Jetson uploader.

Every test is offline (``httpx.MockTransport``).  ``T-n`` numbers refer to
the PROD-07 kickoff test plan; ``FM-n`` to its failure modes.  The V2
tests in ``test_jetson_uploader.py`` are untouched and must keep passing.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List

import httpx
import pytest

import obd_agent.jetson_uploader as ju
from obd_agent.jetson_uploader import main

_V3_TOKEN = "dev-token-DO-NOT-LOG-9f8e7d6c"
_VEHICLE = "11111111-2222-3333-4444-555555555555"


def _write_env(tmp_path: Path, **extra: str) -> Path:
    """Writes a V3 env file with the standard keys (+ overrides)."""
    values = {
        "STF_V3_BASE_URL": "https://v3.example.invalid",
        "STF_V3_DEVICE_TOKEN": _V3_TOKEN,
        "STF_V3_VEHICLE_ID": _VEHICLE,
        "STF_V3_SPOOL_DIR": str(tmp_path / "spool"),
    }
    values.update(extra)
    env = tmp_path / "v3.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    os.chmod(env, 0o600)   # else the posix permissions warning is the first log line
    return env


def _v3_ok(request: httpx.Request, code: int = 201, vehicle: str = _VEHICLE) -> httpx.Response:
    return httpx.Response(code, json={
        "id": "log-1", "vehicle_id": vehicle, "duplicate": code == 200,
        "source": "device",
    })


def _trip(tmp_path: Path, name: str = "trip.tsv") -> Path:
    log = tmp_path / name
    log.write_text("OBD Data Log\nTimestamp\tRPM\n2026-09-17 10:00:00\t800\n")
    return log


class _Recorder:
    """MockTransport handler that records every request and answers per path."""

    def __init__(self, v2: Callable[[httpx.Request], httpx.Response],
                 v3: Callable[[httpx.Request], httpx.Response]) -> None:
        self.calls: List[str] = []
        self.v2, self.v3 = v2, v3

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        if request.url.path == "/v2/obd/analyze":
            return self.v2(request)
        if request.url.path == "/v3/ingest/device":
            assert request.headers["x-device-token"] == _V3_TOKEN
            return self.v3(request)
        return httpx.Response(404)


def _patch_client(monkeypatch: pytest.MonkeyPatch,
                  handler: Callable[[httpx.Request], httpx.Response]) -> None:
    original = httpx.Client

    def patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("obd_agent.jetson_uploader.httpx.Client", patched)
    monkeypatch.setattr(ju, "_V3_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(ju.time, "sleep", lambda s: None)


def _main(env: Path, log: Path, *extra: str) -> int:
    return main([
        "--base-url", "https://example.invalid", "--username", "perry",
        "--password", "secret", "--manufacturer", "Toyota", "--model", "Hiace",
        "--log-file", str(log), "--v3-env-file", str(env), *extra,
    ])


def _v2_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"session_id": "s-ok"})


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


# ── T-1: order + independence, V2 leg untouched ──────────────────────


class TestDualPushOrder:
    """T-1 (FM-31, acceptance 3)."""

    def test_v2_first_then_v3_and_both_succeed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The V2 request goes out before the V3 one; rc 0; session_id printed."""
        rec = _Recorder(_v2_ok, _v3_ok)
        _patch_client(monkeypatch, rec)
        rc = _main(_write_env(tmp_path), _trip(tmp_path))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "s-ok"
        assert rec.calls == ["/auth/login", "/v2/obd/analyze", "/v3/ingest/device"]

    def test_v2_failure_does_not_stop_v3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """V2 500 → rc 1, but the V3 leg still runs and stores."""
        rec = _Recorder(lambda r: httpx.Response(500, text="boom"), _v3_ok)
        _patch_client(monkeypatch, rec)
        rc = _main(_write_env(tmp_path), _trip(tmp_path))
        assert rc == 1
        assert "/v3/ingest/device" in rec.calls

    def test_v3_failure_does_not_change_v2_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """V3 refusing the file (422) leaves V2 success intact: session printed, rc 2."""
        rec = _Recorder(_v2_ok, lambda r: httpx.Response(422, json={"code": "unsupported_format"}))
        _patch_client(monkeypatch, rec)
        rc = _main(_write_env(tmp_path), _trip(tmp_path))
        assert rc == 2
        assert capsys.readouterr().out.strip() == "s-ok"

    def test_v2_request_unchanged_by_v3_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The V2 request carries no V3 header and the same params as before."""
        seen: Dict[str, object] = {}

        def v2(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            seen["has_device_header"] = "x-device-token" in request.headers
            seen["ct"] = request.headers["content-type"]
            return _v2_ok(request)

        _patch_client(monkeypatch, _Recorder(v2, _v3_ok))
        assert _main(_write_env(tmp_path), _trip(tmp_path)) == 0
        assert seen == {"params": {"manufacturer": "Toyota", "vehicle_model": "Hiace"},
                        "has_device_header": False, "ct": "text/plain; charset=utf-8"}


# ── T-2: spool on failure, atomic copy, original untouched, rollback ──


class TestSpool:
    """T-2 (FM-1, FM-2, FM-16)."""

    def test_network_failure_spools_a_complete_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """V3 unreachable → exactly one complete pending copy; original intact; rc 0."""
        _patch_client(monkeypatch, _Recorder(_v2_ok, _down))
        log = _trip(tmp_path)
        before = log.read_bytes()
        assert _main(_write_env(tmp_path), log) == 0
        pending = list((tmp_path / "spool" / "pending").iterdir())
        assert [p.name for p in pending] == ["trip.tsv"]
        assert pending[0].read_bytes() == before and log.read_bytes() == before

    def test_interrupted_copy_leaves_no_half_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A crash between temp write and rename leaves only a dot-temp, which
        drain ignores; the rerun spools cleanly."""
        spool = ju.Spool(tmp_path / "spool")
        log = _trip(tmp_path)

        def boom(src: str, dst: str) -> None:
            Path(dst).write_bytes(b"half")
            raise OSError("power cut")

        monkeypatch.setattr(ju.shutil, "copyfile", boom)
        with pytest.raises(OSError):
            spool.add(log)
        assert spool.pending() == []           # temp name hidden
        monkeypatch.undo()
        spool.add(log)
        assert [p.name for p in spool.pending()] == ["trip.tsv"]

    def test_no_env_file_means_v2_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Rollback path: without the env file no V3 request is made and the
        first log line says the leg is disabled."""
        rec = _Recorder(_v2_ok, _v3_ok)
        _patch_client(monkeypatch, rec)
        with caplog.at_level(logging.INFO):
            rc = _main(tmp_path / "absent.env", _trip(tmp_path))
        assert rc == 0 and "/v3/ingest/device" not in rec.calls
        assert "v3: disabled (env file not found" in caplog.text
        assert not (tmp_path / "spool").exists()


# ── T-3: retry policy + size-scaled timeout ──────────────────────────


class TestRetryPolicy:
    """T-3 (FM-11, FM-36)."""

    def test_transport_errors_retry_three_times_with_fixed_delays(
        self, tmp_path: Path,
    ) -> None:
        """4 attempts total; sleeps 5, 15, 45; then reported as spooled."""
        attempts: List[int] = []
        sleeps: List[float] = []

        def v3(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            raise httpx.ReadTimeout("slow", request=request)

        cfg = ju.load_v3_config(_write_env(tmp_path))
        assert cfg is not None
        with httpx.Client(transport=httpx.MockTransport(v3)) as client:
            result = ju.v3_upload_with_retry(client, cfg, _trip(tmp_path), sleep=sleeps.append)
        assert len(attempts) == 4 and sleeps == [5.0, 15.0, 45.0]
        assert result.outcome == ju.OUTCOME_SPOOLED

    def test_5xx_retries_but_4xx_never(self, tmp_path: Path) -> None:
        """503 is transient (retried); 422 returns at once with zero sleeps."""
        codes = iter([503, 503, 201])
        cfg = ju.load_v3_config(_write_env(tmp_path))
        assert cfg is not None
        with httpx.Client(transport=httpx.MockTransport(lambda r: _v3_ok(r, next(codes)))) as client:
            sleeps: List[float] = []
            result = ju.v3_upload_with_retry(client, cfg, _trip(tmp_path), sleep=sleeps.append)
        assert result.outcome == ju.OUTCOME_STORED and sleeps == [5.0, 15.0]

        calls: List[int] = []

        def reject(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(422, json={"code": "vin_mismatch"})

        with httpx.Client(transport=httpx.MockTransport(reject)) as client:
            sleeps = []
            result = ju.v3_upload_with_retry(client, cfg, _trip(tmp_path), sleep=sleeps.append)
        assert result.outcome == ju.OUTCOME_REJECTED and calls == [1] and sleeps == []

    def test_write_timeout_scales_with_size_and_caps(self) -> None:
        """60 s + 10 s/MB, capped at 600 s; connect/read stay 60 s."""
        assert ju.v3_timeout(50 * 1024 * 1024).write == 560.0
        assert ju.v3_timeout(100 * 1024 * 1024).write == 600.0
        small = ju.v3_timeout(1024)
        assert small.connect == 60.0 and small.read == 60.0
        assert ju._DEFAULT_TIMEOUT_SECONDS == 60.0     # V2 leg untouched


# ── T-4: drain semantics + lock ──────────────────────────────────────


def _five_pending(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = ju.load_v3_config(_write_env(tmp_path))
    assert cfg is not None
    spool = ju.Spool(cfg.spool_dir)
    spool.ensure()
    for i in range(1, 6):
        (spool.pending_dir / f"t{i}.tsv").write_text(f"OBD Data Log\nTimestamp\tRPM\n{i}\n")
    return cfg, spool


class TestDrain:
    """T-4 (FM-8, FM-9, FM-21, acceptance 2)."""

    def test_per_file_independent_and_stop_on_first_network_error(
        self, tmp_path: Path,
    ) -> None:
        """t1 201 (deleted), t2 200 duplicate (deleted), t3 connection error → stop;
        t3..t5 untouched; backoff recorded."""
        cfg, spool = _five_pending(tmp_path)

        def v3(request: httpx.Request) -> httpx.Response:
            body = request.read()
            if b'filename="t1.tsv"' in body:
                return _v3_ok(request, 201)
            if b'filename="t2.tsv"' in body:
                return _v3_ok(request, 200)
            raise httpx.ConnectError("down", request=request)

        with httpx.Client(transport=httpx.MockTransport(v3)) as client:
            summary = ju.drain(cfg, spool, client, now=lambda: 1000.0)
        assert summary.uploaded == 2 and summary.stopped == "network"
        assert [p.name for p in spool.pending()] == ["t3.tsv", "t4.tsv", "t5.tsv"]
        state = spool.load_state()
        assert state["consecutive_failures"] == 1
        assert state["next_drain_after"] == 1000.0 + 600

    def test_local_error_three_times_moves_file_to_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A file the script itself cannot read is counted per run and
        quarantined on the third failure; the others still upload."""
        cfg, spool = _five_pending(tmp_path)
        original = ju.v3_upload_once

        def flaky(client: httpx.Client, c: ju.V3Config, path: Path) -> ju.V3Result:
            if path.name == "t2.tsv":
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
            return original(client, c, path)

        monkeypatch.setattr(ju, "v3_upload_once", flaky)
        with httpx.Client(transport=httpx.MockTransport(_v3_ok)) as client:
            for _ in range(3):
                summary = ju.drain(cfg, spool, client, force=True)
        assert summary.stopped is None
        assert spool.pending() == []
        rejected = sorted(p.name for p in spool.rejected_dir.iterdir())
        assert rejected == ["t2.tsv", "t2.tsv.error.txt"]
        assert "local error x3" in (spool.rejected_dir / "t2.tsv.error.txt").read_text()

    def test_second_instance_exits_zero_while_lock_held(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """FM-9: the lock is exclusive; a concurrent --drain exits 0 with a log line."""
        cfg = ju.load_v3_config(_write_env(tmp_path))
        assert cfg is not None
        spool = ju.Spool(cfg.spool_dir)
        spool.ensure()
        holder = ju.RunLock(spool.lock_path)
        assert holder.acquire()
        try:
            rec = _Recorder(_v2_ok, _v3_ok)
            _patch_client(monkeypatch, rec)
            with caplog.at_level(logging.INFO):
                rc = main(["--drain", "--v3-env-file", str(tmp_path / "v3.env")])
            assert rc == 0 and "another uploader is running" in caplog.text
            assert rec.calls == []
        finally:
            holder.release()
        again = ju.RunLock(spool.lock_path)
        assert again.acquire()
        again.release()


# ── T-5: rejection routing + exit codes ──────────────────────────────


class TestRejectionRouting:
    """T-5 (FM-3, FM-30, FM-35)."""

    @pytest.mark.parametrize("code", [413, 422])
    def test_file_problems_go_to_rejected_with_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int,
    ) -> None:
        """413/422 → copy in rejected/ + .error.txt, nothing pending, rc 2."""
        rec = _Recorder(_v2_ok, lambda r: httpx.Response(code, json={"code": "nope"}))
        _patch_client(monkeypatch, rec)
        log = _trip(tmp_path)
        assert _main(_write_env(tmp_path), log) == 2
        spool = ju.Spool(tmp_path / "spool")
        assert spool.pending() == []
        names = sorted(p.name for p in spool.rejected_dir.iterdir())
        assert names == ["trip.tsv", "trip.tsv.error.txt"]
        assert f"HTTP {code}" in (spool.rejected_dir / "trip.tsv.error.txt").read_text()
        assert log.exists()

    def test_401_keeps_file_pending_and_stops(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A revoked token is a config problem: file stays pending (so a new
        token can push it), rc 2, clear log line; --drain behaves the same."""
        rec = _Recorder(_v2_ok, lambda r: httpx.Response(401, json={"code": "device_token_invalid"}))
        _patch_client(monkeypatch, rec)
        with caplog.at_level(logging.INFO):
            rc = _main(_write_env(tmp_path), _trip(tmp_path))
        assert rc == 2
        spool = ju.Spool(tmp_path / "spool")
        assert [p.name for p in spool.pending()] == ["trip.tsv"]
        assert list(spool.rejected_dir.iterdir()) == []
        assert "token invalid or revoked" in caplog.text
        assert main(["--drain", "--v3-env-file", str(tmp_path / "v3.env")]) == 2
        assert [p.name for p in spool.pending()] == ["trip.tsv"]

    def test_5xx_and_network_spool_with_rc_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient V3 failure after retries → pending copy, rc 0 (V2 fine)."""
        rec = _Recorder(_v2_ok, lambda r: httpx.Response(503, text="maintenance"))
        _patch_client(monkeypatch, rec)
        assert _main(_write_env(tmp_path), _trip(tmp_path)) == 0
        assert rec.calls.count("/v3/ingest/device") == 4
        assert [p.name for p in ju.Spool(tmp_path / "spool").pending()] == ["trip.tsv"]

    def test_v2_failure_wins_the_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """V2 413 (its 10 MB cap) + V3 stored → rc 1; the V3 leg still ran."""
        rec = _Recorder(lambda r: httpx.Response(413, text="too large"), _v3_ok)
        _patch_client(monkeypatch, rec)
        assert _main(_write_env(tmp_path), _trip(tmp_path)) == 1
        assert "/v3/ingest/device" in rec.calls


# ── T-6: spool cap + drain backoff ───────────────────────────────────


class TestSpoolCapAndBackoff:
    """T-6 (FM-12, FM-11)."""

    def test_cap_refuses_new_files_and_keeps_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With the cap at 3 files the 4th is not copied; original stays; rc 2."""
        monkeypatch.setattr(ju, "_V3_SPOOL_MAX_FILES", 3)
        spool = ju.Spool(tmp_path / "spool")
        for i in range(3):
            spool.add(_trip(tmp_path, f"t{i}.tsv"))
        log = _trip(tmp_path, "t9.tsv")
        with pytest.raises(ju.SpoolFull):
            spool.add(log)
        assert len(spool.pending()) == 3 and log.exists()

        _patch_client(monkeypatch, _Recorder(_v2_ok, _down))
        assert _main(_write_env(tmp_path), log) == 2
        assert len(spool.pending()) == 3

    def test_backoff_doubles_to_60_min_then_resets(self, tmp_path: Path) -> None:
        """4 failed drains → 10, 20, 40, 60 min; a 5th stays at 60; success resets."""
        cfg = ju.load_v3_config(_write_env(tmp_path))
        assert cfg is not None
        spool = ju.Spool(cfg.spool_dir)
        spool.add(_trip(tmp_path))
        clock = {"t": 0.0}
        waits: List[float] = []
        with httpx.Client(transport=httpx.MockTransport(_down)) as client:
            for _ in range(5):
                summary = ju.drain(cfg, spool, client, now=lambda: clock["t"])
                assert summary.stopped == "network"
                waits.append(spool.load_state()["next_drain_after"] - clock["t"])
                clock["t"] = spool.load_state()["next_drain_after"] + 1
        assert waits == [600, 1200, 2400, 3600, 3600]
        clock["t"] -= 100
        with httpx.Client(transport=httpx.MockTransport(_down)) as client:
            assert ju.drain(cfg, spool, client, now=lambda: clock["t"]).skipped_backoff
        clock["t"] += 200
        with httpx.Client(transport=httpx.MockTransport(_v3_ok)) as client:
            summary = ju.drain(cfg, spool, client, now=lambda: clock["t"])
        assert summary.uploaded == 1 and spool.load_state()["consecutive_failures"] == 0


# ── T-7: config, expected vehicle, token hygiene ─────────────────────


class TestConfigAndHygiene:
    """T-7 (FM-4, FM-22, FM-34)."""

    def test_env_file_without_token_is_config_error(self, tmp_path: Path) -> None:
        """Present-but-broken env file raises, absent returns None."""
        with pytest.raises(ju.V3ConfigError):
            ju.load_v3_config(_write_env(tmp_path, STF_V3_DEVICE_TOKEN=""))
        assert ju.load_v3_config(tmp_path / "nope.env") is None
        with pytest.raises(ju.V3ConfigError):
            ju.load_v3_config(_write_env(tmp_path, STF_V3_VEHICLE_ID="not-a-uuid"))

    def test_self_check_fails_without_config_and_passes_with(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--self-check: rc 1 without env file; rc 0 with env file + health 200; no upload."""
        assert main(["--self-check", "--v3-env-file", str(tmp_path / "absent.env")]) == 1
        assert "not found" in capsys.readouterr().out
        calls: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"status": "ok"})

        env = _write_env(tmp_path)
        rc = ju.self_check(
            env, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
        assert rc == 0 and calls == ["/v3/health"]
        assert "pending 0 files" in capsys.readouterr().out

    def test_vehicle_mismatch_is_reported_and_copy_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Server files the log under another vehicle id → rc 2, both ids in
        the log line, a copy in rejected/ so nothing is silently lost."""
        other = "99999999-2222-3333-4444-555555555555"
        rec = _Recorder(_v2_ok, lambda r: _v3_ok(r, 201, vehicle=other))
        _patch_client(monkeypatch, rec)
        with caplog.at_level(logging.INFO):
            rc = _main(_write_env(tmp_path), _trip(tmp_path))
        assert rc == 2 and other in caplog.text and _VEHICLE in caplog.text
        assert (tmp_path / "spool" / "rejected" / "trip.tsv").exists()

    def test_token_never_appears_in_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Across success, rejection, 401 and network failure the token is
        absent from every log record and from the run log file."""
        answers = iter([_v3_ok, lambda r: httpx.Response(422, text="bad"),
                        lambda r: httpx.Response(401, text="revoked")])

        def v3(request: httpx.Request) -> httpx.Response:
            try:
                return next(answers)(request)
            except StopIteration:
                raise httpx.ConnectError("down", request=request)

        _patch_client(monkeypatch, _Recorder(_v2_ok, v3))
        env = _write_env(tmp_path)
        with caplog.at_level(logging.DEBUG):
            for _ in range(4):
                _main(env, _trip(tmp_path))
        assert _V3_TOKEN not in caplog.text
        run_log = (tmp_path / "spool" / "uploader.log").read_text()
        assert _V3_TOKEN not in run_log and "v3_result" in run_log

    def test_first_log_line_states_v3_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """FM-34: with an env file the first line names the base URL and spool."""
        _patch_client(monkeypatch, _Recorder(_v2_ok, _v3_ok))
        with caplog.at_level(logging.INFO):
            _main(_write_env(tmp_path), _trip(tmp_path))
        first = [r.getMessage() for r in caplog.records
                 if r.name == "obd_agent.jetson_uploader"][0]
        assert first.startswith("v3: enabled base_url=https://v3.example.invalid")


# ── T-8: zero new dependencies, Python 3.8 syntax ────────────────────


def test_uploader_only_imports_stdlib_and_httpx() -> None:
    """T-8 (FM-15): the device script must run on the existing interpreter
    with the existing dependency set; no new third-party import."""
    src = Path(ju.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    stdlib = set(getattr(sys, "stdlib_module_names", ()) or {
        # py3.8 CI leg has no sys.stdlib_module_names: the modules the
        # script is allowed to use, spelled out.
        "argparse", "dataclasses", "fcntl", "json", "logging", "os", "pathlib",
        "shutil", "stat", "sys", "time", "typing", "uuid",
    })
    third_party = set()
    for node in ast.walk(tree):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            if root not in stdlib and root != "__future__":
                third_party.add(root)
    assert third_party == {"httpx"}
    # Parses as Python 3.8 (no match, no PEP 604 unions, no parenthesised
    # context managers): the future import keeps annotations lazy.
    ast.parse(src, feature_version=(3, 8))
    req = (Path(ju.__file__).parent / "requirements.txt").read_text()
    assert "httpx==0.26.0" in req   # the HTTP client pin the device already has

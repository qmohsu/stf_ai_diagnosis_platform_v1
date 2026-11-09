"""PR #278: a V3 deploy must verify the frontend it restarts.

The command stubs keep this check independent of Podman and the PolyU server.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_check.sh"
COMMIT = "1" * 40


def _bash() -> Path:
    if os.name == "nt":
        for candidate in (
            Path(r"D:\GitForWindows\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\bin\bash.exe"),
        ):
            if candidate.is_file():
                return candidate
    found = shutil.which("bash")
    if found is None:
        pytest.skip("bash is unavailable")
    return Path(found)


def _shell_path(path: Path, bash: Path) -> str:
    if os.name != "nt":
        return str(path)
    cygpath = bash.parents[1] / "usr" / "bin" / "cygpath.exe"
    return subprocess.check_output(
        [str(cygpath), "-u", str(path)], text=True, encoding="utf-8"
    ).strip()


def _write_command(directory: Path, name: str, body: str) -> None:
    command = directory / name
    command.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    command.chmod(0o755)


def _run_deploy_check(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    commands = tmp_path / "commands"
    commands.mkdir()

    _write_command(commands, "podman", """
case "$1" in
  inspect)
    if [ "$2" = -f ]; then format="$3"; container="$4"; else container="$2"; format=""; fi
    if [ "$container" = stf-v3-frontend ] && [ "$FRONTEND_SCENARIO" = missing ]; then exit 1; fi
    case "$format" in
      *State.Status*) echo running ;;
      *Created*)
        if [ "$container" = stf-v3-frontend ] && [ "$FRONTEND_SCENARIO" = stale ]; then
          echo "$MOCK_STALE_CREATED"
        else
          echo "$MOCK_FRESH_CREATED"
        fi ;;
      *org.opencontainers.image.revision*)
        if [ "$container" = stf-v3-frontend ] && [ "$FRONTEND_SCENARIO" = revision_mismatch ]; then
          echo incorrect-revision
        else
          echo "$MOCK_COMMIT"
        fi ;;
      *Mounts*) echo test-volume ;;
      *Pod*) echo pod-v3 ;;
    esac
    ;;
  exec)
    case "$*" in
      *'alembic current'*) echo abc123 ;;
      *'alembic heads'*) echo abc123 ;;
    esac
    ;;
  logs) echo worker.heartbeat ;;
  volume) exit 1 ;;
esac
""")
    _write_command(commands, "curl", """
case "$*" in
  *'/api/health'*)
    if [ "$FRONTEND_SCENARIO" = health_error ]; then exit 22; fi
    echo '{"status":"ok","v3Api":"ok"}' ;;
  *'/v3/health'*)
    echo '{"db":"ok","gpu_worker":{"alive":true,"age_s":1,"commit":"'"$MOCK_COMMIT"'"},"disk_free_gb":100,"backup":{"state":"ok","age_h":1,"offsite":{"state":"ok","age_h":1},"last_result":"success"}}' ;;
  *) exit 22 ;;
esac
""")
    _write_command(commands, "systemctl", "exit 0\n")
    _write_command(commands, "python3", 'exec "$MOCK_PYTHON" "$@"\n')

    now = datetime.now(timezone.utc)
    env = os.environ.copy()
    env.update(
        {
            "FRONTEND_SCENARIO": scenario,
            "MOCK_COMMIT": COMMIT,
            "MOCK_PYTHON": sys.executable,
            "MOCK_FRESH_CREATED": now.strftime("%Y-%m-%d %H:%M:%S +0000 UTC"),
            "MOCK_STALE_CREATED": (now - timedelta(minutes=31)).strftime(
                "%Y-%m-%d %H:%M:%S +0000 UTC"
            ),
            "LLM_CHECK": "skip",
            "LLM_CHECK_REASON": "test",
            "STF_V3_VOLUME_STATE": _shell_path(tmp_path / "volumes.tsv", bash),
            "STF_V3_BACKUP_ROOT": _shell_path(tmp_path / "no-backups", bash),
        }
    )
    return subprocess.run(
        [
            str(bash),
            "-c",
            'PATH="$1:$PATH"; shift; exec bash "$@"',
            "bash",
            _shell_path(commands, bash),
            _shell_path(SCRIPT, bash),
            "--expect-commit",
            COMMIT,
        ],
        cwd=SCRIPT.parents[2],
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("scenario", "should_pass"),
    [
        ("missing", False),
        ("stale", False),
        ("health_error", False),
        ("revision_mismatch", False),
        ("ok", True),
    ],
)
def test_pr278_deploy_check_requires_current_healthy_frontend(
    tmp_path: Path, scenario: str, should_pass: bool
) -> None:
    result = _run_deploy_check(tmp_path, scenario)
    output = result.stdout + result.stderr
    if should_pass:
        assert result.returncode == 0, output
        assert "PASS  frontend fresh + healthy + commit" in output
        assert "DEPLOY CHECK ALL PASS" in output
    else:
        assert result.returncode == 1, output
        assert "FAIL  frontend fresh + healthy + commit" in output
        assert "DEPLOY CHECK FAILED" in output

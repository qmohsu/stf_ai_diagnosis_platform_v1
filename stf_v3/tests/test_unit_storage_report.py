"""PROD-15A T-26: the deploy check's storage report (sizes; WARN only).

Runs ``scripts/storage_report.sh`` with a fake ``podman`` on PATH.

Author: Xiangzhu Yan
"""

import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash + POSIX modes")

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "storage_report.sh"
_FAKE_PODMAN = """#!/bin/sh
# volume inspect <name> --format <fmt> | unshare du -sh <dir>
if [ "$1 $2" = "volume inspect" ]; then
  [ -f "$FAKE/missing_$3" ] && exit 1
  case "$5" in
    *Mountpoint*) echo "$FAKE/vol_$3" ;;
    *) cat "$FAKE/created_$3" 2>/dev/null || echo "2026-09-12 10:00:00 +0800 HKT" ;;
  esac
  exit 0
fi
if [ "$1 $2" = "unshare du" ]; then printf '12M\\t%s\\n' "$4"; exit 0; fi
exit 2
"""


def _run(tmp: pathlib.Path) -> str:
    env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE=str(tmp),
               HOME=str(tmp / "home"), STF_V3_VOLUME_STATE=str(tmp / "seen.tsv"))
    res = subprocess.run(["bash", str(_SCRIPT), str(tmp / "home" / "stf_v3_backups")], env=env,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return res.stdout


@pytest.fixture()
def fake(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "bin").mkdir()
    podman = tmp_path / "bin" / "podman"
    podman.write_text(_FAKE_PODMAN)
    podman.chmod(0o755)
    for d in ("home/stf_v3_logs", "home/stf_v3_backups/archive"):
        (tmp_path / d).mkdir(parents=True, mode=0o700)
        os.chmod(tmp_path / d, 0o700)
    return tmp_path


def test_sizes_are_reported_and_a_clean_state_warns_nothing(fake: pathlib.Path) -> None:
    """Every V3 volume and the log / backup / archive dirs get a size line."""
    out = _run(fake)
    for v in ("stf_v3_obd_logs", "stf_v3_manuals", "stf_v3_logs", "stf_v3_backup_state"):
        assert f"volume {v}  12M" in out
    assert "stf_v3_backups/archive" in out and "WARN" not in out
    assert _run(fake).count("WARN") == 0                      # second run: same volumes, still quiet


def test_a_recreated_volume_and_loose_modes_warn(fake: pathlib.Path) -> None:
    """FM-8: a volume created again since the last run → WARN; FM-49: a
    directory that is not 700 → WARN; a missing volume → WARN.  Exit 0."""
    _run(fake)
    (fake / "created_stf_v3_obd_logs").write_text("2026-09-28 04:00:00 +0800 HKT\n")
    (fake / "missing_stf_v3_logs").write_text("")
    os.chmod(fake / "home" / "stf_v3_logs", 0o755)
    out = _run(fake)
    assert "WARN  volume stf_v3_obd_logs was re-created" in out
    assert "WARN  volume stf_v3_logs missing" in out
    assert "stf_v3_logs is not mode 700" in out

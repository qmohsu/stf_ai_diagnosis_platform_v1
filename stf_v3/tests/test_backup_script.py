"""PROD-15A PR ① (T-1 … T-5, T-13): the daily backup with a fake host.

Every host call (podman, pg_dump, tar, gpg, the network share) is scripted,
so the tests need no database, no GPU and no network.  The share is a real
temp directory the fake ``cp`` / ``mv`` / ``rm`` operate on.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import pathlib
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backup.py"
_spec = importlib.util.spec_from_file_location("stf_backup", _SCRIPT)
assert _spec and _spec.loader
bk: Any = importlib.util.module_from_spec(_spec)
sys.modules["stf_backup"] = bk          # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(bk)

POSIX = os.name == "posix"
PASSPHRASE = "zz-fake-passphrase-487"
NOW = dt.datetime(2026, 9, 28, 19, 30, tzinfo=dt.timezone.utc)
TABLES = {"stf_v3": ["public.users", "public.vehicles", "public.audit_events"],
          "stf_diagnosis": ["public.obd_analysis_sessions", "public.users"]}


class FakeSession:
    """Answers the snapshot session's queries."""

    def __init__(self, db: str, log: List[str]) -> None:
        self.db, self.log = db, log

    def query(self, sql: str) -> List[str]:
        self.log.append(f"{self.db}: {sql.split()[0]}")
        if "pg_export_snapshot" in sql:
            return ["00000003-0000001B-1"]
        if sql.strip() == "SELECT now()":
            return ["2026-09-28 19:30:01+00"]
        if "server_version" in sql:
            return ["15.7"]
        if "to_regclass" in sql:
            return ["t"]
        if "alembic_version" in sql:
            return ["c4e8a1f2b7d3"]
        if "query_to_xml" in sql:
            return [f"{t}|{i + 1}|md5{i}" for i, t in enumerate(TABLES[self.db])]
        return []

    def close(self) -> None:
        self.log.append(f"{self.db}: close")


class FakeHost:
    """Scripted host; ``fail`` names one step to break."""

    def __init__(self, tmp: pathlib.Path, *, fail: Optional[str] = None, fstype: str = "cifs",
                 free_gb: float = 600.0) -> None:
        self.tmp, self.fail, self.fstype, self.free = tmp, fail, fstype, free_gb
        self.calls: List[List[str]] = []
        self.sessions: List[str] = []
        self.state_dir = tmp / "state_volume"
        self.state_dir.mkdir()

    # -- Host interface ------------------------------------------------
    def now(self) -> dt.datetime:
        return NOW

    def sleep(self, s: float) -> None:
        pass

    def free_gb(self, path: pathlib.Path) -> float:
        return self.free

    def psql_session(self, container: str, user: str, db: str) -> FakeSession:
        assert user == "stf_v3_backup"                    # FM-16: never the superuser
        return FakeSession(db, self.sessions)

    def run(self, argv: Sequence[str], timeout: float = 600, *, input_text: Optional[str] = None,
            stdin_path: Optional[pathlib.Path] = None, stdout_path: Optional[pathlib.Path] = None) -> Any:
        a = list(argv)
        self.calls.append(a)
        assert timeout > 0
        R = bk.Result
        if a[:3] == ["podman", "volume", "inspect"]:
            if a[3] == bk.STATE_VOLUME:
                return R(0, f"{self.state_dir}\n")
            return R(0, f"/fake/volumes/{a[3]}/_data\n")
        if a[:2] == ["podman", "exec"] and "pg_isready" in a:
            return R(0 if self.fail != "db_down" else 2)
        if a[:2] == ["podman", "exec"] and "pg_dump" in a:
            if self.fail == "pg_dump":
                stdout_path.write_bytes(b"PGDMP-half")
                return R(1, "", "pg_dump: error: connection lost")
            stdout_path.write_bytes(b"PGDMP-" + a[a.index("-d") + 1].encode())
            return R(0)
        if "pg_restore" in a and "--list" in a:
            db = stdin_path.read_bytes().decode().split("-", 1)[1]
            return R(0, "".join(f"1; 0 0 TABLE DATA public {t} owner\n" for t in TABLES[db]))
        if "pg_dumpall" in a:
            stdout_path.write_text("CREATE ROLE stf_v3;\n")
            return R(0)
        if a[:3] == ["podman", "unshare", "find"]:
            return R(0, "...")                               # 3 files in each volume
        if a[:3] == ["podman", "unshare", "tar"]:
            stdout_path.write_bytes(b"tgz:" + a[3].encode())
            return R(0 if self.fail != "tar" else 2, "", "tar: ./x: Cannot open: Permission denied")
        if a[:2] == ["tar", "-tzf"]:
            n = 2 if self.fail == "tar_short" else 3
            return R(0, "./\n" + "".join(f"./v/{i}.csv\n" for i in range(n)))
        if a[:3] == ["git", "-C", str(self.tmp / "repo")]:
            return R(0, "1b4b18c2e228\n")
        if a[0] == "gpg":
            src, out = pathlib.Path(a[-1]), pathlib.Path(a[a.index("-o") + 1])
            data = src.read_bytes()
            if "-d" in a and self.fail == "decrypt_differs":
                data = data + b"x"
            out.write_bytes(data[::-1])                      # "encryption": reversible, not plaintext
            return R(0)
        if a[0] == "findmnt":
            return R(0, self.fstype + "\n")
        if a[:2] == ["mkdir", "-p"]:
            pathlib.Path(a[2]).mkdir(parents=True, exist_ok=True)
            return R(0)
        if a[:2] == ["ls", "-1"]:
            return R(0, "".join(n + "\n" for n in sorted(os.listdir(a[2]))))
        if a[0] == "cp":
            if self.fail == "offsite_cp":
                pathlib.Path(a[2]).write_bytes(b"half")
                return R(1, "", "cp: No space left on device")
            shutil.copyfile(a[1], a[2])
            return R(0)
        if a[0] == "sha256sum":
            return R(0, f"{bk.sha256_file(pathlib.Path(a[1]))}  {a[1]}\n")
        if a[0] == "mv":
            os.replace(a[1], a[2])
            return R(0)
        if a[:2] == ["rm", "-f"]:
            try:
                os.unlink(a[2])
            except FileNotFoundError:
                pass
            return R(0)
        raise AssertionError(f"unexpected command {a}")


def _cfg(tmp: pathlib.Path) -> Any:
    (tmp / "repo" / "infra").mkdir(parents=True)
    (tmp / "repo" / "infra" / ".env").write_text("STF_V3_JWT_SECRET=zz-fake\n")
    pp = tmp / "pp"
    pp.write_text(PASSPHRASE)
    return bk.Config(root=tmp / "root", offsite=tmp / "share" / "stf_v3_backups", passphrase_file=pp,
                     repo=tmp / "repo", db_ready_tries=2, db_ready_sleep_s=0)


def _status(cfg: Any) -> Dict[str, Any]:
    return json.loads(cfg.status_file.read_text())


# ── T-1: only a verified bundle is a success ───────────────────────────


def test_a_clean_run_produces_one_verified_encrypted_bundle(tmp_path: pathlib.Path) -> None:
    """Happy path: one bundle locally and on the share, status ok, nothing left in tmp."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path)
    assert bk.run_backup(cfg, host) == 0
    name = bk.bundle_name(NOW)
    assert (cfg.daily / name).is_file() and (cfg.offsite / name).is_file()
    st = _status(cfg)
    assert st["last_result"] == "ok" and st["offsite"]["last_result"] == "ok"
    assert list(cfg.tmp.iterdir()) == []                               # FM-23: no plaintext left
    public = json.loads((host.state_dir / "status.json").read_text())  # what /v3/health reads
    assert public["last_success_at"] == st["last_success_at"]


@pytest.mark.parametrize("fail, why", [
    ("pg_dump", "pg_dump stf_v3 failed"),
    ("tar", "tar of stf_v3_obd_logs failed"),
    ("tar_short", "has 2 files, volume had 3"),
    ("decrypt_differs", "does not decrypt to the same bytes"),
    ("db_down", "database not ready"),
])
def test_any_failed_step_is_a_failed_run_with_no_bundle(tmp_path: pathlib.Path, fail: str, why: str) -> None:
    """T-1 / FM-1 / FM-19 / FM-34: every step's failure fails the run; no
    finished bundle, no success timestamp, no temp files left."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path, fail=fail)
    assert bk.run_backup(cfg, host) == 1
    st = _status(cfg)
    assert st["last_result"] == "failed" and why in st["last_error"]
    assert "last_success_at" not in st
    assert [p.name for p in cfg.daily.iterdir()] == []
    assert list(cfg.tmp.iterdir()) == []


def test_a_half_copy_on_the_share_is_a_failed_offsite_and_is_removed(tmp_path: pathlib.Path) -> None:
    """T-1 / FM-21: local success stays a success; the share's half file is
    deleted, the offsite column says failed, and the next run copies it."""
    cfg = _cfg(tmp_path)
    assert bk.run_backup(cfg, FakeHost(tmp_path, fail="offsite_cp")) == 0
    st = _status(cfg)
    assert st["last_result"] == "ok" and st["offsite"]["last_result"] == "failed"
    assert os.listdir(cfg.offsite) == []


def test_the_next_run_copies_bundles_missing_on_the_share(tmp_path: pathlib.Path) -> None:
    """FM-21: a day missing on the share is copied by the next successful run."""
    cfg = _cfg(tmp_path)
    host = FakeHost(tmp_path, fail="offsite_cp")
    assert bk.run_backup(cfg, host) == 0
    host.fail = None
    info = bk.offsite_sync(cfg, host, sorted(n for n in os.listdir(cfg.daily) if bk.parse_bundle_time(n)))
    assert info["error"] is None and info["copied"] == [bk.bundle_name(NOW)]


# ── T-2: retention by successful bundles ───────────────────────────────


def _names(days: List[int]) -> List[str]:
    base = dt.datetime(2026, 9, 30, 19, 30, tzinfo=dt.timezone.utc)
    return [bk.bundle_name(base - dt.timedelta(days=d)) for d in days]


def test_retention_counts_successes_not_calendar_days() -> None:
    """T-2 / FM-2: after 14 failed days the last 14 SUCCESSFUL bundles stay."""
    names = _names(list(range(14, 44)))           # nothing in the last two weeks
    gone = bk.select_to_delete(names, keep_daily=14, keep_weekly=8)
    kept = [n for n in names if n not in gone]
    assert sorted(names)[-14:] == sorted(kept)[-14:]
    assert max(names) in kept


def test_retention_keeps_daily_plus_one_per_week_and_never_the_newest() -> None:
    """T-2: 14 dailies + one per ISO week for 8 weeks; a single bundle is kept."""
    names = _names(list(range(0, 90)))
    kept = sorted(set(names) - set(bk.select_to_delete(names, 14, 8)))
    assert len(kept) == 14 + 8
    assert bk.select_to_delete(_names([0]), 14, 8) == []
    assert bk.select_to_delete(["notes.txt", ".tmp-" + _names([0])[0]], 14, 8) == []


def test_pruning_runs_only_after_a_successful_run(tmp_path: pathlib.Path) -> None:
    """FM-2: a failed run deletes nothing, even with 30 old bundles present."""
    cfg = _cfg(tmp_path)
    bk.ensure_private_dir(cfg.daily)
    for n in _names(list(range(20, 50))):
        (cfg.daily / n).write_bytes(b"old")
    assert bk.run_backup(cfg, FakeHost(tmp_path, fail="pg_dump")) == 1
    assert len([n for n in os.listdir(cfg.daily) if bk.parse_bundle_time(n)]) == 30


# ── T-3: guards ────────────────────────────────────────────────────────


def test_a_share_that_is_not_a_network_file_system_is_not_written(tmp_path: pathlib.Path) -> None:
    """T-3 ① / FM-22: the path exists but is the local disk → offsite fails."""
    cfg = _cfg(tmp_path)
    assert bk.run_backup(cfg, FakeHost(tmp_path, fstype="ext4")) == 0
    st = _status(cfg)
    assert st["offsite"]["last_result"] == "failed" and "not a network share" in st["offsite"]["last_error"]
    assert not cfg.offsite.exists()


def test_low_disk_refuses_to_write_locally(tmp_path: pathlib.Path) -> None:
    """T-3 ② / FM-33: below the threshold nothing is dumped at all."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path, free_gb=12.0)
    assert bk.run_backup(cfg, host) == 1
    assert "only 12 GB free" in _status(cfg)["last_error"]
    assert not any("pg_dump" in c for c in host.calls)


@pytest.mark.skipif(not POSIX, reason="flock is posix-only")
def test_a_second_run_while_one_holds_the_lock_is_skipped(tmp_path: pathlib.Path) -> None:
    """T-3 ③ / FM-26."""
    import fcntl

    cfg = _cfg(tmp_path)
    bk.ensure_private_dir(cfg.root)
    with open(cfg.root / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        host = FakeHost(tmp_path)
        assert bk.run_backup(cfg, host) == 0
        assert host.calls == []


# ── T-4: secrets and permissions ───────────────────────────────────────


def test_the_passphrase_never_reaches_a_command_line(tmp_path: pathlib.Path) -> None:
    """T-4 / FM-48: gpg reads it from a file, non-interactively."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path)
    assert bk.run_backup(cfg, host) == 0
    assert all(PASSPHRASE not in " ".join(c) for c in host.calls)
    gpg = [c for c in host.calls if c[0] == "gpg"]
    assert gpg and all("--passphrase-file" in c and "--batch" in c and "loopback" in c for c in gpg)
    assert all("--symmetric" not in c or "AES256" in c for c in gpg)


def test_bundles_are_encrypted_and_named_by_date_and_category_only(tmp_path: pathlib.Path) -> None:
    """T-4 / FM-50 / FM-54: no plaintext on disk or share; the name says only when."""
    cfg = _cfg(tmp_path)
    assert bk.run_backup(cfg, FakeHost(tmp_path)) == 0
    for d in (cfg.daily, cfg.offsite):
        for n in os.listdir(d):
            assert n.startswith("stf-backup_") and (n.endswith("_daily.tar.gpg") or n.endswith(".sha256"))
    blob = (cfg.daily / bk.bundle_name(NOW)).read_bytes()
    assert b"STF_V3_JWT_SECRET" not in blob and b"manifest.json" not in blob


@pytest.mark.skipif(not POSIX, reason="posix modes")
def test_everything_written_is_private(tmp_path: pathlib.Path) -> None:
    """T-4 / FM-49: dirs 700, bundle + status 600; the health copy is 644."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path)
    assert bk.run_backup(cfg, host) == 0
    mode = lambda p: oct(p.stat().st_mode & 0o777)  # noqa: E731
    assert mode(cfg.root) == mode(cfg.daily) == mode(cfg.tmp) == "0o700"
    assert mode(cfg.daily / bk.bundle_name(NOW)) == mode(cfg.status_file) == "0o600"
    assert mode(host.state_dir / "status.json") == "0o644"


def test_the_health_copy_carries_no_secret_or_path_detail(tmp_path: pathlib.Path) -> None:
    """The status the API serves holds times and results only."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path)
    bk.run_backup(cfg, host)
    public = json.loads((host.state_dir / "status.json").read_text())
    assert set(public) <= {"schema", "updated_at", "last_attempt_at", "last_result", "last_success_at",
                           "last_success_size", "offsite", "last_error"}


# ── T-5: the manifest ──────────────────────────────────────────────────


def _manifest(cfg: Any, host: FakeHost) -> Dict[str, Any]:
    enc = cfg.daily / bk.bundle_name(NOW)
    plain = cfg.root / "m.tar"
    plain.write_bytes(enc.read_bytes()[::-1])        # the fake cipher reverses bytes
    import tarfile

    with tarfile.open(plain) as tar:
        return json.load(tar.extractfile("manifest.json"))


def test_the_manifest_records_versions_counts_and_order(tmp_path: pathlib.Path) -> None:
    """T-5 / FM-10 / FM-36 / FM-41: schema version, commit, server version,
    per-table rows + sample hash from the dump's own snapshot; databases
    finished before the files were archived."""
    cfg, host = _cfg(tmp_path), FakeHost(tmp_path)
    assert bk.run_backup(cfg, host) == 0
    m = _manifest(cfg, host)
    assert m["git_commit"] == "1b4b18c2e228"
    v3 = m["databases"]["stf_v3"]
    assert v3["alembic"] == "c4e8a1f2b7d3" and v3["server_version"] == "15.7"
    assert v3["tables"]["public.users"] == {"rows": 1, "sample_md5": "md50"}
    assert set(m["volumes"]) == set(bk.VOLUMES) and all(v["files"] == 3 for v in m["volumes"].values())
    assert m["databases_done_at"] <= min(v["taken_at"] for v in m["volumes"].values())
    assert "vllm_hf_cache" in m["not_backed_up"]      # FM-11: inventory in the bundle
    # counts and dump come from ONE snapshot (FM-41)
    dump = [c for c in host.calls if "pg_dump" in c][0]
    assert "--snapshot=00000003-0000001B-1" in dump and "--lock-wait-timeout=60s" in dump
    assert host.sessions.index("stf_v3: close") > host.sessions.index("stf_v3: SELECT")


# ── T-13 / T-11 (static): schedule, zone, timeouts ─────────────────────


def test_the_units_carry_zone_catch_up_timeout_and_one_retry() -> None:
    """T-13 / FM-30 / FM-31 / FM-32 / FM-28 / FM-49."""
    ops = _SCRIPT.parents[1] / "ops"
    timer = (ops / "stf-v3-backup.timer").read_text()
    service = (ops / "stf-v3-backup.service").read_text()
    assert "OnCalendar=*-*-* 03:30:00 Asia/Hong_Kong" in timer and "Persistent=true" in timer
    for line in ("Type=oneshot", "UMask=0077", "TimeoutStartSec=2h", "Restart=on-failure",
                 "RestartSec=30min", "StartLimitBurst=2"):
        assert line in service, line
    assert "backup.py run" in service


def test_the_backup_role_sql_is_read_only_and_passwordless() -> None:
    """FM-16 (static half; the database half runs in the integration job)."""
    sql = (_SCRIPT.parent / "sql" / "ops_roles.sql").read_text()
    assert "GRANT pg_read_all_data TO stf_v3_backup" in sql
    assert "PASSWORD NULL" in sql and "default_transaction_read_only = on" in sql
    assert "NOSUPERUSER" in sql and "CONNECTION LIMIT 4" in sql


def test_the_escrowed_recovery_key_file_is_used_as_is(tmp_path: pathlib.Path) -> None:
    """FM-51 (user decision 2026-09-27): recovery uses the key file kept in the
    user's cloud drive (first line = key, notes below it; gpg reads only the
    first line), never a remembered passphrase."""
    cfg = _cfg(tmp_path)
    key = tmp_path / "STF_V3_recovery-key.txt"
    key.write_text("k3y\n\nnotes for humans\n")
    assert bk.read_passphrase(cfg, False, tmp_path, str(key)) == key
    assert bk.read_passphrase(cfg, False, tmp_path) == cfg.passphrase_file

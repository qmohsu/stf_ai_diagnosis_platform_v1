#!/usr/bin/env python3
"""Daily encrypted backup, restore drill and partial restore (PROD-15A).

Runs on the PolyU host (stdlib only, host python3) from the user unit
``stf-v3-backup.service`` — never through the V3 job queue, so a broken
V3 still gets backed up.  One run:

1. waits for the shared Postgres; refuses when the local disk is low;
2. per database (V3 ``stf_v3`` + V1/V2 ``stf_diagnosis``) opens ONE
   read-only snapshot as the ``stf_v3_backup`` role (``pg_read_all_data``,
   no password, reachable only through ``podman exec``), records row counts
   and a sample hash of every table IN that snapshot, then ``pg_dump``s the
   same snapshot (FM-41);
3. dumps role definitions without passwords;
4. THEN tars the file volumes (FM-10: files only grow, so they cover every
   row the dumps reference) inside ``podman unshare`` (FM-19);
5. packs everything + ``infra/.env`` + a manifest into one tar, encrypts
   it (gpg AES256, passphrase from a 600 file, never on a command line —
   FM-48/50), checks it decrypts to the same bytes, and only then calls
   the run a success (FM-1);
6. copies every local success missing on the network share (cifs/nfs
   only, FM-22), verified by checksum before the rename (FM-21);
7. prunes by the number of SUCCESSFUL bundles, never the newest (FM-2);
8. writes a status file (local + the ``stf_v3_backup_state`` volume that
   ``/v3/health`` reads, FM-40).

Subcommands::

    backup.py init                   # dirs + passphrase file (prints no secret)
    backup.py run                    # the daily backup (systemd)
    backup.py status                 # print the status file
    backup.py drill [--offsite] [--ask-passphrase | --passphrase-file KEYFILE] [--keep]
    backup.py restore-vehicle --vehicle-id UUID [--conversation-id UUID] --i-am-restoring-live

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = 1
PREFIX = "stf-backup_"
SUFFIX = "_daily.tar.gpg"
DRILL_CONTAINER = "stf-v3-restore-drill"
STATE_VOLUME = "stf_v3_backup_state"
OFFSITE_FSTYPES = ("cifs", "smb3", "nfs", "nfs4")
END_MARK = "@@STF_END@@"

# What lives on this account and what happens to it (FM-11).  Only the
# "backup" rows are copied; the others are listed so nothing is forgotten.
DATABASES = ("stf_v3", "stf_diagnosis")
VOLUMES = (
    "stf_v3_obd_logs",                 # V3 raw uploads (irreplaceable)
    "stf_v3_manuals",                  # V3 manual library (D4: full copy, FM-3)
    "infra_diagnostic_api_obd_logs",   # V1/V2 raw uploads (irreplaceable)
    "infra_diagnostic_api_manuals",    # V1/V2 manual library
    "infra_diagnostic_api_audio",      # V1 audio feedback
)
NOT_BACKED_UP = {
    "vllm_hf_cache": "rebuild: model weights re-download",
    "infra_ollama_data": "rebuild: Ollama models re-pull",
    "ollama_data": "rebuild: Ollama models re-pull",
    "infra_postgres_data": "covered by the per-database dumps",
    "infra_diagnostic_api_logs": "skip: logs",
    "diagnostic_api_logs": "skip: logs",
    STATE_VOLUME: "skip: backup status only",
}


class BackupError(Exception):
    """A step failed; the run is not a success."""


@dataclass
class Result:
    """Outcome of one external command."""

    rc: int
    out: str = ""
    err: str = ""


@dataclass
class Config:
    """Paths and knobs (env-overridable, see ``from_env``)."""

    root: Path
    offsite: Optional[Path]
    passphrase_file: Path
    repo: Path
    pg_container: str = "stf-postgres"
    role: str = "stf_v3_backup"
    databases: Tuple[str, ...] = DATABASES
    volumes: Tuple[str, ...] = VOLUMES
    min_free_gb: float = 50.0
    keep_daily: int = 14
    keep_weekly: int = 8
    lock_wait_s: int = 60
    db_ready_tries: int = 10
    db_ready_sleep_s: float = 30.0
    offsite_timeout_s: int = 1800

    @classmethod
    def from_env(cls) -> "Config":
        """Builds the config from ``STF_V3_BACKUP_*`` env vars."""
        home = Path.home()
        off = os.environ.get("STF_V3_BACKUP_OFFSITE_DIR", "/localnvme/stf_v3_backups")
        return cls(
            root=Path(os.environ.get("STF_V3_BACKUP_ROOT", str(home / "stf_v3_backups"))),
            offsite=Path(off) if off else None,
            passphrase_file=Path(os.environ.get(
                "STF_V3_BACKUP_PASSPHRASE_FILE", str(home / ".config/stf/backup_passphrase"))),
            repo=Path(os.environ.get("STF_V3_REPO", str(home / "stf_ai_diagnosis_platform_v1"))),
        )

    @property
    def daily(self) -> Path:
        return self.root / "daily"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def gnupg(self) -> Path:
        return self.root / ".gnupg"

    @property
    def status_file(self) -> Path:
        return self.root / "status.json"


# ── host access (replaced by a fake in tests) ──────────────────────────


class PsqlSession:
    """One interactive ``psql`` over ``podman exec`` (keeps a snapshot open)."""

    def __init__(self, argv: Sequence[str]) -> None:
        self.proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)

    def query(self, sql: str) -> List[str]:
        """Runs one statement; returns its output lines (``-A -t`` format)."""
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(sql.rstrip().rstrip(";") + ";\n\\echo " + END_MARK + "\n")
        self.proc.stdin.flush()
        lines: List[str] = []
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise BackupError(f"psql session ended: {err.strip()[:300]}")
            line = line.rstrip("\n")
            if line == END_MARK:
                return lines
            lines.append(line)

    def close(self) -> None:
        """Ends the session (the snapshot goes with it)."""
        try:
            if self.proc.stdin:
                self.proc.stdin.write("COMMIT;\n\\q\n")
                self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


class Host:
    """Real command execution with a timeout on every call."""

    def run(self, argv: Sequence[str], timeout: float = 600, *, input_text: Optional[str] = None,
            stdin_path: Optional[Path] = None, stdout_path: Optional[Path] = None) -> Result:
        """Runs a command; stdout goes to ``stdout_path`` when given."""
        stdin = open(stdin_path, "rb") if stdin_path else None
        stdout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
        try:
            res = subprocess.run(list(argv), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                                 input=input_text.encode() if input_text is not None else None,
                                 timeout=timeout)
            out = res.stdout.decode(errors="replace") if stdout_path is None and res.stdout else ""
            return Result(res.returncode, out, res.stderr.decode(errors="replace"))
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timeout after {timeout:.0f}s")
        except OSError as exc:
            return Result(127, "", str(exc))
        finally:
            if stdin:
                stdin.close()
            if stdout_path is not None and stdout is not subprocess.PIPE:
                stdout.close()  # type: ignore[union-attr]

    def psql_session(self, container: str, user: str, db: str) -> PsqlSession:
        """Opens an interactive read session."""
        return PsqlSession(["podman", "exec", "-i", container, "psql", "-U", user, "-d", db,
                            "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1"])

    def free_gb(self, path: Path) -> float:
        return shutil.disk_usage(path).free / 1e9

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def sleep(self, s: float) -> None:
        time.sleep(s)


# ── small helpers ──────────────────────────────────────────────────────


def utc_stamp(t: dt.datetime) -> str:
    """``20260927T193000Z``."""
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def bundle_name(t: dt.datetime) -> str:
    """Date and category only (FM-54)."""
    return f"{PREFIX}{utc_stamp(t)}{SUFFIX}"


def parse_bundle_time(name: str) -> Optional[dt.datetime]:
    """Timestamp of a finished bundle name, None for anything else."""
    if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
        return None
    try:
        return dt.datetime.strptime(name[len(PREFIX):-len(SUFFIX)], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Writes JSON via a temp file + rename, mode 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def acquire_lock(fh: Any) -> bool:
    """Non-blocking exclusive lock (FM-26); always granted where flock is absent."""
    try:
        import fcntl
    except ImportError:          # Windows dev boxes (tests)
        return True
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def ensure_private_dir(path: Path) -> None:
    """Creates ``path`` (and parents) and forces mode 700 (FM-49)."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def select_to_delete(names: Iterable[str], keep_daily: int, keep_weekly: int) -> List[str]:
    """Retention by SUCCESSFUL bundles (FM-2).

    Keeps the ``keep_daily`` newest bundles, plus the newest bundle of each
    of the ``keep_weekly`` most recent ISO weeks older than those; never
    the newest one.  Anything that is not a finished bundle name is ignored.

    Returns:
        Names to delete, oldest first.
    """
    dated = sorted((t, n) for n in names if (t := parse_bundle_time(n)) is not None)
    if not dated:
        return []
    newest_first = list(reversed(dated))
    keep = {n for _, n in newest_first[:max(1, keep_daily)]}
    weeks: List[Tuple[int, int]] = []
    for t, n in newest_first[max(1, keep_daily):]:
        wk = t.isocalendar()[:2]
        if wk in weeks:
            continue
        if len(weeks) >= keep_weekly:
            break
        weeks.append(wk)
        keep.add(n)
    return [n for _, n in dated if n not in keep]


# ── status ─────────────────────────────────────────────────────────────


def load_status(cfg: Config) -> Dict[str, Any]:
    try:
        data = json.loads(cfg.status_file.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def state_volume_path(host: Host) -> Optional[Path]:
    """Mountpoint of the volume ``/v3/health`` reads, None if absent."""
    res = host.run(["podman", "volume", "inspect", STATE_VOLUME, "--format", "{{.Mountpoint}}"], 30)
    return Path(res.out.strip()) if res.rc == 0 and res.out.strip() else None


def save_status(cfg: Config, host: Host, status: Dict[str, Any]) -> None:
    """Writes the status locally and into the health volume (best effort)."""
    status["schema"] = SCHEMA
    status["updated_at"] = host.now().isoformat()
    write_json_atomic(cfg.status_file, status)
    vol = state_volume_path(host)
    if vol is not None:
        public = {k: status.get(k) for k in (
            "schema", "updated_at", "last_attempt_at", "last_result", "last_success_at",
            "last_success_size", "offsite")}
        public["last_error"] = (status.get("last_error") or "")[:200] or None
        try:
            write_json_atomic(vol / "status.json", public)
            os.chmod(vol / "status.json", 0o644)   # read by the API container user
        except OSError as exc:
            print(f"warning: could not write health status: {exc}", file=sys.stderr)


# ── moving files in and out of containers ──────────────────────────────
#
# 2026-09-28: large output streamed through `podman exec` (podman 3.4) was
# cut short at random — a pg_dump written to a host file that way came out
# 15 MB instead of 20 MB and still "listed" fine.  So data never travels
# through an exec stream: commands write files INSIDE the container, and
# `podman cp` moves them, checked by sha256 on both sides.


def container_sha256(host: "Host", container: str, inner: str) -> str:
    res = host.run(["podman", "exec", container, "sha256sum", inner], 600)
    if res.rc != 0 or not res.out.split():
        raise BackupError(f"cannot checksum {inner} in {container}")
    return res.out.split()[0]


def copy_out(host: "Host", container: str, inner: str, out: Path) -> None:
    """``podman cp`` a container file to the host; checksums must match."""
    want = container_sha256(host, container, inner)
    res = host.run(["podman", "cp", f"{container}:{inner}", str(out)], 1800)
    if res.rc != 0 or not out.is_file():
        raise BackupError(f"podman cp {inner} failed: {res.err.strip()[:200]}")
    os.chmod(out, 0o600)
    if sha256_file(out) != want:
        raise BackupError(f"{inner} changed while copying out of {container}")


def copy_in(host: "Host", src: Path, container: str, inner: str) -> None:
    """``podman cp`` a host file into a container; checksums must match."""
    res = host.run(["podman", "cp", str(src), f"{container}:{inner}"], 1800)
    if res.rc != 0:
        raise BackupError(f"podman cp into {container} failed: {res.err.strip()[:200]}")
    if container_sha256(host, container, inner) != sha256_file(src):
        raise BackupError(f"{src.name} changed while copying into {container}")


def remove_inner(host: "Host", container: str, inner: str) -> None:
    host.run(["podman", "exec", container, "rm", "-f", inner], 60)


# ── database snapshot + dump ───────────────────────────────────────────

COUNT_SQL = """
SELECT n.nspname || '.' || c.relname || '|' ||
  (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', n.nspname, c.relname), false, true, '')))[1]::text || '|' ||
  (xpath('/row/h/text()', query_to_xml(format(
     'select md5(coalesce(string_agg(t::text, %L order by t::text), %L)) as h from (select * from %I.%I x order by x::text limit 10) t',
     '|', '', n.nspname, c.relname), false, true, '')))[1]::text
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY 1
"""


def table_stats(query: Callable[[str], List[str]]) -> Dict[str, Dict[str, Any]]:
    """Row count + a hash of 10 sampled rows per table (same snapshot)."""
    stats: Dict[str, Dict[str, Any]] = {}
    for line in query(COUNT_SQL):
        if not line.strip():
            continue
        name, count, sample = line.split("|", 2)
        stats[name] = {"rows": int(count), "sample_md5": sample}
    return stats


def dump_database(cfg: Config, host: Host, db: str, out: Path) -> Dict[str, Any]:
    """Counts + samples + ``pg_dump`` of ONE snapshot; verifies the dump."""
    session = host.psql_session(cfg.pg_container, cfg.role, db)
    try:
        session.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snap = session.query("SELECT pg_export_snapshot()")[0].strip()
        taken = session.query("SELECT now()")[0].strip()
        version = session.query("SHOW server_version")[0].strip()
        alembic: List[str] = []
        if session.query("SELECT to_regclass('public.alembic_version') IS NOT NULL")[0].strip() == "t":
            alembic = session.query("SELECT coalesce(string_agg(version_num, ','), '') FROM alembic_version")
        stats = table_stats(session.query)
        inner = f"/tmp/stf_backup_{uuid.uuid4().hex[:8]}_{db}.dump"
        try:
            res = host.run(["podman", "exec", cfg.pg_container, "pg_dump", "-U", cfg.role, "-d", db,
                            "-Fc", f"--snapshot={snap}", f"--lock-wait-timeout={cfg.lock_wait_s}s",
                            "-f", inner], timeout=3600)
            if res.rc != 0:
                raise BackupError(f"pg_dump {db} failed rc={res.rc}: {res.err.strip()[:300]}")
            # FM-1: read EVERY byte back (a TOC listing passes on a cut file)
            full = host.run(["podman", "exec", cfg.pg_container, "pg_restore", "-f", "/dev/null", inner], 3600)
            if full.rc != 0:
                raise BackupError(f"dump of {db} is incomplete: {full.err.strip()[:200]}")
            listing = host.run(["podman", "exec", cfg.pg_container, "pg_restore", "--list", inner], 600)
            data_entries = sum(1 for ln in listing.out.splitlines() if " TABLE DATA " in ln)
            if listing.rc != 0 or data_entries < len(stats):
                raise BackupError(f"dump of {db} lists {data_entries} table data entries, "
                                  f"snapshot saw {len(stats)} tables")
            copy_out(host, cfg.pg_container, inner, out)
        finally:
            remove_inner(host, cfg.pg_container, inner)
    finally:
        session.close()
    return {"snapshot_at": taken, "server_version": version,
            "alembic": (alembic[0].strip() if alembic else ""), "tables": stats,
            "file": out.name, "bytes": out.stat().st_size, "sha256": sha256_file(out)}


def dump_roles(cfg: Config, host: Host, out: Path) -> Dict[str, Any]:
    """Role definitions without passwords (they come back from .env)."""
    inner = f"/tmp/stf_backup_{uuid.uuid4().hex[:8]}_roles.sql"
    try:
        res = host.run(["podman", "exec", cfg.pg_container, "pg_dumpall", "-U", cfg.role,
                        "--roles-only", "--no-role-passwords", "-f", inner], timeout=300)
        if res.rc != 0:
            raise BackupError(f"role dump failed: {res.err.strip()[:200]}")
        copy_out(host, cfg.pg_container, inner, out)
    finally:
        remove_inner(host, cfg.pg_container, inner)
    return {"file": out.name, "sha256": sha256_file(out)}


# ── file volumes ───────────────────────────────────────────────────────


# Runtime state that is rewritten every minute and rebuilt on its own: the
# host GPU worker's heartbeat (atomic rename in the manual volume's top
# directory).  Archiving "." made tar fail with "file changed as we read it"
# whenever a heartbeat landed mid-run (2026-09-28), so the top-level entries
# are archived one by one and these are left out.  Any OTHER change still
# fails the run.
VOLATILE_PREFIXES = (".gpu_worker_status",)


def archive_volume(host: Host, volume: str, out: Path) -> Dict[str, Any]:
    """Tars one volume inside the user namespace (files keep their owners)."""
    mp = host.run(["podman", "volume", "inspect", volume, "--format", "{{.Mountpoint}}"], 30)
    if mp.rc != 0 or not mp.out.strip():
        raise BackupError(f"volume {volume} not found (was it removed? FM-8)")
    mount = mp.out.strip()
    top = host.run(["podman", "unshare", "find", mount, "-mindepth", "1", "-maxdepth", "1", "-printf", "%f\\n"], 120)
    if top.rc != 0:
        raise BackupError(f"cannot list volume {volume}: {top.err.strip()[:200]}")
    entries = sorted(n for n in top.out.split("\n") if n and not n.startswith(VOLATILE_PREFIXES))
    counted = host.run(["podman", "unshare", "find", mount, "-type", "f", "!", "-name",
                        VOLATILE_PREFIXES[0] + "*", "-printf", "."], 600)
    if counted.rc != 0:
        raise BackupError(f"cannot list volume {volume}: {counted.err.strip()[:200]}")
    before = len(counted.out)
    names = ["--", *entries] if entries else ["-T", "/dev/null"]
    res = host.run(["podman", "unshare", "tar", "-C", mount, "-czf", "-", *names], 3600, stdout_path=out)
    if res.rc != 0:
        raise BackupError(f"tar of {volume} failed rc={res.rc}: {res.err.strip()[:200]}")
    listed = host.run(["tar", "-tzf", str(out)], 600)
    if listed.rc != 0:
        raise BackupError(f"archive of {volume} is unreadable")
    files = sum(1 for ln in listed.out.splitlines() if ln and not ln.endswith("/"))
    if files < before:
        raise BackupError(f"archive of {volume} has {files} files, volume had {before}")
    return {"file": out.name, "files": files, "bytes": out.stat().st_size,
            "sha256": sha256_file(out), "taken_at": host.now().isoformat()}


# ── encryption ─────────────────────────────────────────────────────────


def gpg_base(cfg: Config, passphrase_file: Path) -> List[str]:
    """Non-interactive gpg; the passphrase only ever comes from a file."""
    return ["gpg", "--homedir", str(cfg.gnupg), "--batch", "--yes", "--quiet",
            "--pinentry-mode", "loopback", "--no-symkey-cache", "--passphrase-file", str(passphrase_file)]


def encrypt(cfg: Config, host: Host, plain: Path, enc: Path, passphrase_file: Path) -> None:
    res = host.run(gpg_base(cfg, passphrase_file) + ["--symmetric", "--cipher-algo", "AES256",
                                                     "--compress-algo", "none", "-o", str(enc), str(plain)], 3600)
    if res.rc != 0:
        raise BackupError(f"encryption failed: {res.err.strip()[:200]}")


def decrypt(cfg: Config, host: Host, enc: Path, out: Path, passphrase_file: Path) -> None:
    res = host.run(gpg_base(cfg, passphrase_file) + ["-o", str(out), "-d", str(enc)], 3600)
    if res.rc != 0:
        raise BackupError(f"decryption failed: {res.err.strip()[:200]}")


# ── offsite ────────────────────────────────────────────────────────────


def offsite_ready(cfg: Config, host: Host) -> Optional[str]:
    """None when the share is a mounted network FS, else the reason (FM-22)."""
    if cfg.offsite is None:
        return "offsite disabled"
    res = host.run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(cfg.offsite.parent)], 15)
    fstype = res.out.strip().splitlines()[0] if res.rc == 0 and res.out.strip() else ""
    if fstype not in OFFSITE_FSTYPES:
        return f"{cfg.offsite.parent} is not a network share (fstype={fstype or 'unknown'})"
    mk = host.run(["mkdir", "-p", str(cfg.offsite)], 30)
    if mk.rc != 0:
        return f"cannot create {cfg.offsite}: {mk.err.strip()[:120]}"
    return None


def offsite_list(cfg: Config, host: Host) -> List[str]:
    res = host.run(["ls", "-1", str(cfg.offsite)], 60)
    if res.rc != 0:
        raise BackupError(f"cannot list share: {res.err.strip()[:120]}")
    return [n for n in res.out.splitlines() if n]


def offsite_sync(cfg: Config, host: Host, local_names: List[str]) -> Dict[str, Any]:
    """Copies missing local bundles (newest first), verifies, prunes (FM-21)."""
    info: Dict[str, Any] = {"copied": [], "error": None}
    why = offsite_ready(cfg, host)
    if why:
        info["error"] = why
        return info
    assert cfg.offsite is not None
    have = set(offsite_list(cfg, host))
    for name in sorted(local_names, reverse=True):
        if name in have:
            continue
        src = cfg.daily / name
        tmp = cfg.offsite / f".tmp-{name}"
        cp = host.run(["cp", str(src), str(tmp)], cfg.offsite_timeout_s)
        if cp.rc != 0:
            info["error"] = f"copy {name} failed: {cp.err.strip()[:120]}"
            host.run(["rm", "-f", str(tmp)], 60)
            break
        chk = host.run(["sha256sum", str(tmp)], cfg.offsite_timeout_s)
        if chk.rc != 0 or chk.out.split()[:1] != [sha256_file(src)]:
            info["error"] = f"checksum mismatch on share for {name}"
            host.run(["rm", "-f", str(tmp)], 60)
            break
        mv = host.run(["mv", str(tmp), str(cfg.offsite / name)], 60)
        if mv.rc != 0:
            info["error"] = f"rename on share failed for {name}"
            break
        info["copied"].append(name)
        have.add(name)
    names = sorted(have)
    if not info["error"]:            # never prune the share after a failed copy
        names = offsite_list(cfg, host)
        for old in select_to_delete(names, cfg.keep_daily, cfg.keep_weekly):
            host.run(["rm", "-f", str(cfg.offsite / old)], 60)
        names = [n for n in names if n not in set(select_to_delete(names, cfg.keep_daily, cfg.keep_weekly))]
    finished = [n for n in names if parse_bundle_time(n)]
    info["latest"] = max(finished) if finished else None
    return info


# ── the daily run ──────────────────────────────────────────────────────


def wait_db(cfg: Config, host: Host) -> None:
    for attempt in range(cfg.db_ready_tries):
        if host.run(["podman", "exec", cfg.pg_container, "pg_isready", "-q"], 30).rc == 0:
            return
        if attempt + 1 < cfg.db_ready_tries:
            host.sleep(cfg.db_ready_sleep_s)
    raise BackupError("database not ready (FM-34)")


def git_head(cfg: Config, host: Host) -> str:
    res = host.run(["git", "-C", str(cfg.repo), "rev-parse", "HEAD"], 30)
    return res.out.strip() if res.rc == 0 else "unknown"


def build_bundle(cfg: Config, host: Host, work: Path, started: dt.datetime) -> Dict[str, Any]:
    """Dumps, archives and a manifest into ``work`` (the plaintext stage)."""
    manifest: Dict[str, Any] = {"schema": SCHEMA, "started_at": started.isoformat(),
                                "host": socket.gethostname(), "git_commit": git_head(cfg, host),
                                "databases": {}, "volumes": {}, "not_backed_up": NOT_BACKED_UP}
    for db in cfg.databases:                                   # FM-10: databases first
        manifest["databases"][db] = dump_database(cfg, host, db, work / f"db_{db}.dump")
    manifest["databases_done_at"] = host.now().isoformat()
    manifest["roles"] = dump_roles(cfg, host, work / "roles.sql")
    for vol in cfg.volumes:                                    # ... then the files
        manifest["volumes"][vol] = archive_volume(host, vol, work / f"vol_{vol}.tar.gz")
    env = cfg.repo / "infra" / ".env"
    if not env.is_file():
        raise BackupError("infra/.env missing (FM-24)")
    shutil.copyfile(env, work / "infra.env")
    os.chmod(work / "infra.env", 0o600)
    manifest["env_sha256"] = sha256_file(work / "infra.env")
    manifest["finished_at"] = host.now().isoformat()
    write_json_atomic(work / "manifest.json", manifest)
    return manifest


def pack(work: Path, out: Path) -> None:
    """Plain tar of the stage (contents are already compressed)."""
    with tarfile.open(out, "w") as tar:
        for p in sorted(work.iterdir()):
            tar.add(p, arcname=p.name)


def run_backup(cfg: Config, host: Host) -> int:
    """One daily run.  Returns 0 when the local bundle is a verified success."""
    for d in (cfg.root, cfg.daily, cfg.tmp, cfg.gnupg):
        ensure_private_dir(d)
    lock_fh = open(cfg.root / ".lock", "w")
    if not acquire_lock(lock_fh):
        print("another backup is running; skipped (FM-26)")
        return 0
    status = load_status(cfg)
    started = host.now()
    status.update(last_attempt_at=started.isoformat(), last_result="running", last_error=None)
    save_status(cfg, host, status)
    run_id = uuid.uuid4().hex[:8]
    work = cfg.tmp / f"run-{run_id}"
    plain = cfg.tmp / f"run-{run_id}.tar"
    name = bundle_name(started)
    part = cfg.daily / f".tmp-{name}"
    try:
        if not cfg.passphrase_file.is_file():
            raise BackupError("passphrase file missing (run: backup.py init)")
        free = host.free_gb(cfg.root)
        if free < cfg.min_free_gb:
            raise BackupError(f"only {free:.0f} GB free, need {cfg.min_free_gb:.0f} (FM-33)")
        wait_db(cfg, host)
        ensure_private_dir(work)
        manifest = build_bundle(cfg, host, work, started)
        pack(work, plain)
        plain_sha = sha256_file(plain)
        encrypt(cfg, host, plain, part, cfg.passphrase_file)
        check = cfg.tmp / f"run-{run_id}.check"
        decrypt(cfg, host, part, check, cfg.passphrase_file)
        ok = sha256_file(check) == plain_sha
        check.unlink()
        if not ok:
            raise BackupError("encrypted bundle does not decrypt to the same bytes")
        os.chmod(part, 0o600)
        os.replace(part, cfg.daily / name)
        (cfg.daily / f"{name}.sha256").write_text(sha256_file(cfg.daily / name) + "\n")
        os.chmod(cfg.daily / f"{name}.sha256", 0o600)
    except (BackupError, OSError) as exc:
        status.update(last_result="failed", last_error=str(exc)[:500])
        save_status(cfg, host, status)
        print(f"backup FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for p in (plain, part):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    local = sorted(n for n in os.listdir(cfg.daily) if parse_bundle_time(n))
    removed = select_to_delete(local, cfg.keep_daily, cfg.keep_weekly)
    for old in removed:
        for p in (cfg.daily / old, cfg.daily / f"{old}.sha256"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    local = [n for n in local if n not in removed]
    status.update(last_result="ok", last_success_at=host.now().isoformat(), last_success_bundle=name,
                  last_success_size=(cfg.daily / name).stat().st_size,
                  last_manifest={"git_commit": manifest["git_commit"],
                                 "alembic": {d: v["alembic"] for d, v in manifest["databases"].items()},
                                 "tables": {d: len(v["tables"]) for d, v in manifest["databases"].items()}},
                  local_bundles=len(local))
    off = offsite_sync(cfg, host, local)
    prev = status.get("offsite") or {}
    status["offsite"] = {
        "last_result": "failed" if off["error"] else "ok",
        "last_error": off["error"],
        "last_success_at": host.now().isoformat() if not off["error"] else prev.get("last_success_at"),
        "latest_bundle": off.get("latest") or prev.get("latest_bundle"),
        "copied": len(off["copied"]),
    }
    save_status(cfg, host, status)
    print(f"backup ok: {name} ({status['last_success_size'] / 1e6:.0f} MB); offsite: "
          f"{'ok' if not off['error'] else 'FAILED - ' + off['error']}")
    return 0


# ── restore drill + partial restore ────────────────────────────────────

FINALIZE_SQL = Path(__file__).with_name("sql") / "restore_finalize.sql"


def drill_exec(host: Host, argv: Sequence[str], timeout: float = 600, **kw: Any) -> Result:
    """``podman exec`` into the drill container; ``-i`` only when stdin is fed
    (with ``-i`` and piped output, large output was cut mid-row)."""
    fed = kw.get("stdin_path") is not None or kw.get("input_text") is not None
    return host.run(["podman", "exec"] + (["-i"] if fed else []) + [DRILL_CONTAINER] + list(argv),
                    timeout, **kw)


def drill_psql(host: Host, db: str, sql: str) -> List[str]:
    res = drill_exec(host, ["psql", "-U", "postgres", "-d", db, "-X", "-A", "-t", "-q",
                            "-v", "ON_ERROR_STOP=1", "-c", sql])
    if res.rc != 0:
        raise BackupError(f"drill psql on {db}: {res.err.strip()[:300]}")
    return [ln for ln in res.out.splitlines() if ln.strip()]


def remove_drill_container(host: Host, timeout: float = 120) -> None:
    """Drops the drill container together with its data volume.

    The Postgres image declares a VOLUME for its data directory, so the
    container gets an anonymous volume holding a full plaintext copy of both
    restored databases; ``rm -f`` without ``-v`` left one behind per drill
    (seven found 2026-09-28).
    """
    host.run(["podman", "rm", "-f", "-v", DRILL_CONTAINER], timeout)


def start_drill_container(cfg: Config, host: Host) -> None:
    """Throwaway Postgres, same image, NO network (only podman exec reaches it)."""
    img = host.run(["podman", "inspect", cfg.pg_container, "--format", "{{.ImageName}}"], 30)
    if img.rc != 0 or not img.out.strip():
        raise BackupError("cannot read the database image name")
    remove_drill_container(host, 60)
    res = host.run(["podman", "run", "-d", "--rm", "--name", DRILL_CONTAINER, "--network", "none",
                    "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_USER=postgres",
                    img.out.strip()], 300)
    if res.rc != 0:
        raise BackupError(f"cannot start drill container: {res.err.strip()[:200]}")
    for _ in range(60):
        if drill_exec(host, ["pg_isready", "-U", "postgres", "-q"], 30).rc == 0:
            # the image restarts once after init; wait for the final server
            host.sleep(3)
            if drill_exec(host, ["pg_isready", "-U", "postgres", "-q"], 30).rc == 0:
                return
        host.sleep(2)
    raise BackupError("drill database did not become ready")


def restore_into_drill(host: Host, stage: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Roles, then each database; compares counts + samples with the manifest."""
    copy_in(host, stage / manifest["roles"]["file"], DRILL_CONTAINER, "/tmp/roles.sql")
    roles = drill_exec(host, ["psql", "-U", "postgres", "-d", "postgres", "-X", "-q", "-f", "/tmp/roles.sql"])
    role_errors = [ln for ln in roles.err.splitlines() if "ERROR" in ln and "already exists" not in ln]
    if role_errors:
        raise BackupError(f"roles restore: {role_errors[0][:200]}")
    report: Dict[str, Any] = {}
    for db, info in manifest["databases"].items():
        mk = drill_exec(host, ["createdb", "-U", "postgres", "-T", "template0", db], 120)
        if mk.rc != 0:
            raise BackupError(f"createdb {db}: {mk.err.strip()[:200]}")
        copy_in(host, stage / info["file"], DRILL_CONTAINER, f"/tmp/{info['file']}")
        rs = drill_exec(host, ["pg_restore", "-U", "postgres", "-d", db, "--exit-on-error",
                               f"/tmp/{info['file']}"], 3600)
        remove_inner(host, DRILL_CONTAINER, f"/tmp/{info['file']}")
        if rs.rc != 0:
            raise BackupError(f"pg_restore {db}: {rs.err.strip()[:300]}")
        got = table_stats(lambda sql, _db=db: drill_psql(host, _db, sql))
        want = info["tables"]
        mismatches = sorted(t for t in set(want) | set(got) if want.get(t) != got.get(t))
        report[db] = {"tables": len(want), "rows": sum(v["rows"] for v in want.values()),
                      "mismatches": mismatches}
    return report


def finalize_restored(host: Host, db: str = "stf_v3") -> Dict[str, int]:
    """FM-9: cancel / fail unfinished jobs and reset the model service state."""
    drill_psql(host, db, FINALIZE_SQL.read_text())
    left = drill_psql(host, db, "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo','doing')")
    model = drill_psql(host, db, "SELECT count(*) FROM model_service_state WHERE state <> 'stopped' OR started_by_us")
    return {"unfinished_jobs": int(left[0]), "model_not_reset": int(model[0])}


CONTENT_TABLES = ("obd_logs", "diagnosis_conversations", "messages", "reports", "audit_events")
OWNERSHIP_TABLES = ("workshops", "memberships", "users", "invite_codes", "vehicles", "vehicle_devices")


def content_filters(vehicle_id: str, conversation_id: Optional[str]) -> Dict[str, str]:
    """WHERE clauses per content table (never ownership rows, FM-17)."""
    uuid.UUID(vehicle_id)
    if conversation_id:
        uuid.UUID(conversation_id)
        conv = f"id = '{conversation_id}' AND vehicle_id = '{vehicle_id}'"
    else:
        conv = f"vehicle_id = '{vehicle_id}'"
    in_conv = f"conversation_id IN (SELECT id FROM diagnosis_conversations WHERE {conv})"
    return {
        "obd_logs": f"id IN (SELECT obd_log_id FROM diagnosis_conversations WHERE {conv})",
        "diagnosis_conversations": conv,
        "messages": in_conv, "reports": in_conv, "audit_events": in_conv,
    }


def copy_content(host: Host, src: Tuple[str, str, str], dst: Tuple[str, str, str],
                 vehicle_id: str, conversation_id: Optional[str] = None,
                 workdir: Optional[Path] = None) -> Dict[str, int]:
    """Copies one vehicle's (or one conversation's) diagnosis content.

    ``src`` / ``dst`` are ``(container, superuser, db)``.  Rows already in
    the target are skipped; ownership tables are never written.  The whole
    copy is one transaction on the target.
    """
    filters = content_filters(vehicle_id, conversation_id)
    work = workdir or Path(os.environ.get("TMPDIR", "/tmp"))
    exists = host.run(["podman", "exec", dst[0], "psql", "-U", dst[1], "-d", dst[2], "-X", "-A", "-t", "-q",
                       "-c", f"SELECT count(*) FROM vehicles WHERE id = '{vehicle_id}'"], 60)
    if exists.rc != 0 or exists.out.strip() != "1":
        raise BackupError("vehicle not in the target database: restore the whole database or add it first")
    script = ["\\set ON_ERROR_STOP on", "BEGIN;"]
    copied: Dict[str, int] = {}
    for table in CONTENT_TABLES:
        # Read into a FILE and without `-i`: large output of `podman exec -i`
        # through a pipe arrived cut mid-row on the server (2026-09-28);
        # pg_dump's output written the same way is intact.
        data_file = work / f"copy-{uuid.uuid4().hex[:8]}-{table}.tsv"
        inner = f"/tmp/stf_copy_{table}.tsv"
        out = host.run(["podman", "exec", src[0], "psql", "-U", src[1], "-d", src[2], "-X", "-q", "-c",
                        f"\\copy (SELECT * FROM {table} WHERE {filters[table]}) TO '{inner}'"], 600)
        if out.rc != 0:
            raise BackupError(f"read {table} from backup: {out.err.strip()[:200]}")
        try:
            copy_out(host, src[0], inner, data_file)
            rows = data_file.read_text(encoding="utf-8")
        finally:
            remove_inner(host, src[0], inner)
            if data_file.exists():
                data_file.unlink()
        copied[table] = rows.count("\n")
        script += [f"CREATE TEMP TABLE s_{table} (LIKE {table});", f"COPY s_{table} FROM STDIN;"]
        script.append(rows.rstrip("\n") + ("\n\\." if rows else "\\."))
        script.append(f"INSERT INTO {table} OVERRIDING SYSTEM VALUE SELECT * FROM s_{table} ON CONFLICT DO NOTHING;")
    script.append("COMMIT;")
    # The script (COPY data inline) also goes to psql from a private file.
    script_file = work / f"copy-{uuid.uuid4().hex[:8]}.sql"
    fd = os.open(script_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(script) + "\n")
    inner = f"/tmp/{script_file.name}"
    try:
        copy_in(host, script_file, dst[0], inner)
        res = host.run(["podman", "exec", dst[0], "psql", "-U", dst[1], "-d", dst[2], "-X", "-q", "-f", inner], 600)
    finally:
        script_file.unlink()
        remove_inner(host, dst[0], inner)
    if res.rc != 0:
        raise BackupError(f"write into target failed (nothing written): {res.err.strip()[:300]}")
    return copied


def read_passphrase(cfg: Config, ask: bool, tmpdir: Path, key_file: Optional[str] = None) -> Path:
    """The passphrase file to use (FM-51).

    ``key_file``: the escrowed recovery-key file (only its FIRST line is the
    key; gpg reads just that line).  ``ask``: the operator types it.
    Default: the server's own copy.
    """
    if key_file:
        return Path(key_file)
    if not ask:
        return cfg.passphrase_file
    typed = getpass.getpass("backup passphrase (offline copy): ")
    path = tmpdir / "pp"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(typed)
    return path


def pick_bundle(cfg: Config, host: Host, offsite: bool, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit)
    if offsite:
        if offsite_ready(cfg, host):
            raise BackupError("network share not available")
        assert cfg.offsite is not None
        names = sorted(n for n in offsite_list(cfg, host) if parse_bundle_time(n))
        if not names:
            raise BackupError("no bundle on the network share")
        return cfg.offsite / names[-1]
    names = sorted(n for n in os.listdir(cfg.daily) if parse_bundle_time(n))
    if not names:
        raise BackupError("no local bundle")
    return cfg.daily / names[-1]


def run_drill(cfg: Config, host: Host, *, offsite: bool, ask: bool, keep: bool,
              bundle: Optional[str] = None, key_file: Optional[str] = None) -> int:
    """Restores the latest bundle into a throwaway container and checks it."""
    ensure_private_dir(cfg.tmp)
    tmp = cfg.tmp / f"drill-{uuid.uuid4().hex[:8]}"
    ensure_private_dir(tmp)
    report: Dict[str, Any] = {"ok": False}
    try:
        src = pick_bundle(cfg, host, offsite, bundle)
        report["bundle"] = src.name
        report["source"] = "offsite" if offsite else "local"
        pp = read_passphrase(cfg, ask, tmp, key_file)
        report["key"] = "recovery-key file" if key_file else ("typed" if ask else "server copy")
        plain = tmp / "bundle.tar"
        decrypt(cfg, host, src, plain, pp)
        stage = tmp / "stage"
        ensure_private_dir(stage)
        with tarfile.open(plain) as tar:
            try:
                tar.extractall(stage, filter="data")
            except TypeError:        # python without extraction filters
                tar.extractall(stage)  # our own archive (flat names, no links)
        plain.unlink()
        manifest = json.loads((stage / "manifest.json").read_text())
        for vol, info in manifest["volumes"].items():
            if sha256_file(stage / info["file"]) != info["sha256"]:
                raise BackupError(f"archive {vol} checksum mismatch")
        start_drill_container(cfg, host)
        report["restore"] = restore_into_drill(host, stage, manifest)
        report["finalize"] = finalize_restored(host)
        report["partial"] = partial_drill(host, stage, manifest, tmp)
        bad = [db for db, r in report["restore"].items() if r["mismatches"]]
        report["ok"] = (not bad and report["finalize"] == {"unfinished_jobs": 0, "model_not_reset": 0}
                        and report["partial"].get("ok", False))
    except (BackupError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        report["error"] = str(exc)[:500]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not keep:
            remove_drill_container(host)
    status = load_status(cfg)
    status["last_drill"] = {"at": host.now().isoformat(), "ok": report["ok"],
                            "source": report.get("source"), "bundle": report.get("bundle"),
                            "error": report.get("error")}
    save_status(cfg, host, status)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["ok"] else 1


def partial_drill(host: Host, stage: Path, manifest: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    """Drill for "bring back one vehicle's diagnoses" on a copy (FM-17)."""
    pick = drill_psql(host, "stf_v3", "SELECT vehicle_id FROM diagnosis_conversations "
                                      "GROUP BY vehicle_id ORDER BY count(*) DESC, vehicle_id LIMIT 1")
    if not pick:
        return {"ok": True, "skipped": "no diagnoses in the backup"}
    vid = pick[0].strip()
    drill_psql(host, "postgres", "CREATE DATABASE stf_v3_partial_target TEMPLATE stf_v3")
    own_sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in OWNERSHIP_TABLES)
    before_own = drill_psql(host, "stf_v3_partial_target", own_sql)
    want = {t: int(drill_psql(host, "stf_v3", f"SELECT count(*) FROM {t} WHERE {w}")[0])
            for t, w in content_filters(vid, None).items()}
    drill_psql(host, "stf_v3_partial_target",
               f"DELETE FROM diagnosis_conversations WHERE vehicle_id = '{vid}'")
    copied = copy_content(host, (DRILL_CONTAINER, "postgres", "stf_v3"),
                          (DRILL_CONTAINER, "postgres", "stf_v3_partial_target"), vid, workdir=workdir)
    got = {t: int(drill_psql(host, "stf_v3_partial_target", f"SELECT count(*) FROM {t} WHERE {w}")[0])
           for t, w in content_filters(vid, None).items()}
    after_own = drill_psql(host, "stf_v3_partial_target", own_sql)
    return {"ok": got == want and before_own == after_own, "tables": list(want),
            "rows_expected": sum(want.values()), "rows_back": sum(got.values()),
            "rows_copied": sum(copied.values()), "ownership_unchanged": before_own == after_own}


def run_restore_vehicle(cfg: Config, host: Host, vehicle_id: str, conversation_id: Optional[str]) -> int:
    """Live partial restore from a kept drill container into the real V3 DB."""
    running = host.run(["podman", "container", "exists", DRILL_CONTAINER], 30)
    if running.rc != 0:
        print("run `backup.py drill --keep` first (the backup must be restored in the drill container)",
              file=sys.stderr)
        return 2
    su = host.run(["podman", "exec", cfg.pg_container, "printenv", "POSTGRES_USER"], 30).out.strip()
    try:
        ensure_private_dir(cfg.tmp)
        copied = copy_content(host, (DRILL_CONTAINER, "postgres", "stf_v3"),
                              (cfg.pg_container, su, "stf_v3"), vehicle_id, conversation_id,
                              workdir=cfg.tmp)
    except BackupError as exc:
        print(f"restore-vehicle FAILED (nothing written): {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"copied_rows_offered": copied, "note": "rows already present were skipped"}))
    return 0


# ── init / CLI ─────────────────────────────────────────────────────────


def run_init(cfg: Config) -> int:
    """Private dirs + a random passphrase file (never printed)."""
    for d in (cfg.root, cfg.daily, cfg.tmp, cfg.gnupg):
        ensure_private_dir(d)
    ensure_private_dir(cfg.passphrase_file.parent)
    if cfg.passphrase_file.exists():
        print(f"passphrase file already exists: {cfg.passphrase_file} (kept)")
    else:
        fd = os.open(cfg.passphrase_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32))
        print(f"passphrase written to {cfg.passphrase_file} (mode 600). Keep an OFFLINE copy: "
              f"read it yourself in your own terminal; it is never printed here.")
    return 0


def _on_term(signum: int, frame: Any) -> None:  # noqa: ARG001
    raise SystemExit(143)   # so the finally blocks clean temp files (FM-23)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("run")
    sub.add_parser("status")
    d = sub.add_parser("drill")
    d.add_argument("--offsite", action="store_true", help="use the newest bundle on the network share")
    d.add_argument("--ask-passphrase", action="store_true", help="type the offline passphrase (FM-51)")
    d.add_argument("--passphrase-file", help="the escrowed recovery-key file (first line = key)")
    d.add_argument("--keep", action="store_true", help="keep the drill container for restore-vehicle")
    d.add_argument("--bundle", help="explicit bundle path")
    r = sub.add_parser("restore-vehicle")
    r.add_argument("--vehicle-id", required=True)
    r.add_argument("--conversation-id")
    r.add_argument("--i-am-restoring-live", action="store_true", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    signal.signal(signal.SIGTERM, _on_term)
    cfg, host = Config.from_env(), Host()
    if args.cmd == "init":
        return run_init(cfg)
    if args.cmd == "run":
        return run_backup(cfg, host)
    if args.cmd == "status":
        print(json.dumps(load_status(cfg), indent=1, ensure_ascii=False))
        return 0
    if args.cmd == "drill":
        return run_drill(cfg, host, offsite=args.offsite, ask=args.ask_passphrase, keep=args.keep,
                         bundle=args.bundle, key_file=args.passphrase_file)
    return run_restore_vehicle(cfg, host, args.vehicle_id, args.conversation_id)


if __name__ == "__main__":
    sys.exit(main())

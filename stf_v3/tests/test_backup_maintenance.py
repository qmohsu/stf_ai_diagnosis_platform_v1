"""PROD-15A ② T-14 / T-17 (orchestration): maintenance after a good backup.

A scripted host plays the V3 database: finished conversations with process
events, and old queue jobs.  The SQL itself runs against real Postgres in
``test_db_maintenance.py``; here: order, the count-only default, the backup
gate, and "any failure deletes nothing".

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import pathlib
import re
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest

from tests.test_backup_script import NOW, FakeHost, _cfg, _status, bk

OLD = [str(uuid.uuid4()) for _ in range(3)]


class DbHost(FakeHost):
    """FakeHost whose owner SQL hits a tiny in-memory V3 database."""

    def __init__(self, tmp: pathlib.Path, *, fail: Optional[str] = None,
                 events: Optional[Dict[str, int]] = None, old_jobs: int = 4) -> None:
        super().__init__(tmp, fail=fail)
        self.events = dict(events if events is not None else {c: 5 for c in OLD})
        self.archived: List[str] = []
        self.old_jobs = old_jobs
        self.sql: List[str] = []

    def owner_sql(self, sql: str) -> Any:
        self.sql.append(sql)
        R = bk.Result
        ids = re.findall(r"'([0-9a-f-]{36})'", sql)
        if sql.startswith("SELECT id FROM diagnosis_conversations"):
            return R(0, "".join(c + "\n" for c in self.events if c not in self.archived))
        if sql.startswith("SELECT count(*) FROM audit_events"):
            return R(0, f"{sum(self.events[c] for c in ids)}\n")
        if sql.startswith("SELECT string_agg"):
            return R(0, "id,conversation_id,seq,event_type,payload,created_at\n")
        if sql.startswith("\\copy"):
            if self.fail == "export":
                return R(1, "", "ERROR: could not open file")
            n = sum(self.events[c] for c in ids) - (1 if self.fail == "short_export" else 0)
            inner = sql.rsplit("TO '", 1)[1].rstrip("'")
            self.inner[inner] = "".join(f"{i}\t{{}}\n" for i in range(n)).encode()
            return R(0)
        if sql.startswith("DO $$"):
            if self.fail == "delete":
                return R(1, "", "ERROR: archive: 9 rows to delete, 10 exported")
            for c in ids:
                self.events[c] = 0
                self.archived.append(c)
            return R(0)
        if "procrastinate_jobs" in sql and sql.startswith("WITH gone"):
            n, self.old_jobs = self.old_jobs, 0
            return R(0, f"{n}\n")
        if "procrastinate_jobs" in sql:
            return R(0, f"{self.old_jobs}\n")
        raise AssertionError(f"unexpected SQL {sql[:80]}")


def _good_backup(cfg: Any, host: FakeHost) -> None:
    assert bk.run_backup(cfg, host) == 0


def _apply(cfg: Any) -> None:
    cfg.root.mkdir(parents=True, exist_ok=True)
    (cfg.root / "maintenance.apply").write_text("reviewed")


def test_by_default_it_only_counts(tmp_path: pathlib.Path) -> None:
    """FM-38 / FM-43: without the apply flag it records candidates / rows /
    deleted = 0 and never exports or deletes."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _good_backup(cfg, host)
    m = _status(cfg)["maintenance"]
    assert m["mode"] == "count-only" and m["error"] is None
    assert (m["archive"]["candidates"], m["archive"]["rows"], m["archive"]["exported"],
            m["archive"]["deleted"]) == (3, 15, 0, 0)
    assert (m["queue"]["candidates"], m["queue"]["deleted"]) == (4, 0)
    assert not any(s.startswith(("\\copy", "DO $$", "WITH gone")) for s in host.sql)
    assert not cfg.archive.exists() and sum(host.events.values()) == 15


def test_with_the_flag_it_exports_rereads_then_deletes_in_that_order(tmp_path: pathlib.Path) -> None:
    """FM-4: export → read back (rows + checksum) → delete exactly those rows;
    the archive is a 0600 gzip + a manifest listing the conversations."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _apply(cfg)
    _good_backup(cfg, host)
    m = _status(cfg)["maintenance"]["archive"]
    assert (m["exported"], m["deleted"]) == (15, 15) and sum(host.events.values()) == 0
    order = [s.split()[0] for s in host.sql if s.startswith(("\\copy", "DO"))]
    assert order == ["\\copy", "DO"]
    gz, meta = sorted(cfg.archive.glob("audit_events_*.tsv.gz")), sorted(cfg.archive.glob("*.json"))
    assert len(gz) == 1 and len(meta) == 1
    with gzip.open(gz[0], "rt") as fh:
        assert len(fh.read().splitlines()) == 15
    info = json.loads(meta[0].read_text())
    assert info["rows"] == 15 and sorted(info["conversations"]) == sorted(OLD)
    assert _status(cfg)["maintenance"]["queue"]["deleted"] == 4
    assert not any(k.startswith("/tmp/stf_archive_") for k in host.inner)     # container file removed
    if sys.platform != "win32":
        assert all((p.stat().st_mode & 0o777) == 0o600 for p in gz + meta)


@pytest.mark.parametrize("fail", ["export", "short_export", "cp_differs", "delete"])
def test_any_failure_before_or_during_the_delete_deletes_nothing(tmp_path: pathlib.Path, fail: str) -> None:
    """FM-4: a failed export, a short export, a copy that differs, or a delete
    whose row count does not match → nothing deleted, error recorded, and the
    backup itself stays a success."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path, events={})
    _apply(cfg)
    _good_backup(cfg, host)                 # a clean backup first (cp_differs would break it) …
    host.fail = fail                        # … then the fault, for this maintenance run only
    host.events = {c: 5 for c in OLD}
    host.old_jobs, host.sql = 4, []
    before = dict(host.events)
    assert bk.run_maintenance(cfg, host) == 1
    st = _status(cfg)
    assert st["last_result"] == "ok" and st["maintenance"]["error"]
    assert host.events == before and host.archived == []
    assert not any(s.startswith("WITH gone") for s in host.sql)             # queue untouched too
    assert list(cfg.archive.glob("audit_events_*")) == []                   # no half archive left


def test_without_a_successful_backup_today_it_is_skipped(tmp_path: pathlib.Path) -> None:
    """FM-5: no fresh success → no SQL at all, a warning in the status."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path, fail="pg_dump")
    _apply(cfg)
    assert bk.run_backup(cfg, host) == 1
    assert bk.run_maintenance(cfg, host) == 0
    assert "no successful backup" in _status(cfg)["maintenance"]["skipped"]
    assert host.sql == []


def test_a_backup_older_than_a_day_does_not_count(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FM-5: yesterday's success is not today's."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _good_backup(cfg, host)
    host.sql.clear()
    monkeypatch.setattr(host, "now", lambda: NOW + dt.timedelta(hours=25))
    assert bk.run_maintenance(cfg, host) == 0
    assert host.sql == [] and "skipped" in _status(cfg)["maintenance"]


def test_repeated_runs_never_overwrite_an_archive(tmp_path: pathlib.Path) -> None:
    """FM-27: each run's files carry its own run id; an existing name is refused."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path, events={OLD[0]: 2})
    _apply(cfg)
    _good_backup(cfg, host)
    host.events[OLD[1]] = 3                         # more old conversations the same day
    assert bk.run_maintenance(cfg, host) == 0
    assert len(list(cfg.archive.glob("*.tsv.gz"))) == 2
    first = sorted(cfg.archive.glob("*.tsv.gz"))[0]
    with pytest.raises(FileExistsError):
        bk._create_private(first)


def test_archive_files_ride_in_the_next_bundle(tmp_path: pathlib.Path) -> None:
    """FM-6: the archive directory is part of every later backup."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _apply(cfg)
    _good_backup(cfg, host)
    work = tmp_path / "stage"
    work.mkdir()
    manifest = bk.build_bundle(cfg, host, work, NOW)
    assert manifest["archive"]["files"] == 2 and (work / "archive.tar").is_file()


def test_the_cutoffs_are_180_and_30_days(tmp_path: pathlib.Path) -> None:
    """D5: process events 180 days; queue history 30 days (both from 'now')."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _good_backup(cfg, host)
    m = _status(cfg)["maintenance"]
    assert m["archive"]["cutoff"] == (NOW - dt.timedelta(days=180)).isoformat()
    assert m["queue"]["cutoff"] == (NOW - dt.timedelta(days=30)).isoformat()


def test_candidate_ids_are_validated_uuids() -> None:
    """Only UUIDs can reach the SQL text (the ids come from the database, but
    a malformed line must never become SQL)."""
    with pytest.raises(ValueError):
        bk.uuid_array(["x'); DROP TABLE audit_events; --"])
    assert bk.uuid_array([str(uuid.UUID(int=1))]).startswith("ARRAY['00000000-")


# ── T-28 / FM-47: the weekly light check ───────────────────────────────


def test_the_weekly_check_decrypts_the_share_copy_and_checks_every_file(tmp_path: pathlib.Path) -> None:
    """Newest bundle on the share: decrypted, every manifest file present with
    its checksum; result in the status; no plaintext left behind."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _good_backup(cfg, host)
    assert bk.run_verify(cfg, host) == 0
    v = _status(cfg)["last_verify"]
    assert v["ok"] and v["source"] == "offsite" and v["files_checked"] == 9 and v["error"] is None
    assert list(cfg.tmp.iterdir()) == []
    assert json.loads((host.state_dir / "status.json").read_text())["last_verify"]["ok"] is True


def test_the_weekly_check_catches_a_changed_file(tmp_path: pathlib.Path) -> None:
    """One byte changed inside a dump (the archive itself still opens) → failed."""
    cfg, host = _cfg(tmp_path), DbHost(tmp_path)
    _good_backup(cfg, host)
    share = sorted(cfg.offsite.glob("stf-backup_*"))[-1]
    plain = share.read_bytes()[::-1]                      # the fake "encryption" reverses bytes
    i = plain.index(b"PGDMP-stf_v3")
    share.write_bytes((plain[:i] + b"X" + plain[i + 1:])[::-1])
    assert bk.run_verify(cfg, host) == 1
    assert "db_stf_v3.dump checksum differs" in _status(cfg)["last_verify"]["error"]


def test_the_weekly_check_runs_sundays_with_zone_and_catch_up() -> None:
    """FM-30 / FM-31: explicit zone, missed runs caught up; it only verifies."""
    ops = pathlib.Path(bk.__file__).resolve().parents[1] / "ops"
    timer = (ops / "stf-v3-backup-verify.timer").read_text()
    unit = (ops / "stf-v3-backup-verify.service").read_text()
    assert "OnCalendar=Sun *-*-* 05:00:00 Asia/Hong_Kong" in timer and "Persistent=true" in timer
    assert "backup.py verify" in unit and "UMask=0077" in unit and "TimeoutStartSec=" in unit
    installer = (ops / "install_backup.sh").read_text()
    assert "stf-v3-backup-verify.timer" in installer and "maintenance.apply" in installer

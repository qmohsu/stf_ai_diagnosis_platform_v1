"""PROD-15A T-18: orphaned manual work directories are cleaned safely.

Author: Xiangzhu Yan
"""

import datetime as dt
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from stf_v3.knowledge import ingest, tasks
from stf_v3.settings import settings

NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.timezone.utc)


def _row(status: str, days_ago: float, phase: Optional[str] = None, pending: bool = False) -> Dict[str, Any]:
    return {"status": status, "pages_phase": phase, "updated_at": NOW - dt.timedelta(days=days_ago),
            "job_pending": pending}


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A work root beside (not inside) the manual library."""
    work, manuals = tmp_path / "srv" / "manual_builds", tmp_path / "srv" / "manuals"
    work.mkdir(parents=True)
    manuals.mkdir(parents=True)
    monkeypatch.setattr(settings, "manual_work_dir", str(work))
    monkeypatch.setattr(settings, "manual_storage_path", str(manuals))
    return work


def test_an_unset_or_unsafe_work_root_is_refused(roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FM-7: empty, the manual library itself, inside it, or above it → no clean."""
    assert ingest.work_root() == roots.resolve()
    manuals = Path(settings.manual_storage_path)
    for bad in ("", "  ", str(manuals), str(manuals / "sub"), str(manuals.parent), str(roots / ".." / "..")):
        (manuals / "sub").mkdir(exist_ok=True)
        monkeypatch.setattr(settings, "manual_work_dir", bad)
        assert ingest.work_root() is None, bad
    assert ingest.clean_work_dirs(NOW, read_status=lambda _: None)["refused"] == 1


@pytest.mark.parametrize("row, verdict", [
    (_row("failed", 8, "failed"), "delete"),
    (_row("ingested", 8), "delete"),
    (_row("failed", 3, "failed"), "keep: failed recently"),
    (_row("failed", 30, "retrying"), "keep: retry pending"),
    (_row("failed", 30, "failed", pending=True), "keep: ingest job pending"),
    (_row("converting", 30, "building"), "keep: active"),
    (_row("queued", 30, "queued"), "keep: active"),
    (_row("uploading", 30), "keep: active"),
])
def test_only_manuals_failed_for_good_for_seven_days_are_deleted(roots: Path, row: Dict[str, Any], verdict: str) -> None:
    """FM-29: converting / queued / retrying / a pending job keep their work."""
    entry = roots / str(uuid.uuid4())
    entry.mkdir()
    assert ingest.work_dir_verdict(entry, roots.resolve(), lambda _: row, NOW) == verdict


def test_a_deleted_manuals_dir_goes_after_seven_days(roots: Path) -> None:
    """Orphan (manual row gone): judged by the directory's own age."""
    old, new = roots / str(uuid.uuid4()), roots / str(uuid.uuid4())
    old.mkdir()
    new.mkdir()
    ts = (NOW - dt.timedelta(days=9)).timestamp()
    os.utime(old, (ts, ts))
    os.utime(new, (NOW.timestamp(), NOW.timestamp()))
    assert ingest.work_dir_verdict(old, roots.resolve(), lambda _: None, NOW) == "delete"
    assert ingest.work_dir_verdict(new, roots.resolve(), lambda _: None, NOW) == "keep: manual gone recently"


def test_files_odd_names_and_links_are_never_touched(roots: Path) -> None:
    """FM-7: only a UUID-named real directory directly under the root."""
    (roots / "notes.txt").write_text("x")
    (roots / "not-a-uuid").mkdir()
    assert ingest.work_dir_verdict(roots / "notes.txt", roots.resolve(), None, NOW) == "keep: not a directory"
    assert ingest.work_dir_verdict(roots / "not-a-uuid", roots.resolve(), None, NOW) == "keep: unexpected name"
    if sys.platform != "win32":
        target = Path(settings.manual_storage_path)
        link = roots / str(uuid.uuid4())
        link.symlink_to(target, target_is_directory=True)
        assert ingest.work_dir_verdict(link, roots.resolve(), None, NOW) == "keep: link"


def test_a_clean_run_deletes_exactly_the_eligible_dirs(roots: Path) -> None:
    """The run re-reads each manual's status right before deciding (FM-29)."""
    gone_for_good, retrying = str(uuid.uuid4()), str(uuid.uuid4())
    for name in (gone_for_good, retrying):
        (roots / name / "mineru").mkdir(parents=True)
        (roots / name / "mineru" / "page.json").write_text("{}")
    rows = {gone_for_good: _row("failed", 10, "failed"), retrying: _row("failed", 10, "retrying")}
    out = ingest.clean_work_dirs(NOW, read_status=rows.get)
    assert out == {"deleted": 1, "kept": 1, "refused": 0}
    assert not (roots / gone_for_good).exists() and (roots / retrying / "mineru" / "page.json").exists()


def test_the_task_shares_the_ingest_lock_and_never_piles_up() -> None:
    """FM-29: same lock as manual conversion (never concurrent with one);
    a queueing lock keeps at most one waiting run; host GPU queue; daily."""
    task = tasks.clean_work_dirs
    assert task.lock == tasks.INGEST_LOCK and task.queueing_lock == tasks.WORKDIR_TASK
    assert task.queue == tasks.GPU_QUEUE
    periodic = [p for p in tasks.app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == ["40 20 * * *"]

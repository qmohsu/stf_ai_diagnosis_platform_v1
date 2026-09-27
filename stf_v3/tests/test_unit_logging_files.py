"""PROD-15A T-19: logs reach a per-process file, roll daily, age out, stay capped.

Author: Xiangzhu Yan
"""

import gzip
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterator, List

import pytest
import structlog

from stf_v3 import logging_config as lc

DAY = 86400.0
T0 = 1_790_000_000.0          # 2026-09-21 (UTC), an arbitrary fixed "now"


class Clock:
    """A settable clock for the handler."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def restore_logging() -> Iterator[None]:
    """Leaves root handlers, levels and structlog as they were."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)
    structlog.reset_defaults()


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)


def _lines(path: Path) -> List[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        return [ln for ln in fh.read().splitlines() if ln]


def test_business_and_framework_logs_both_reach_the_process_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_logging: None) -> None:
    """FM-63: business logs (structlog) and framework logs (stdlib) share one
    exit — both appear, as JSON, in <dir>/<process>.log."""
    monkeypatch.setenv("STF_V3_LOG_DIR", str(tmp_path / "logs"))
    lc.configure_logging("INFO", process="api")
    structlog.get_logger("stf_v3.test").info("diagnosis.done", conversation_id="c-1")
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    for h in logging.getLogger().handlers:
        h.flush()
    rows = [json.loads(ln) for ln in _lines(tmp_path / "logs" / "api.log")]
    assert {"event": "diagnosis.done", "conversation_id": "c-1"}.items() <= rows[0].items()
    assert rows[1]["event"] == "Application startup complete." and rows[1]["logger"] == "uvicorn.error"
    assert all("timestamp" in r and "level" in r for r in rows)


def test_without_a_log_dir_or_process_nothing_is_written_to_disk(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_logging: None) -> None:
    """Scripts and tests (no process name) and unset STF_V3_LOG_DIR stay on stdout."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STF_V3_LOG_DIR", raising=False)
    lc.configure_logging("INFO", process="api")
    monkeypatch.setenv("STF_V3_LOG_DIR", str(tmp_path / "logs"))
    lc.configure_logging("INFO")
    structlog.get_logger("stf_v3.test").info("x")
    assert not (tmp_path / "logs").exists() and list(tmp_path.iterdir()) == []


def test_a_new_day_rolls_the_file_and_the_new_file_gets_the_lines(tmp_path: Path) -> None:
    """FM-25: after midnight (UTC) the old day is gzipped and the NEW live file
    has content — the process never keeps writing into a renamed file."""
    clock = Clock(T0)
    h = lc.DailyFileHandler(tmp_path / "worker.log", clock=clock)
    h.emit(_record("day one"))
    clock.t += DAY
    h.emit(_record("day two"))
    h.close()
    day1 = lc.DailyFileHandler._day_of(T0)
    assert _lines(tmp_path / f"worker.log.{day1}.gz") == ["day one"]
    assert _lines(tmp_path / "worker.log") == ["day two"]


def test_days_older_than_the_retention_are_deleted(tmp_path: Path) -> None:
    """30 days kept: 31 days on, the first rolled day is gone, later ones stay."""
    clock = Clock(T0)
    h = lc.DailyFileHandler(tmp_path / "api.log", keep_days=30, clock=clock)
    for _ in range(3):
        h.emit(_record("line"))
        clock.t += DAY
    h.emit(_record("line"))                   # rolls day 3
    assert len(h.rolled_files()) == 3
    clock.t += 29 * DAY                       # day 1 is now 31+ days old
    h.emit(_record("line"))
    h.close()
    kept = [p.name for p in h.rolled_files()]
    assert f"api.log.{lc.DailyFileHandler._day_of(T0)}.gz" not in kept
    assert f"api.log.{lc.DailyFileHandler._day_of(T0 + 2 * DAY)}.gz" in kept


def test_the_size_cap_deletes_the_oldest_rolled_days_first(tmp_path: Path) -> None:
    """At the total-size cap the oldest rolled days go; the live file stays."""
    live = tmp_path / "gpu-worker.log"
    for i, day in enumerate(["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]):
        (tmp_path / f"gpu-worker.log.{day}.gz").write_bytes(os.urandom(1000 + i))
    (tmp_path / "other.log.2026-09-01.gz").write_bytes(b"x" * 5000)     # not ours
    h = lc.DailyFileHandler(live, max_bytes=2100, clock=Clock(T0))
    h.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["gpu-worker.log.2026-09-19.gz", "gpu-worker.log.2026-09-20.gz",
                     "other.log.2026-09-01.gz"]


def test_a_rolled_day_is_never_overwritten(tmp_path: Path) -> None:
    """Two rolls for the same day (restart + clock back) keep both files."""
    clock = Clock(T0)
    h = lc.DailyFileHandler(tmp_path / "api.log", clock=clock)
    h.emit(_record("a"))
    clock.t += DAY
    h.emit(_record("b"))
    clock.t -= DAY
    h.emit(_record("c"))
    clock.t += DAY
    h.emit(_record("d"))
    h.close()
    day1 = lc.DailyFileHandler._day_of(T0)
    assert _lines(tmp_path / f"api.log.{day1}.gz") == ["a"]
    assert _lines(tmp_path / f"api.log.{day1}-1.gz") == ["c"]
    assert len(h.rolled_files()) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_log_files_and_directory_are_private(tmp_path: Path) -> None:
    """FM-49: 0600 files, 0700 directory, rolled files included."""
    clock = Clock(T0)
    d = tmp_path / "logs"
    h = lc.DailyFileHandler(d / "api.log", clock=clock)
    h.emit(_record("a"))
    clock.t += DAY
    h.emit(_record("b"))
    h.close()
    assert (d.stat().st_mode & 0o777) == 0o700
    for p in d.iterdir():
        assert (p.stat().st_mode & 0o777) == 0o600, p.name

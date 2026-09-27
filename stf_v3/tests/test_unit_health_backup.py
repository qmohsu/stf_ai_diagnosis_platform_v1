"""PROD-15A T-10 (unit half): /v3/health reports the last SUCCESSFUL backup.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

import pytest


def _write(path: pathlib.Path, **status: Any) -> None:
    path.write_text(json.dumps(status), encoding="utf-8")


@pytest.fixture()
def status_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Points the health check at a temp status file."""
    import stf_v3.main as main

    path = tmp_path / "status.json"
    monkeypatch.setattr(main, "BACKUP_STATUS_PATH", str(path))
    return path


def _ago(hours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()


def test_no_status_file_is_unknown_not_ok(status_file: pathlib.Path) -> None:
    """FM-40: before the first backup there is nothing to trust."""
    from stf_v3.main import backup_status

    b = backup_status()
    assert b["state"] == "unknown" and b["offsite"]["state"] == "unknown"


def test_fresh_success_is_ok_even_after_a_later_failure(status_file: pathlib.Path) -> None:
    """Judged by the last success time, not by the last attempt's result."""
    from stf_v3.main import backup_status

    _write(status_file, last_success_at=_ago(5), last_result="failed",
           offsite={"last_success_at": _ago(5)})
    b = backup_status()
    assert b["state"] == "ok" and b["last_result"] == "failed" and b["offsite"]["state"] == "ok"


def test_older_than_36_hours_is_stale(status_file: pathlib.Path) -> None:
    """T-10: 37 h → stale (deploy_check fails on it); offsite judged the same way."""
    from stf_v3.main import backup_status

    _write(status_file, last_success_at=_ago(37), last_result="ok",
           offsite={"last_success_at": _ago(50)})
    b = backup_status()
    assert b["state"] == "stale" and b["age_h"] >= 37 and b["offsite"]["state"] == "stale"


def test_a_garbled_status_file_is_unknown(status_file: pathlib.Path) -> None:
    """A half-written or corrupt file never reads as a success."""
    from stf_v3.main import backup_status

    status_file.write_text("{not json", encoding="utf-8")
    assert backup_status()["state"] == "unknown"

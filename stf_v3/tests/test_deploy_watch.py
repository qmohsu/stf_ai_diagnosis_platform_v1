"""#272: the deploy watch logs every change to what the server runs.

Every host call (git, podman, systemctl, psql, the OpenAPI fetch) is
scripted, so the tests need no server, no containers and no network.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "deploy_watch.py"
_spec = importlib.util.spec_from_file_location("stf_deploy_watch", _SCRIPT)
assert _spec and _spec.loader
dw: Any = importlib.util.module_from_spec(_spec)
sys.modules["stf_deploy_watch"] = dw
_spec.loader.exec_module(dw)

POSIX = os.name == "posix"
REPO = pathlib.Path("/srv/repo")
T0, T1, T2 = "2026-10-29T07:00:00+00:00", "2026-10-29T07:05:00+00:00", "2026-10-29T07:10:00+00:00"


class FakeHost:
    """Answers the commands the watcher runs; tests edit the answers."""

    def __init__(self) -> None:
        self.head = "073c112 docs(PROD-15A): mark the ticket done"
        self.status = " M stf_v3/README.md\n?? scratch.txt\n?? stf_v3/__pycache__/x.pyc\n"
        self.containers = {
            "stf-v3-api": "sha256abcdef0123456|073c112aaaaaaaa|2026-09-28 09:27:33.38 +0800 HKT|running",
            "stf-nginx": "sha256999999999999||2026-09-01 10:00:00.00 +0800 HKT|exited",
            "stf-vllm": "sha256vllm00000000||2026-10-29 06:00:00.00 +0800 HKT|running",
            "relaxed_curran": "x|y|z|running",
        }
        self.worker_since = "Fri 2026-10-02 18:42:49 HKT"
        self.alembic = {"stf_v3": "0a1b2c3d4e5f", "stf_diagnosis": "9f8e7d6c5b4a"}
        self.fail: Optional[str] = None

    def run(self, cmd: Sequence[str]) -> Tuple[int, str]:
        cmd = list(cmd)
        if cmd[0] == "git":
            if self.fail == "git":
                return 128, ""
            if "rev-parse" in cmd:
                return 0, "main\n"
            if "log" in cmd:
                return 0, self.head + "\n"
            return 0, self.status
        if cmd[:3] == ["podman", "ps", "-a"]:
            if self.fail == "podman":
                return 125, ""
            return 0, "\n".join(self.containers) + "\n"
        if cmd[:2] == ["podman", "inspect"]:
            name = cmd[-1]
            return (0, self.containers[name] + "\n") if name in self.containers else (125, "")
        if cmd[:2] == ["podman", "exec"]:
            db = cmd[-1].split("-d ")[1].split()[0]
            return 0, self.alembic[db] + "\n"
        if cmd[0] == "systemctl":
            since = self.worker_since if "stf-v3-gpu-worker" in cmd else "Mon 2026-09-01 09:00:00 HKT"
            return 0, f"ActiveState=active\nActiveEnterTimestamp={since}\n"
        raise AssertionError(f"unexpected command {cmd}")

    def fetch(self, url: str) -> Optional[bytes]:
        if "8002" in url:
            return json.dumps({"paths": {"/v3/health": {}, "/v3/users/me": {}}}).encode()
        return None


def _snap(host: FakeHost, unit_dir: pathlib.Path) -> Dict[str, str]:
    return dw.collect(REPO, unit_dir, run=host.run, fetch=host.fetch)


def test_collect_reads_checkout_containers_units_contracts_and_schemas(tmp_path):
    """One snapshot covers everything that decides what the server runs."""
    # Arrange
    host = FakeHost()
    (tmp_path / "stf-v3-gpu-worker.service").write_text("[Service]\n")
    (tmp_path / "unrelated.service").write_text("x")

    # Act
    snap = _snap(host, tmp_path)

    # Assert
    assert snap["checkout.branch"] == "main"
    assert snap["checkout.head"].startswith("073c112")
    assert snap["checkout.changed"] == "M stf_v3/README.md"
    assert snap["checkout.untracked"] == "scratch.txt"          # __pycache__ ignored
    assert snap["container.stf-v3-api"] == ("image=sha256abcdef commit=073c112aaaaa "
                                             "created=2026-09-28 09:27:33 state=running")
    assert "commit=- " in snap["container.stf-nginx"]
    assert snap["unit.stf-v3-gpu-worker"] == "active since Fri 2026-10-02 18:42:49 HKT"
    assert "unitfile.stf-v3-gpu-worker.service" in snap
    assert "unitfile.unrelated.service" not in snap
    assert snap["api.v3_openapi"].endswith("(2 paths)")
    assert snap["api.v12_openapi"] == "unreachable"
    assert snap["db.stf_v3_alembic"] == "0a1b2c3d4e5f"
    assert not [k for k in snap if k.startswith("error.")]


def test_collect_skips_on_demand_vllm_and_foreign_containers(tmp_path):
    """vLLM comes and goes on its own (PROD-11) and other projects' containers are not ours."""
    snap = _snap(FakeHost(), tmp_path)
    assert "container.stf-vllm" not in snap
    assert not [k for k in snap if "relaxed_curran" in k]


def test_long_file_lists_are_capped(tmp_path):
    """A directory full of new files stays one readable line."""
    host = FakeHost()
    host.status = "".join(f"?? f{i:03d}.txt\n" for i in range(45))
    snap = _snap(host, tmp_path)
    assert snap["checkout.untracked"].endswith("… +15 more")


def test_first_check_writes_baseline_then_nothing_when_unchanged(tmp_path):
    """The first run is a baseline; an unchanged server adds no log lines."""
    # Arrange
    host, state = FakeHost(), tmp_path / "status"

    # Act
    first = dw.check(state, _snap(host, tmp_path), now=T0)
    second = dw.check(state, _snap(host, tmp_path), now=T1)

    # Assert
    lines = (state / "changes.jsonl").read_text().splitlines()
    assert (first, second) == (0, 0)
    assert len(lines) == 1 and json.loads(lines[0])["baseline"] is True
    assert json.loads((state / "current.json").read_text())["at"] == T1


def test_redeploy_and_edited_file_are_logged_with_before_and_after(tmp_path):
    """A new commit, a recreated container and an edited file each show up once."""
    # Arrange
    host, state = FakeHost(), tmp_path / "status"
    dw.check(state, _snap(host, tmp_path), now=T0)
    host.head = "569a381 fix(ci): V3 tests pass inside the image again"
    host.containers["stf-v3-api"] = "sha256newimage0000|569a381bbbbbbbb|2026-10-29 15:04:00.00 +0800 HKT|running"
    host.status += " M stf_v3/src/stf_v3/main.py\n"

    # Act
    n = dw.check(state, _snap(host, tmp_path), now=T1)

    # Assert
    run = json.loads((state / "changes.jsonl").read_text().splitlines()[-1])
    keys = {c["key"]: c for c in run["changes"]}
    assert n == 3 and set(keys) == {"checkout.head", "container.stf-v3-api", "checkout.changed"}
    assert keys["checkout.head"]["before"].startswith("073c112")
    assert keys["checkout.head"]["after"].startswith("569a381")
    assert "main.py" in keys["checkout.changed"]["after"]


def test_worker_restart_schema_and_contract_changes_are_logged(tmp_path):
    """A host worker restart, a migration and an API change are deploy events too."""
    host, state = FakeHost(), tmp_path / "status"
    dw.check(state, _snap(host, tmp_path), now=T0)
    host.worker_since = "Wed 2026-10-29 15:10:00 HKT"
    host.alembic["stf_v3"] = "1b2c3d4e5f60"
    host.fetch = lambda url: b'{"paths": {"/v3/health": {}}}' if "8002" in url else None

    dw.check(state, _snap(host, tmp_path), now=T1)

    run = json.loads((state / "changes.jsonl").read_text().splitlines()[-1])
    assert {c["key"] for c in run["changes"]} == {
        "unit.stf-v3-gpu-worker", "db.stf_v3_alembic", "api.v3_openapi"}


def test_unreadable_section_logs_an_error_not_vanished_containers(tmp_path):
    """A podman hiccup is one error line; containers keep their last known values."""
    # Arrange
    host, state = FakeHost(), tmp_path / "status"
    dw.check(state, _snap(host, tmp_path), now=T0)
    host.fail = "podman"

    # Act
    dw.check(state, _snap(host, tmp_path), now=T1)
    host.fail = None
    dw.check(state, _snap(host, tmp_path), now=T2)

    # Assert
    runs = [json.loads(x) for x in (state / "changes.jsonl").read_text().splitlines()]
    assert [c["key"] for c in runs[1]["changes"]] == ["error.container"]
    assert [c["key"] for c in runs[2]["changes"]] == ["error.container"]
    assert runs[2]["changes"][0]["after"] == "(none)"


def test_report_shows_only_changes_after_the_last_ack(tmp_path):
    """Our own deploys are acknowledged; the report lists only what came after."""
    # Arrange
    host, state = FakeHost(), tmp_path / "status"
    dw.check(state, _snap(host, tmp_path), now=T0)
    host.head = "1111111 our deploy"
    dw.check(state, _snap(host, tmp_path), now=T1)
    dw.ack(state, "PR #273 deployed by us", now=T1)
    host.status += "?? stray_edit.py\n"
    dw.check(state, _snap(host, tmp_path), now=T2)

    # Act
    text = dw.report(state)
    everything = dw.report(state, show_all=True)

    # Assert
    assert "unacknowledged change runs: 1" in text
    assert "checkout.untracked: +stray_edit.py" in text
    assert "our deploy" not in text.split("change runs")[1]
    assert "PR #273 deployed by us" in text
    assert "all change runs: 2" in everything and "baseline" in everything


@pytest.mark.skipif(not POSIX, reason="file modes are POSIX-only")
def test_state_is_readable_by_collaborators_but_not_writable(tmp_path):
    """Collaborators may read what is deployed; only we may write it, whatever the umask."""
    old = os.umask(0o002)
    try:
        state = tmp_path / "status"
        dw.check(state, _snap(FakeHost(), tmp_path), now=T0)
        dw.ack(state, "x", now=T0)
    finally:
        os.umask(old)
    assert oct(state.stat().st_mode & 0o777) == "0o755"
    for f in state.iterdir():
        assert oct(f.stat().st_mode & 0o777) == "0o644", f.name


def test_units_run_the_installed_copy_every_five_minutes():
    """The timer never runs code from the checkout it watches."""
    ops = _SCRIPT.parents[1] / "ops"
    if not ops.is_dir():
        pytest.skip("ops/ not present (image / unbound copy)")
    service = (ops / "stf-v3-deploy-watch.service").read_text()
    timer = (ops / "stf-v3-deploy-watch.timer").read_text()
    installer = (ops / "install_deploy_watch.sh").read_text()
    assert "ExecStart=/usr/bin/python3 @LIB@/deploy_watch.py check" in service
    assert "UMask=0022" in service
    assert "OnCalendar=*:0/5" in timer
    assert '"$HOME/.local/share/stf_deploy_watch"' in installer and "install -d -m 700" in installer

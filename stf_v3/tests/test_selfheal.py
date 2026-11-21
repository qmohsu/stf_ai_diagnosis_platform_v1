"""2026-11-21 outage: the self-heal timer starts killed containers again.

Every host call (podman, the /proc files) is scripted, so the tests need
no server, no containers and no network.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "selfheal.py"
_spec = importlib.util.spec_from_file_location("stf_selfheal", _SCRIPT)
assert _spec and _spec.loader
sh: Any = importlib.util.module_from_spec(_spec)
sys.modules["stf_selfheal"] = sh
_spec.loader.exec_module(sh)

NOW = 1_795_300_000
KILLED_AT = NOW - 600
CALM = {
    "/proc/pressure/memory": "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n"
                             "full avg10=0.00 avg60=0.00 avg300=0.00 total=1\n",
    "/proc/meminfo": "MemTotal: 230000000 kB\nMemAvailable: 85000000 kB\n",
}


class FakeHost:
    """Answers the commands the script runs; tests edit the answers."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        for name in sh.MANAGED:
            pod = "pod-v3" if name.startswith("stf-v3") else "pod-infra"
            self.add(name, "running", pod=pod)
        self.add("stf-ollama", "exited", exit_code=0, pod="pod-infra")
        self.add("stf-obd-ui", "exited", exit_code=0, pod="pod-infra")
        self.calls: List[List[str]] = []
        self.files = dict(CALM)
        self.ps_fails = False
        self.db_accepts = True
        self.start_fails: set = set()

    def add(self, name: str, state: str, exit_code: int = 0,
            exited_at: int = KILLED_AT, pod: str = "") -> None:
        self.rows[name] = {"Names": [name], "State": state,
                           "ExitCode": exit_code, "ExitedAt": exited_at,
                           "Pod": pod}

    def kill(self, *names: str, exit_code: int = 137,
             exited_at: int = KILLED_AT) -> None:
        for name in names:
            self.rows[name].update(State="exited", ExitCode=exit_code,
                                   ExitedAt=exited_at)

    def run(self, cmd: Sequence[str]) -> Tuple[int, str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:2] == ["podman", "ps"]:
            if self.ps_fails:
                return 125, ""
            return 0, json.dumps(list(self.rows.values()))
        if cmd[:2] == ["podman", "start"]:
            name = cmd[2]
            if name in self.start_fails:
                return 125, ""
            self.rows[name]["State"] = "running"
            if name == "stf-diagnostic-api":  # podman starts its dependency
                self.rows["stf-ollama"]["State"] = "running"
            return 0, name
        if cmd[:2] == ["podman", "stop"]:
            self.rows[cmd[2]].update(State="exited", ExitCode=0)
            return 0, cmd[2]
        if cmd[:2] == ["podman", "exec"]:
            return (0 if self.db_accepts else 2), ""
        raise AssertionError(f"unexpected command: {cmd}")

    def read(self, path: str) -> Optional[str]:
        return self.files.get(path)

    def started(self) -> List[str]:
        return [c[2] for c in self.calls if c[:2] == ["podman", "start"]]

    def stopped(self) -> List[str]:
        return [c[2] for c in self.calls if c[:2] == ["podman", "stop"]]


def _pass(host: FakeHost, tmp_path: pathlib.Path, now: int = NOW) -> str:
    return sh.run_pass(tmp_path, run=host.run, read=host.read, now=now,
                       sleep=lambda _s: None)


def _log(tmp_path: pathlib.Path) -> List[dict]:
    path = tmp_path / "selfheal.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_nothing_is_started_when_everything_runs(tmp_path: pathlib.Path) -> None:
    """A healthy server gets no podman start and no log line."""
    host = FakeHost()

    result = _pass(host, tmp_path)

    assert result == "ok"
    assert host.started() == [] and host.stopped() == []
    assert _log(tmp_path) == []


def test_the_outage_is_healed_in_order_and_ollama_stays_off(
    tmp_path: pathlib.Path,
) -> None:
    """The 2026-11-21 state: all six killed.  They start in order, and the
    Ollama dependency that had been stopped on purpose is stopped again."""
    host = FakeHost()
    host.kill("stf-postgres", "stf-diagnostic-api", "stf-nginx",
              "stf-v3-api", "stf-v3-frontend")
    host.kill("stf-v3-worker", exit_code=1)

    result = _pass(host, tmp_path)

    assert result == "healed"
    assert host.started() == list(sh.MANAGED)
    assert host.stopped() == ["stf-ollama"]
    assert host.rows["stf-ollama"]["State"] == "exited"
    assert host.rows["stf-obd-ui"]["State"] == "exited"
    actions = [(a["action"], a["container"]) for a in _log(tmp_path)]
    assert ("stopped_dependency", "stf-ollama") in actions
    assert actions.count(("started", "stf-postgres")) == 1
    # the next quiet pass records that things are back to normal, once
    assert _pass(host, tmp_path, NOW + 120) == "ok"
    assert _pass(host, tmp_path, NOW + 240) == "ok"
    assert [a["action"] for a in _log(tmp_path)].count("back_to_normal") == 1


def test_a_container_stopped_on_purpose_is_left_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Exit code 0 means somebody stopped it: never restarted."""
    host = FakeHost()
    host.kill("stf-v3-frontend", exit_code=0)

    assert _pass(host, tmp_path) == "ok"
    assert host.started() == []


def test_a_fresh_exit_waits_for_the_grace_period(tmp_path: pathlib.Path) -> None:
    """A container that just exited (deploy, podman's own restart) is not
    touched until it has been down for the grace period."""
    host = FakeHost()
    host.kill("stf-v3-api", exited_at=NOW - 60)

    assert _pass(host, tmp_path) == "ok"
    assert host.started() == []
    assert _pass(host, tmp_path, NOW + sh.GRACE_S) == "healed"
    assert host.started() == ["stf-v3-api"]


def test_nothing_is_started_while_memory_is_short(tmp_path: pathlib.Path) -> None:
    """Under memory pressure a start would be killed again: wait, and log
    the wait once, not on every pass."""
    host = FakeHost()
    host.kill("stf-nginx")
    host.files["/proc/pressure/memory"] = (
        "some avg10=60.00 avg60=55.00 avg300=40.00 total=1\n"
        "full avg10=42.50 avg60=40.00 avg300=30.00 total=1\n")

    assert _pass(host, tmp_path) == "waiting_memory"
    assert _pass(host, tmp_path, NOW + 120) == "waiting_memory"
    assert host.started() == []
    assert [a["action"] for a in _log(tmp_path)] == ["waiting_memory"]

    host.files["/proc/pressure/memory"] = CALM["/proc/pressure/memory"]
    host.files["/proc/meminfo"] = "MemAvailable: 2000000 kB\n"
    assert _pass(host, tmp_path, NOW + 240) == "waiting_memory"

    host.files = dict(CALM)
    assert _pass(host, tmp_path, NOW + 360) == "healed"
    assert host.started() == ["stf-nginx"]


def test_a_container_that_keeps_dying_is_capped_per_hour(
    tmp_path: pathlib.Path,
) -> None:
    """At most MAX_STARTS starts an hour for one container."""
    host = FakeHost()
    for i in range(sh.MAX_STARTS):
        host.kill("stf-v3-worker", exit_code=1)
        assert _pass(host, tmp_path, NOW + i * 300) == "healed"
    host.kill("stf-v3-worker", exit_code=1)

    assert _pass(host, tmp_path, NOW + 1200) == "capped"
    assert len(host.started()) == sh.MAX_STARTS
    # an hour after the first start the window has moved on
    assert _pass(host, tmp_path, NOW + sh.WINDOW_S + 1) == "healed"


def test_pause_stops_all_action_until_resume(tmp_path: pathlib.Path) -> None:
    """The pause flag is for maintenance: nothing starts while it is set."""
    host = FakeHost()
    host.kill("stf-v3-api")
    sh.pause(tmp_path, "rollback drill", now=NOW)

    assert _pass(host, tmp_path) == "paused"
    assert host.started() == []
    assert "PAUSED" in sh.status(tmp_path) and "rollback drill" in sh.status(tmp_path)
    assert sh.resume(tmp_path) is True and sh.resume(tmp_path) is False
    assert _pass(host, tmp_path, NOW + 120) == "healed"


def test_database_clients_wait_until_postgres_answers(
    tmp_path: pathlib.Path,
) -> None:
    """When Postgres cannot be started, the containers that need it wait;
    the gateway and the frontend are still brought back."""
    host = FakeHost()
    host.kill(*sh.MANAGED)
    host.start_fails = {"stf-postgres"}

    result = _pass(host, tmp_path)

    assert result == "partial"
    # attempted: Postgres (failed), then only the two that need no database
    assert host.started() == ["stf-postgres", "stf-nginx", "stf-v3-frontend"]
    log = _log(tmp_path)
    assert ("start_failed", "stf-postgres") in [
        (a["action"], a["container"]) for a in log]
    waiting = sorted(a["container"] for a in log if a["action"] == "waiting_db")
    assert waiting == sorted(sh.NEEDS_DB)


def test_postgres_that_never_gets_ready_blocks_its_clients(
    tmp_path: pathlib.Path,
) -> None:
    """Postgres started but still recovering: clients wait for a later pass."""
    host = FakeHost()
    host.kill("stf-postgres", "stf-v3-api")
    host.db_accepts = False

    assert _pass(host, tmp_path) == "partial"
    assert host.started() == ["stf-postgres"]
    assert "db_not_ready" in [a["action"] for a in _log(tmp_path)]


def test_podman_failure_is_recorded_not_raised(tmp_path: pathlib.Path) -> None:
    """Under pressure podman itself fails: record it and try next time."""
    host = FakeHost()
    host.ps_fails = True

    assert _pass(host, tmp_path) == "podman_unavailable"
    assert "podman_unavailable" in sh.status(tmp_path)


def test_a_killed_dependency_is_kept_running(tmp_path: pathlib.Path) -> None:
    """A dependency that was itself killed (non-zero exit) was running
    before the outage, so it stays up after its dependant starts it."""
    host = FakeHost()
    host.kill("stf-diagnostic-api", "stf-ollama")

    assert _pass(host, tmp_path) == "healed"
    assert host.stopped() == []
    assert host.rows["stf-ollama"]["State"] == "running"
    assert ("kept_dependency", "stf-ollama") in [
        (a["action"], a["container"]) for a in _log(tmp_path)]


def test_containers_of_other_pods_are_never_stopped(tmp_path: pathlib.Path) -> None:
    """A container in another pod that happens to start meanwhile (the
    on-demand model) is not ours to stop."""
    host = FakeHost()
    host.add("stf-vllm", "exited", exit_code=0, pod="pod-llm")
    host.kill("stf-nginx")
    real_run = host.run

    def run(cmd: Sequence[str]) -> Tuple[int, str]:
        if list(cmd[:2]) == ["podman", "start"]:
            host.rows["stf-vllm"]["State"] = "running"
        return real_run(cmd)

    result = sh.run_pass(tmp_path, run=run, read=host.read, now=NOW,
                         sleep=lambda _s: None)

    assert result == "healed"
    assert host.stopped() == []


def test_state_files_are_world_readable_not_writable(
    tmp_path: pathlib.Path,
) -> None:
    """The status directory is shared read-only with collaborators (#272):
    files are 644 whatever the caller's umask."""
    import os

    host = FakeHost()
    host.kill("stf-nginx")
    old = os.umask(0o002)
    try:
        _pass(host, tmp_path)
    finally:
        os.umask(old)

    if os.name == "posix":
        for name in ("selfheal.json", "selfheal.jsonl"):
            assert (tmp_path / name).stat().st_mode & 0o777 == 0o644

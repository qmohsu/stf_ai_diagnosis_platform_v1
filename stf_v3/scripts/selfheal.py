#!/usr/bin/env python3
"""Starts the STF containers again after they were killed (2026-11-21).

On 2026-11-21 the shared server ran short of memory and ``systemd-oomd``
killed everything in our user slice.  ``cloudflared`` and the host GPU
worker came back on their own (systemd restarts them); the containers did
not, and the site stayed down for 22 hours.  A host timer now runs one pass
of this script every two minutes:

    python3 selfheal.py run                       # timer: one pass
    python3 selfheal.py status                    # last pass + recent actions
    python3 selfheal.py pause --note "rollback"   # stop acting (maintenance)
    python3 selfheal.py resume

What one pass does:

* Only the six containers in ``MANAGED`` are ever started, in that order,
  and only with ``podman start`` (same container, same image).
* A container is started when it has been ``exited`` for ``GRACE_S`` with a
  **non-zero** exit code (killed or crashed).  Exit code 0 means somebody
  stopped it on purpose: it is left alone.  A deploy removes containers
  instead of leaving them exited, so a deploy is never disturbed.
* Nothing is started while memory is still short (it would be killed
  again), and a container is started at most ``MAX_STARTS`` times an hour.
* Containers that need the database wait until Postgres accepts
  connections.
* Starting ``stf-diagnostic-api`` also starts its dependency
  ``stf-ollama``, whose warm-up would load a model onto both GPUs and block
  the on-demand vLLM.  A container of the same pod that was started as a
  side effect and had been stopped on purpose (exit code 0) is stopped
  again straight away.

The timer runs an installed copy (``stf_v3/ops/install_selfheal.sh``).
State lives in ``~/stf_v3_status`` (``selfheal.json``, ``selfheal.jsonl``,
``selfheal.pause``): container names and states only, nothing secret.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]
Reader = Callable[[str], Optional[str]]
Containers = Dict[str, Dict[str, Any]]

# Start order.  Nothing outside this list is ever started.
MANAGED = (
    "stf-postgres",
    "stf-diagnostic-api",
    "stf-nginx",
    "stf-v3-api",
    "stf-v3-worker",
    "stf-v3-frontend",
)
NEEDS_DB = frozenset({"stf-diagnostic-api", "stf-v3-api", "stf-v3-worker"})
DB_CONTAINER = "stf-postgres"
GRACE_S = 180
MAX_STARTS = 3
WINDOW_S = 3600
PRESSURE_FULL_AVG10_MAX = 10.0
MIN_AVAILABLE_KB = 8 * 1024 * 1024
DB_READY_TRIES = 12
DB_READY_PAUSE_S = 5
RECENT_ACTIONS = 15


class CollectError(Exception):
    """The container list could not be read this time."""


def _run(cmd: Sequence[str]) -> Tuple[int, str]:
    """Runs a host command; (exit code, stdout).  Never raises."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        p = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=120, env=env
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


def _read(path: str) -> Optional[str]:
    """Reads a small host file; None when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def list_containers(run: Runner) -> Containers:
    """Reads every container's state from podman.

    Args:
        run: Host command runner.

    Returns:
        Name -> {"running", "exited", "exit_code", "exited_at", "pod"}.

    Raises:
        CollectError: If podman does not answer or its output is not JSON.
    """
    rc, out = run(["podman", "ps", "-a", "--format", "json"])
    if rc != 0:
        raise CollectError("podman ps failed")
    try:
        rows = json.loads(out or "[]")
    except ValueError as exc:
        raise CollectError("podman ps output is not JSON") from exc
    found: Containers = {}
    for row in rows:
        state = str(row.get("State", ""))
        for name in row.get("Names") or []:
            found[name] = {
                "running": state == "running",
                "exited": state in ("exited", "stopped"),
                "exit_code": int(row.get("ExitCode") or 0),
                "exited_at": int(row.get("ExitedAt") or 0),
                "pod": str(row.get("Pod") or ""),
            }
    return found


def memory_ok(read: Reader) -> Tuple[bool, str]:
    """Says whether it is safe to start containers now.

    Args:
        read: Host file reader.

    Returns:
        (True, "") or (False, reason).  Unreadable files do not block.
    """
    pressure = read("/proc/pressure/memory") or ""
    for line in pressure.splitlines():
        if line.startswith("full "):
            for field in line.split():
                if field.startswith("avg10="):
                    try:
                        value = float(field[6:])
                    except ValueError:
                        continue
                    if value >= PRESSURE_FULL_AVG10_MAX:
                        return False, f"memory pressure full avg10={value}"
    for line in (read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            try:
                kb = int(line.split()[1])
            except (IndexError, ValueError):
                continue
            if kb < MIN_AVAILABLE_KB:
                return False, f"only {kb // 1048576} GiB memory available"
    return True, ""


def due(
    containers: Containers,
    starts: Dict[str, List[int]],
    now: int,
) -> Tuple[List[str], List[str]]:
    """Picks the managed containers to start in this pass.

    Args:
        containers: Output of ``list_containers``.
        starts: Name -> epoch seconds of earlier starts by this script.
        now: Current epoch seconds.

    Returns:
        (names to start in order, names skipped because of the hourly cap).
    """
    todo: List[str] = []
    capped: List[str] = []
    for name in MANAGED:
        c = containers.get(name)
        if not c or not c["exited"] or c["exit_code"] == 0:
            continue
        if now - c["exited_at"] < GRACE_S:
            continue
        recent = [t for t in starts.get(name, []) if now - t < WINDOW_S]
        if len(recent) >= MAX_STARTS:
            capped.append(name)
            continue
        todo.append(name)
    return todo, capped


def _db_ready(run: Runner, sleep: Callable[[float], None], tries: int) -> bool:
    """Waits until Postgres accepts connections (``tries`` checks)."""
    for i in range(tries):
        rc, _ = run(["podman", "exec", DB_CONTAINER, "pg_isready", "-q"])
        if rc == 0:
            return True
        if i + 1 < tries:
            sleep(DB_READY_PAUSE_S)
    return False


def _now_iso(now: int) -> str:
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(
        timespec="seconds"
    )


def _write_json(path: Path, obj: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o644)  # the status directory is world-readable by design
    os.replace(tmp, path)


def _append(path: Path, obj: object) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.chmod(path, 0o644)


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def run_pass(
    state_dir: Path,
    run: Runner = _run,
    read: Reader = _read,
    now: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Runs one self-heal pass.

    Args:
        state_dir: Directory holding the state files.
        run: Host command runner (scripted in tests).
        read: Host file reader (scripted in tests).
        now: Current epoch seconds (tests pass a fixed value).
        sleep: Sleep function (tests pass a no-op).

    Returns:
        The pass result: ``ok``, ``paused``, ``podman_unavailable``,
        ``waiting_memory``, ``healed``, ``partial`` or ``capped``.
    """
    now = int(time.time()) if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "selfheal.json"
    log_path = state_dir / "selfheal.jsonl"
    state = _load(state_path)
    starts: Dict[str, List[int]] = {
        k: [t for t in v if now - t < WINDOW_S]
        for k, v in (state.get("starts") or {}).items()
    }
    actions: List[dict] = []

    def act(action: str, **fields: object) -> None:
        actions.append({"at": _now_iso(now), "action": action, **fields})

    def finish(result: str, detail: str = "") -> str:
        if result != state.get("result") and not actions and result != "ok":
            act(result, detail=detail)
        if result == "ok" and state.get("result") not in (None, "ok"):
            act("back_to_normal", was=state.get("result"))
        for a in actions:
            _append(log_path, a)
        _write_json(state_path, {"at": _now_iso(now), "result": result,
                                 "detail": detail, "starts": starts})
        return result

    if (state_dir / "selfheal.pause").exists():
        return finish("paused")
    try:
        containers = list_containers(run)
    except CollectError as exc:
        return finish("podman_unavailable", str(exc))
    todo, capped = due(containers, starts, now)
    if not todo:
        if capped:
            return finish("capped", "hourly start cap reached: "
                          + ", ".join(capped))
        return finish("ok")
    ok, why = memory_ok(read)
    if not ok:
        return finish("waiting_memory", why)

    db = containers.get(DB_CONTAINER)
    db_ready: Optional[bool] = None
    failed = False
    for name in todo:
        if name in NEEDS_DB:
            if db_ready is None:
                db_ready = bool(db and db["running"]) and _db_ready(
                    run, sleep, 1)
            if not db_ready:
                act("waiting_db", container=name)
                failed = True
                continue
        before = containers
        rc, _ = run(["podman", "start", name])
        starts.setdefault(name, []).append(now)
        act("started" if rc == 0 else "start_failed", container=name,
            exit_code_before=before[name]["exit_code"],
            down_for_s=now - before[name]["exited_at"])
        failed = failed or rc != 0
        try:
            containers = list_containers(run)
        except CollectError:
            containers = before
            continue
        for other, cur in sorted(containers.items()):
            was = before.get(other)
            if (other in MANAGED or not cur["running"] or not was
                    or was["running"] or cur["pod"] != before[name]["pod"]):
                continue
            if was["exit_code"] == 0:
                run(["podman", "stop", other])
                act("stopped_dependency", container=other, started_by=name)
            else:
                act("kept_dependency", container=other, started_by=name)
        if name == DB_CONTAINER and rc == 0:
            db = containers.get(DB_CONTAINER)
            db_ready = _db_ready(run, sleep, DB_READY_TRIES)
            if not db_ready:
                act("db_not_ready", container=name)
    return finish("partial" if failed else "healed")


def pause(state_dir: Path, note: str, now: Optional[int] = None) -> None:
    """Stops the script from acting until ``resume``."""
    now = int(time.time()) if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(state_dir / "selfheal.pause",
                {"at": _now_iso(now), "note": note})


def resume(state_dir: Path) -> bool:
    """Lets the script act again; False when it was not paused."""
    try:
        (state_dir / "selfheal.pause").unlink()
    except FileNotFoundError:
        return False
    return True


def status(state_dir: Path) -> str:
    """Describes the last pass, the pause flag and recent actions."""
    state = _load(state_dir / "selfheal.json")
    lines = []
    if not state:
        lines.append("self-heal has not run yet")
    else:
        detail = f" ({state['detail']})" if state.get("detail") else ""
        lines.append(
            f"last pass {state.get('at')}: {state.get('result')}{detail}")
    paused = _load(state_dir / "selfheal.pause")
    if (state_dir / "selfheal.pause").exists():
        lines.append(f"PAUSED since {paused.get('at', '?')}: "
                     f"{paused.get('note', '')}")
    try:
        rows = (state_dir / "selfheal.jsonl").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        rows = []
    for raw in rows[-RECENT_ACTIONS:]:
        try:
            a = json.loads(raw)
        except ValueError:
            continue
        extra = " ".join(f"{k}={v}" for k, v in a.items()
                         if k not in ("at", "action"))
        lines.append(f"{a.get('at')}  {a.get('action')}  {extra}".rstrip())
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("status")
    sp = sub.add_parser("pause")
    sp.add_argument("--note", required=True)
    sub.add_parser("resume")
    args = p.parse_args(argv)
    state_dir = Path(os.environ.get("STF_SELFHEAL_DIR")
                     or Path.home() / "stf_v3_status")
    if args.cmd == "run":
        print(run_pass(state_dir))
    elif args.cmd == "status":
        print(status(state_dir))
    elif args.cmd == "pause":
        pause(state_dir, args.note)
        print("paused")
    else:
        print("resumed" if resume(state_dir) else "was not paused")
    return 0


if __name__ == "__main__":
    sys.exit(main())

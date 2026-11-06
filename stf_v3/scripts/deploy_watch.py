#!/usr/bin/env python3
"""Records every change to what the PolyU server runs (#272).

Collaborators now have read-only access to the deploy checkout, so a host
timer keeps a log of anything that changes what runs, whoever did it: the
checkout (branch, commit, edited or new files), the ``stf-*`` containers
(image, commit label, creation time, state), the host worker units and their
unit files, the live API contracts (V3 and V1/V2) and both schema versions.

    python3 deploy_watch.py check                    # timer: compare + log
    python3 deploy_watch.py report [--all]           # not yet acknowledged
    python3 deploy_watch.py ack --note "PR #273 deployed"

The timer runs an installed copy (``stf_v3/ops/install_deploy_watch.sh``),
never the checkout it watches.  State lives in ``~/stf_v3_status``
(``current.json``, ``changes.jsonl``, ``acks.jsonl``): world-readable on
purpose so collaborators can see what is deployed, never group/other
writable, and nothing secret goes in (no env, no logs, no data).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]
Fetcher = Callable[[str], Optional[bytes]]

# Started / stopped on demand by the model controller (PROD-11): its comings
# and goings are routine, not deploys.
VOLATILE_CONTAINERS = {"stf-vllm"}
WATCHED_UNITS = ("stf-v3-gpu-worker", "marker-worker")
UNIT_FILE_PREFIXES = ("stf-", "marker-worker")
CONTRACTS = {
    "api.v3_openapi": "http://127.0.0.1:8002/v3/openapi.json",
    "api.v12_openapi": "http://127.0.0.1:8001/openapi.json",
}
SCHEMAS = {"db.stf_v3_alembic": "stf_v3", "db.stf_diagnosis_alembic": "stf_diagnosis"}
LIST_CAP = 30
SECTIONS = ("checkout", "container", "unit", "api", "db")


class CollectError(Exception):
    """One section of the snapshot could not be read this time."""


def _run(cmd: Sequence[str]) -> Tuple[int, str]:
    """Runs a host command; (exit code, stdout).  Never raises."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=60, env=env)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


def _fetch(url: str) -> Optional[bytes]:
    """GETs a local URL; None when it does not answer 200."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read() if r.status == 200 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _short_list(items: List[str]) -> str:
    """Sorted, comma-joined, capped so one noisy directory stays readable."""
    items = sorted(items)
    if len(items) > LIST_CAP:
        return ", ".join(items[:LIST_CAP]) + f", … +{len(items) - LIST_CAP} more"
    return ", ".join(items)


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def collect_checkout(repo: Path, run: Runner) -> Dict[str, str]:
    """Branch, commit and uncommitted files of the deploy checkout."""
    rc_b, branch = run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    rc_h, head = run(["git", "-C", str(repo), "log", "-1", "--format=%h %s"])
    rc_s, status = run(["git", "-C", str(repo), "status", "--porcelain=v1"])
    if rc_b or rc_h or rc_s:
        raise CollectError("git failed")
    changed, untracked = [], []
    for line in status.splitlines():
        code, path = line[:2], line[3:]
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if code == "??":
            untracked.append(path)
        else:
            changed.append(f"{code.strip()} {path}")
    return {"checkout.branch": branch.strip(), "checkout.head": head.strip()[:80],
            "checkout.changed": _short_list(changed),
            "checkout.untracked": _short_list(untracked)}


def collect_containers(run: Runner) -> Dict[str, str]:
    """Image, commit label, creation time and state of every stf-* container."""
    rc, names = run(["podman", "ps", "-a", "--format", "{{.Names}}"])
    if rc:
        raise CollectError("podman ps failed")
    out = {}
    fmt = ('{{.Image}}|{{index .Config.Labels "org.opencontainers.image.revision"}}'
           "|{{.Created}}|{{.State.Status}}")
    for name in sorted(n.strip() for n in names.splitlines()):
        if not name.startswith("stf-") or name in VOLATILE_CONTAINERS:
            continue
        rc, info = run(["podman", "inspect", "-f", fmt, name])
        if rc:
            continue                    # removed between ps and inspect
        image, commit, created, state = (info.strip().split("|") + ["", "", "", ""])[:4]
        if commit in ("", "<no value>"):
            commit = "-"
        out[f"container.{name}"] = (f"image={image[:12]} commit={commit[:12]} "
                                    f"created={created[:19]} state={state}")
    return out


def collect_units(unit_dir: Path, run: Runner) -> Dict[str, str]:
    """Host worker states (a restart shows as a new start time) + unit file hashes."""
    out = {}
    for unit in WATCHED_UNITS:
        rc, text = run(["systemctl", "--user", "show", unit,
                        "-p", "ActiveState", "-p", "ActiveEnterTimestamp"])
        if rc:
            raise CollectError("systemctl failed")
        props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        state = props.get("ActiveState", "?")
        since = props.get("ActiveEnterTimestamp", "")
        out[f"unit.{unit}"] = f"{state} since {since}" if since else state
    files = {}
    if unit_dir.is_dir():
        for entry in sorted(unit_dir.iterdir()):
            if not entry.name.startswith(UNIT_FILE_PREFIXES):
                continue
            paths = sorted(entry.rglob("*")) if entry.is_dir() else [entry]
            data = b"".join(p.name.encode() + p.read_bytes() for p in paths if p.is_file())
            files[f"unitfile.{entry.name}"] = _sha12(data)
    out.update(files)
    return out


def collect_contracts(fetch: Fetcher) -> Dict[str, str]:
    """Hash + path count of each live OpenAPI document (an interface change shows here)."""
    out = {}
    for key, url in CONTRACTS.items():
        body = fetch(url)
        if body is None:
            out[key] = "unreachable"
            continue
        try:
            paths = len(json.loads(body).get("paths", {}))
        except (ValueError, AttributeError):
            paths = -1
        out[key] = f"{_sha12(body)} ({paths} paths)"
    return out


def collect_schemas(run: Runner) -> Dict[str, str]:
    """Alembic head of each database, read through the container socket."""
    out = {}
    for key, db in SCHEMAS.items():
        rc, text = run(["podman", "exec", "stf-postgres", "sh", "-c",
                        f'psql -U "$POSTGRES_USER" -d {db} -tAc '
                        '"SELECT version_num FROM alembic_version"'])
        if rc:
            raise CollectError("psql failed")
        out[key] = text.strip() or "(none)"
    return out


def collect(repo: Path, unit_dir: Path, run: Runner = _run,
            fetch: Fetcher = _fetch) -> Dict[str, str]:
    """The whole snapshot; a section that cannot be read becomes ``error.<section>``."""
    snap: Dict[str, str] = {}
    parts: List[Tuple[str, Callable[[], Dict[str, str]]]] = [
        ("checkout", lambda: collect_checkout(repo, run)),
        ("container", lambda: collect_containers(run)),
        ("unit", lambda: collect_units(unit_dir, run)),
        ("api", lambda: collect_contracts(fetch)),
        ("db", lambda: collect_schemas(run)),
    ]
    for section, read in parts:
        try:
            snap.update(read())
        except (CollectError, OSError) as e:
            snap[f"error.{section}"] = str(e) or type(e).__name__
    return snap


def merge_failed(prev: Dict[str, str], cur: Dict[str, str]) -> Dict[str, str]:
    """Keeps last known values of a section that failed to read, so a podman
    hiccup is one ``error.*`` line instead of "every container vanished"."""
    merged = dict(cur)
    for section in SECTIONS:
        if f"error.{section}" not in cur:
            continue
        prefixes = (f"{section}.", "unitfile.") if section == "unit" else (f"{section}.",)
        for k, v in prev.items():
            if k.startswith(prefixes) and k not in merged:
                merged[k] = v
    return merged


def diff(prev: Dict[str, str], cur: Dict[str, str]) -> List[Dict[str, str]]:
    """One entry per key whose value changed, appeared or disappeared."""
    return [{"key": k, "before": prev.get(k, "(none)"), "after": cur.get(k, "(none)")}
            for k in sorted(set(prev) | set(cur)) if prev.get(k) != cur.get(k)]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _ensure_dir(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o755)


def _write_json(path: Path, obj: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _append(path: Path, obj: object) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.chmod(path, 0o644)


def _read_lines(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def check(state_dir: Path, snap: Dict[str, str], now: Optional[str] = None) -> int:
    """Compares with the last snapshot and logs the difference; number of changes."""
    now = now or _now()
    _ensure_dir(state_dir)
    current = state_dir / "current.json"
    prev = None
    if current.exists():
        try:
            prev = json.loads(current.read_text(encoding="utf-8")).get("snapshot")
        except ValueError:
            prev = None
    if prev is None:
        _append(state_dir / "changes.jsonl", {"at": now, "baseline": True, "keys": len(snap)})
        changes = []
    else:
        snap = merge_failed(prev, snap)
        changes = diff(prev, snap)
        if changes:
            _append(state_dir / "changes.jsonl", {"at": now, "changes": changes})
    _write_json(current, {"at": now, "snapshot": snap})
    return len(changes)


def ack(state_dir: Path, note: str, now: Optional[str] = None) -> None:
    """Marks everything logged so far as seen (e.g. after our own deploy)."""
    _ensure_dir(state_dir)
    _append(state_dir / "acks.jsonl", {"at": now or _now(), "note": note})


def _local(ts: str) -> str:
    return dt.datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _items(value: str) -> List[str]:
    if value in ("", "(none)"):
        return []
    return [x for x in value.split(", ") if not x.startswith("… +")]


def _describe(c: Dict[str, str]) -> str:
    """File lists as +added / -removed; everything else as before -> after."""
    if c["key"] in ("checkout.changed", "checkout.untracked"):
        before, after = set(_items(c["before"])), set(_items(c["after"]))
        parts = ([f"+{x}" for x in sorted(after - before)]
                 + [f"-{x}" for x in sorted(before - after)])
        if parts:
            return f"{c['key']}: {', '.join(parts)}"
    return f"{c['key']}: {c['before']} -> {c['after']}"


def report(state_dir: Path, show_all: bool = False) -> str:
    """Human-readable changes since the last ack (or all of them)."""
    acks = _read_lines(state_dir / "acks.jsonl")
    last_ack = acks[-1] if acks else None
    runs = _read_lines(state_dir / "changes.jsonl")
    if not show_all and last_ack:
        runs = [r for r in runs if r.get("at", "") > last_ack["at"]]
    lines = []
    try:
        cur = json.loads((state_dir / "current.json").read_text(encoding="utf-8"))
        s = cur["snapshot"]
        lines.append(f"last check {_local(cur['at'])}: checkout {s.get('checkout.branch')} @ "
                     f"{s.get('checkout.head')}")
    except (OSError, ValueError, KeyError):
        lines.append("no check has run yet")
    lines.append(f"last ack: {_local(last_ack['at'])} — {last_ack['note']}" if last_ack
                 else "last ack: never")
    changed = [r for r in runs if r.get("changes")]
    lines.append(f"{'all' if show_all else 'unacknowledged'} change runs: {len(changed)}")
    for r in runs:
        if r.get("baseline"):
            lines.append(f"{_local(r['at'])}  baseline ({r.get('keys')} keys)")
            continue
        for i, c in enumerate(r.get("changes", [])):
            when = _local(r["at"]) if i == 0 else " " * len(_local(r["at"]))
            lines.append(f"{when}  {_describe(c)}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; see the module docstring."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("report")
    r.add_argument("--all", action="store_true")
    a = sub.add_parser("ack")
    a.add_argument("--note", required=True)
    args = p.parse_args(argv)
    home = Path.home()
    repo = Path(os.environ.get("STF_V3_REPO", home / "stf_ai_diagnosis_platform_v1"))
    state_dir = Path(os.environ.get("STF_DEPLOY_WATCH_DIR", home / "stf_v3_status"))
    if args.cmd == "check":
        n = check(state_dir, collect(repo, home / ".config" / "systemd" / "user"))
        print(f"deploy watch: {n} change(s)")
    elif args.cmd == "report":
        print(report(state_dir, show_all=args.all))
    else:
        ack(state_dir, args.note)
        print("acknowledged")
    return 0


if __name__ == "__main__":
    sys.exit(main())

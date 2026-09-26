#!/usr/bin/env python3
"""GPU contention record for one golden eval run (#253).

Started by ``run_golden_eval.sh`` as its own transient user service, it
follows the eval container: every ``--interval`` seconds it samples each
GPU (memory, utilisation, per-process memory and compute) and sorts the
processes into four groups — our vLLM, our Ollama, other processes of the
V3 services (MinerU under the host GPU worker, …), and other workloads:
other users, AND this account's processes outside the V3 containers /
services (2026-09-27: the Gemma competition's own vLLM ran on the same
account and would otherwise have passed for ours).  When the container is gone it
writes ``<scorecard base>.contention.json`` into the run directory (or
``contention.json`` when no scorecard was written), with the vLLM
preemption count before / after.

Privacy (FM-18 / FM-19): user names, process ids and command lines are
used in memory to classify a process and never written; the record holds
numbers, the run name, the scorecard name and UTC times only.

Robustness (FM-2 / FM-10 / FM-11): every external call has a timeout; a
failed sample is counted and skipped; a vLLM restart (preemption counter
going backwards) is flagged; SIGTERM still writes the record.

Stdlib only (runs with the host's python3).

    python3 stf_v3/scripts/gpu_contention.py sample --container NAME --run-dir DIR \\
        [--interval 30] [--concurrency 6] [--metrics-url http://127.0.0.1:8010/metrics]

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

SCHEMA = 1
SUFFIX = ".contention.json"
UNACCOUNTED_NOISE_MIB = 512      # driver memory with no process on the card
CALL_TIMEOUT_S = 10.0

Runner = Callable[[Sequence[str], float], Optional[str]]


def run_cmd(argv: Sequence[str], timeout: float = CALL_TIMEOUT_S) -> Optional[str]:
    """Stdout of a command, or None on error / timeout (never raises)."""
    try:
        res = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def _num(text: str) -> float:
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        return 0.0                  # "-", "[N/A]" …


def proc_owner_uid(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except OSError:
        return ""


def proc_cgroup(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


V3_CGROUP_MARKERS = ("libpod-", "stf-v3-gpu-worker.service")
"""A process of this account belongs to V3 only inside a Podman container
(stf-vllm, stf-ollama, stf-v3-*) or the host GPU worker service (MinerU)."""


def classify(pid: int, my_uid: int, owner: Callable[[int], Optional[int]] = proc_owner_uid,
             cmdline: Callable[[int], str] = proc_cmdline, cgroup: Callable[[int], str] = proc_cgroup) -> str:
    """``vllm`` / ``ollama`` / ``project_other`` / ``others``.

    Unknown owner, another user, or this account outside the V3 containers
    and services → ``others`` (FM-4).
    """
    uid = owner(pid)
    if uid is None or uid != my_uid:
        return "others"
    if not any(m in cgroup(pid) for m in V3_CGROUP_MARKERS):
        return "others"
    cmd = cmdline(pid)
    if "vllm" in cmd:
        return "vllm"
    if "ollama" in cmd:
        return "ollama"
    return "project_other"


def take_sample(run: Runner, my_uid: int, classify_fn: Callable[[int], str]) -> Optional[List[Dict[str, float]]]:
    """One reading per GPU, or None when the GPU query failed."""
    gpus_out = run(["nvidia-smi", "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits"], CALL_TIMEOUT_S)
    if gpus_out is None:
        return None
    apps_out = run(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory",
                    "--format=csv,noheader,nounits"], CALL_TIMEOUT_S) or ""
    pmon_out = run(["nvidia-smi", "pmon", "-c", "1", "-s", "u"], CALL_TIMEOUT_S) or ""
    gpus: Dict[str, Dict[str, float]] = {}
    for line in gpus_out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        gpus[parts[1]] = {"index": int(_num(parts[0])), "used": _num(parts[2]), "total_mib": _num(parts[3]),
                          "util": _num(parts[4]), "vllm": 0.0, "ollama": 0.0, "project_other": 0.0,
                          "others": 0.0, "others_sm": 0.0, "listed": 0.0}
    by_index = {int(g["index"]): g for g in gpus.values()}
    kinds: Dict[int, str] = {}
    for line in apps_out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or parts[0] not in gpus:
            continue
        pid = int(_num(parts[1]))
        kind = kinds.setdefault(pid, classify_fn(pid))
        g = gpus[parts[0]]
        g[kind] += _num(parts[2])
        g["listed"] += _num(parts[2])
    for line in pmon_out.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        gidx, pid = int(_num(parts[0])), int(_num(parts[1]))
        if gidx in by_index and pid and kinds.get(pid, classify_fn(pid)) == "others":
            by_index[gidx]["others_sm"] += _num(parts[3])
    for g in gpus.values():
        extra = g["used"] - g["listed"]
        if extra >= UNACCOUNTED_NOISE_MIB:
            g["others"] += extra           # memory no listed process accounts for
    return sorted(gpus.values(), key=lambda g: g["index"])


def preemptions(url: str, timeout: float = CALL_TIMEOUT_S) -> Optional[float]:
    """Sum of ``vllm:num_preemptions_total`` from the metrics page, or None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    total, seen = 0.0, False
    for line in text.splitlines():
        if line.startswith("vllm:num_preemptions_total"):
            total += _num(line.rsplit(" ", 1)[-1])
            seen = True
    return total if seen else None


def summarise(samples: List[List[Dict[str, float]]]) -> List[Dict[str, float]]:
    """Per GPU: max / mean of the readings (whitelisted keys only)."""
    per: Dict[int, List[Dict[str, float]]] = {}
    for sample in samples:
        for g in sample:
            per.setdefault(int(g["index"]), []).append(g)
    out = []
    for idx in sorted(per):
        rows = per[idx]

        def col(key: str) -> List[float]:
            return [float(r[key]) for r in rows]

        out.append({
            "index": idx, "total_mib": max(col("total_mib")),
            "others_mib_max": max(col("others")), "others_mib_mean": round(statistics.mean(col("others")), 1),
            "others_sm_pct_max": max(col("others_sm")),
            "others_sm_pct_mean": round(statistics.mean(col("others_sm")), 1),
            "vllm_mib_max": max(col("vllm")), "project_other_mib_max": max(col("project_other")),
            "ollama_mib_max": max(col("ollama")),
            "util_pct_max": max(col("util")), "util_pct_mean": round(statistics.mean(col("util")), 1),
        })
    return out


def scorecard_base(run_dir: Path) -> Optional[str]:
    """The base name of the scorecard the eval wrote into ``run_dir``."""
    for p in sorted(run_dir.glob("*.slim.json")):
        return p.name[: -len(".slim.json")]
    for p in sorted(run_dir.glob("*.json")):
        if not p.name.endswith((".partial.json", SUFFIX)) and p.name != "contention.json":
            return p.name[: -len(".json")]
    return None


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(run: str, base: Optional[str], started: float, ended: float, interval: float,
                 samples: List[List[Dict[str, float]]], errors: int, concurrency: Optional[int],
                 before: Optional[float], after: Optional[float]) -> Dict[str, object]:
    delta = None if before is None or after is None else after - before
    return {
        "schema": SCHEMA, "kind": "gpu_contention", "run": run, "scorecard": base,
        "started_at": _utc(started), "ended_at": _utc(ended), "interval_s": interval,
        "samples": len(samples), "sample_errors": errors, "concurrency": concurrency,
        "preemptions_before": before, "preemptions_after": after,
        "preemption_delta": delta if delta is None or delta >= 0 else None,
        "vllm_restarted": bool(delta is not None and delta < 0),
        "gpus": summarise(samples),
    }


def write_record(run_dir: Path, record: Dict[str, object]) -> Path:
    base = record.get("scorecard")
    path = run_dir / (f"{base}{SUFFIX}" if base else "contention.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def container_alive(name: str, run: Runner) -> bool:
    return run(["podman", "container", "exists", name], CALL_TIMEOUT_S) is not None


def sample_run(container: str, run_dir: Path, *, interval: float, concurrency: Optional[int],
               metrics_url: str, max_s: float, run: Runner = run_cmd,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
               my_uid: Optional[int] = None, classify_fn: Optional[Callable[[int], str]] = None,
               metrics: Callable[[str], Optional[float]] = preemptions) -> Path:
    """Follow ``container`` until it ends (or ``max_s``) and write the record."""
    uid = os.getuid() if my_uid is None and hasattr(os, "getuid") else (my_uid or 0)
    cls = classify_fn or (lambda pid: classify(pid, uid))
    started = clock()
    before = metrics(metrics_url)
    samples: List[List[Dict[str, float]]] = []
    errors = 0
    stop = {"now": False}

    def _term(signum: int, frame: object) -> None:   # FM-2: still write on SIGTERM
        stop["now"] = True

    try:
        signal.signal(signal.SIGTERM, _term)
    except ValueError:
        pass                                          # not the main thread (tests)
    try:
        while not stop["now"] and clock() - started < max_s:
            s = take_sample(run, uid, cls)
            if s is None:
                errors += 1
            else:
                samples.append(s)
            if not container_alive(container, run):
                break
            sleep(interval)
    finally:
        record = build_record(container, scorecard_base(run_dir), started, clock(), interval, samples,
                              errors, concurrency, before, metrics(metrics_url))
        path = write_record(run_dir, record)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--container", required=True)
    s.add_argument("--run-dir", required=True)
    s.add_argument("--interval", type=float, default=30.0)
    s.add_argument("--concurrency", type=int, default=None)
    s.add_argument("--metrics-url", default="http://127.0.0.1:8010/metrics")
    s.add_argument("--max-hours", type=float, default=6.0)
    a = p.parse_args(argv)
    path = sample_run(a.container, Path(a.run_dir), interval=a.interval, concurrency=a.concurrency,
                      metrics_url=a.metrics_url, max_s=a.max_hours * 3600)
    print(f"[gpu_contention] wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

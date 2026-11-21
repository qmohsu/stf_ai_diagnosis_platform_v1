#!/usr/bin/env python3
"""Checks the public site from outside and words the alert (2026-11-21).

On 2026-11-21 the site was down for 22 hours and nobody was told.  The
``uptime`` GitHub workflow runs this probe every 15 minutes from GitHub's
runners (outside the server, so it also notices a dead server or tunnel)
and keeps ONE open issue while something is wrong; GitHub then mails the
maintainer.  The workflow closes the issue when the probe is clean again.

    python3 uptime_probe.py --base-url https://example.org \\
        --body-file alert.md [--fail-on-problem]

A problem is reported only when every attempt saw it, so a deploy or a
short blip does not raise an alert.  What is checked:

* ``site``: the login page answers 200 (frontend + gateway + tunnel).
* ``api``: ``/v3/health`` answers 200 with ``status`` and ``db`` ok.
* ``gpu_worker``: the host GPU worker has a heartbeat.
* ``controller``: the on-demand model controller ran in the last 15 min.
* ``backup``: the last successful backup is younger than 36 hours.

The alert text never quotes the health document, only the lines below.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Fetched = Tuple[Optional[int], Optional[bytes]]
Fetcher = Callable[[str], Fetched]

USER_AGENT = "Mozilla/5.0 (compatible; stf-uptime-probe)"
CONTROLLER_STALE_S = 900
DOWN_KEYS = ("site", "api")
HKT = dt.timezone(dt.timedelta(hours=8))
TITLE_DOWN = "【自动告警】线上网站无法访问"
TITLE_DEGRADED = "【自动告警】线上服务异常"


def fetch(url: str) -> Fetched:
    """GETs a URL; (status, body), or (None, None) when nothing answered."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def _http(status: Optional[int]) -> str:
    return "没有响应（超时或连接失败）" if status is None else f"HTTP {status}"


def evaluate(login: Fetched, health: Fetched) -> Dict[str, str]:
    """Turns one pair of answers into problems.

    Args:
        login: Answer of the login page.
        health: Answer of ``/v3/health``.

    Returns:
        Problem key -> one sentence for the alert; empty when all is well.
    """
    problems: Dict[str, str] = {}
    if login[0] != 200:
        problems["site"] = f"网站打不开：登录页{_http(login[0])}"
    doc: Optional[dict] = None
    if health[0] == 200 and health[1]:
        try:
            parsed = json.loads(health[1])
        except ValueError:
            parsed = None
        doc = parsed if isinstance(parsed, dict) else None
    if doc is None or doc.get("status") != "ok" or doc.get("db") != "ok":
        problems["api"] = f"后端健康检查失败：{_http(health[0])}"
        return problems
    worker = doc.get("gpu_worker") or {}
    if worker.get("alive") is not True:
        problems["gpu_worker"] = (
            "宿主机 GPU 工人没有心跳：手册转换和按需启动模型都会停")
    seen = (doc.get("model_service") or {}).get("controller_seen_s")
    if isinstance(seen, (int, float)) and seen > CONTROLLER_STALE_S:
        problems["controller"] = (
            f"按需模型控制器已 {int(seen // 60)} 分钟没有运行："
            "新发起的诊断会一直等模型")
    if (doc.get("backup") or {}).get("state") == "stale":
        problems["backup"] = "最近一次成功备份已超过 36 小时"
    return problems


def probe(
    base_url: str,
    fetcher: Fetcher = fetch,
    attempts: int = 4,
    pause_s: float = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, str]:
    """Probes several times; keeps the problems every attempt saw.

    Args:
        base_url: Site origin without a trailing slash.
        fetcher: URL fetcher (scripted in tests).
        attempts: Number of attempts.
        pause_s: Seconds between attempts.
        sleep: Sleep function (tests pass a no-op).

    Returns:
        Problem key -> sentence from the last attempt; empty when any
        attempt was clean for that key.
    """
    lasting: Optional[Dict[str, str]] = None
    for i in range(attempts):
        found = evaluate(fetcher(base_url + "/login"),
                         fetcher(base_url + "/v3/health"))
        if lasting is None:
            lasting = found
        else:
            lasting = {k: found[k] for k in lasting if k in found}
        if not lasting:
            return {}
        if i + 1 < attempts:
            sleep(pause_s)
    return lasting or {}


def state_of(problems: Dict[str, str]) -> str:
    """``ok``, ``down`` (site or API unreachable) or ``degraded``."""
    if not problems:
        return "ok"
    return "down" if any(k in problems for k in DOWN_KEYS) else "degraded"


def title_of(problems: Dict[str, str]) -> str:
    """Issue title for the current problems ('' when there are none)."""
    return {"ok": "", "down": TITLE_DOWN,
            "degraded": TITLE_DEGRADED}[state_of(problems)]


def alert_body(
    problems: Dict[str, str],
    base_url: str,
    attempts: int,
    now: Optional[dt.datetime] = None,
    runbook_url: str = "",
) -> str:
    """Words the issue body (or the follow-up comment) in Chinese."""
    now = now or dt.datetime.now(dt.timezone.utc)
    when = now.astimezone(HKT).strftime("%Y-%m-%d %H:%M")
    lines: List[str] = [
        "> 由 GitHub 定时检测自动创建（`.github/workflows/uptime.yml`），"
        "恢复后会自动关闭。",
        "",
        f"- **检测时间**：{when}（香港时间）",
        f"- **检测地址**：{base_url}",
        "",
        f"发现的问题（连续 {attempts} 次检测都存在）：",
        "",
    ]
    lines += [f"- {problems[k]}" for k in sorted(problems)]
    lines += [
        "",
        "**怎么处理**：先在服务器上看自动恢复做了什么"
        "（`python3 ~/.local/share/stf_selfheal/selfheal.py status`），"
        f"再按运维手册「停机与自动恢复」一节处理{runbook_url}。",
        "",
        "⚠️ 本仓库是公开的：回复时不要贴 VIN、口令、邀请码或设备凭证。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--pause", type=float, default=30)
    p.add_argument("--body-file")
    p.add_argument("--runbook-url", default="")
    p.add_argument("--fail-on-problem", action="store_true")
    args = p.parse_args(argv)
    base = args.base_url.rstrip("/")
    problems = probe(base, attempts=args.attempts, pause_s=args.pause)
    state = state_of(problems)
    print(f"state={state}")
    for key in sorted(problems):
        print(f"  {key}: {problems[key]}")
    if args.body_file and problems:
        link = f"（{args.runbook_url}）" if args.runbook_url else ""
        with open(args.body_file, "w", encoding="utf-8") as f:
            f.write(alert_body(problems, base, args.attempts,
                               runbook_url=link))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"state={state}\ntitle={title_of(problems)}\n")
    return 1 if (problems and args.fail_on_problem) else 0


if __name__ == "__main__":
    sys.exit(main())

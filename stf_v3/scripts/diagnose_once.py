"""Run ONE diagnosis for a vehicle + log with the configured model (PROD-08).

The server-side equivalence check for the agent runtime and the
comparison tool for PROD-09 model changes.  It loads the vehicle, log and
manual inventory from the database, checks the model endpoint first,
runs the main agent, prints every event as it happens and writes the
report + the event log + the message history to an output directory.

    STF_V3_DATABASE_URL=... python scripts/diagnose_once.py \\
        --vehicle-id <uuid> --log-id <uuid> [--locale zh-TW] \\
        [--out-dir ./diagnosis_runs] [--wall-clock-s 2400]

Never writes into the log or manual volumes (FM-1): the output directory
is separate and file names never contain the VIN (FM-25).  Exit codes:
0 complete, 2 partial (budget / timeout / cancelled), 3 model error,
4 preflight failed (endpoint or rows), 5 hung past the script deadline.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vehicle-id", required=True, type=uuid.UUID)
    p.add_argument("--log-id", required=True, type=uuid.UUID)
    p.add_argument("--locale", default=None, help="report language (default: settings.default_locale)")
    p.add_argument("--out-dir", default="./diagnosis_runs", help="where to write report/events/messages")
    p.add_argument("--wall-clock-s", type=float, default=None, help="override the agent wall-clock budget")
    p.add_argument("--no-warmup", action="store_true", help="skip the model warm-up request")
    p.add_argument("--quiet", action="store_true", help="do not print token/reasoning text")
    return p.parse_args(argv)


def _preflight_model(base_url: str, api_key: str, model: str, warmup: bool) -> None:
    """GET /models and (optionally) one tiny completion; raises on failure (FM-13)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        r = client.get(base_url.rstrip("/") + "/models")
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", [])]
        if model not in ids:
            raise RuntimeError(f"model {model!r} not served at {base_url} (served: {ids})")
    if warmup:
        started = time.monotonic()
        with httpx.Client(timeout=900.0, headers=headers) as client:
            r = client.post(base_url.rstrip("/") + "/chat/completions", json={
                "model": model, "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "max_tokens": 64,
            })
            r.raise_for_status()
        print(f"[preflight] model warm-up ok in {time.monotonic() - started:.1f}s", flush=True)


def _print_event(event: Any, quiet: bool) -> None:
    payload: Dict[str, Any] = dict(event.payload)
    nested = f" (in {event.parent_tool_call_id})" if event.parent_tool_call_id else ""
    if event.event_type in ("token", "reasoning"):
        text = payload.get("text", "")
        shown = "" if quiet else (text[:200].replace("\n", " ") + ("…" if len(text) > 200 else ""))
        print(f"[{event.seq:03d}] {event.event_type}{nested} chars={len(text)} {shown}", flush=True)
        return
    print(f"[{event.seq:03d}] {event.event_type}{nested} {json.dumps(payload, ensure_ascii=False, default=str)[:300]}", flush=True)


async def _main(args: argparse.Namespace) -> int:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.bootstrap import DepsNotFound, load_diag_deps
    from stf_v3.diagnosis.agent.main_agent import stream_diagnosis
    from stf_v3.diagnosis.agent.memory import messages_to_jsonable
    from stf_v3.diagnosis.agent.model import ModelConfigError, build_model, describe
    from stf_v3.ingest.loader import LogOwnershipError
    from stf_v3.settings import settings

    out_dir = Path(args.out_dir).resolve()
    for forbidden in (Path(settings.obd_log_storage_path).resolve(), Path(settings.manual_storage_path).resolve()):
        if out_dir == forbidden or forbidden in out_dir.parents:
            print(f"[preflight] refusing to write into a data volume: {out_dir}", file=sys.stderr)
            return 4
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        model = build_model(settings)
    except ModelConfigError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return 4
    try:
        _preflight_model(settings.llm_base_url, settings.llm_api_key, settings.llm_model, warmup=not args.no_warmup)
    except Exception as exc:  # noqa: BLE001
        print(f"[preflight] model endpoint check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    async with SessionLocal() as session:
        try:
            deps = await load_diag_deps(session, args.vehicle_id, args.log_id, settings, locale=args.locale)
        except (DepsNotFound, LogOwnershipError) as exc:
            print(f"[preflight] {exc}", file=sys.stderr)
            return 4
    if args.wall_clock_s:
        deps.budgets.wall_clock_s = args.wall_clock_s
    print(f"[preflight] vehicle={deps.vehicle.manufacturer} {deps.vehicle.model} log={deps.log.id} "
          f"format={deps.log.format} manuals={len(deps.manuals)} model={describe(model)} "
          f"locale={deps.locale} wall_clock={deps.budgets.wall_clock_s:.0f}s", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = out_dir / f"run_{stamp}_{str(deps.log.id)[:8]}"
    stream = stream_diagnosis(deps, model)
    events_path = prefix.with_suffix(".events.jsonl")
    with events_path.open("w", encoding="utf-8") as fh:
        async for event in stream:
            _print_event(event, args.quiet)
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
    outcome = stream.outcome
    assert outcome is not None
    prefix.with_suffix(".report.md").write_text(outcome.report.content_md, encoding="utf-8")
    prefix.with_suffix(".report.json").write_text(
        json.dumps(outcome.report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".messages.json").write_text(
        json.dumps(messages_to_jsonable(outcome.messages), ensure_ascii=False, default=str), encoding="utf-8")
    rep = outcome.report
    print(f"\n== {outcome.stopped_reason.upper()} in {outcome.elapsed_s:.0f}s: requests={rep.requests} "
          f"tool_calls={rep.tool_calls} tokens={rep.total_tokens} report_chars={len(rep.content_md)} "
          f"citations={len(rep.citations)} events={len(outcome.events)}", flush=True)
    print(f"== files: {prefix}.report.md / .report.json / .events.jsonl / .messages.json", flush=True)
    if outcome.error:
        print(f"== error: {outcome.error}", flush=True)
    if outcome.stopped_reason == "complete":
        return 0
    return 3 if outcome.stopped_reason == "error" else 2


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    from stf_v3.settings import settings

    deadline = (args.wall_clock_s or settings.agent_wall_clock_s) + 900.0 + 120.0
    try:
        return asyncio.run(asyncio.wait_for(_main(args), timeout=deadline))
    except asyncio.TimeoutError:
        print(f"[deadline] script exceeded {deadline:.0f}s — aborting", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())

"""Run ONE diagnosis for a vehicle + log with the configured model (PROD-08/09).

The server-side equivalence check for the agent runtime and the
comparison tool for model changes.  It loads the vehicle, log and manual
inventory from the database, checks the model endpoint first, runs the
main agent, prints every event as it happens and writes the report + the
event log + the message history to an output directory.

    STF_V3_DATABASE_URL=... python scripts/diagnose_once.py \\
        --vehicle-id <uuid> --log-id <uuid> [--locale zh-TW] \\
        [--out-dir ./diagnosis_runs] [--wall-clock-s 2400] \\
        [--cloud] [--model-wait-s 600]

``--cloud`` runs the same diagnosis on the cloud comparison model
(``STF_V3_CLOUD_LLM_*``; the VIN is pseudonymised in prompts) -- never
the product path (PROD-09 D3).  The preflight waits up to
``--model-wait-s`` for a local endpoint to list the model (vLLM cold
start, FM-40); cloud endpoints are checked once and never warmed up.

Never writes into the log or manual volumes (FM-1): the output directory
is separate and file names never contain the VIN (FM-25); they end in
``_local`` / ``_cloud`` (FM-2).  Exit codes: 0 complete, 2 partial
(budget / timeout / cancelled / residue), 3 model error, 4 preflight
failed (endpoint or rows), 5 hung past the script deadline.

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
from typing import Any, Callable, Dict, Optional

import httpx


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vehicle-id", required=True, type=uuid.UUID)
    p.add_argument("--log-id", required=True, type=uuid.UUID)
    p.add_argument("--locale", default=None, help="report language (default: settings.default_locale)")
    p.add_argument("--out-dir", default="./diagnosis_runs", help="where to write report/events/messages")
    p.add_argument("--wall-clock-s", type=float, default=None, help="override the agent wall-clock budget")
    p.add_argument("--cloud", action="store_true",
                   help="use the cloud comparison model (STF_V3_CLOUD_LLM_*) instead of the local one")
    p.add_argument("--model-wait-s", type=float, default=None,
                   help="how long to wait for a local endpoint to serve the model (default 600; cloud 0)")
    p.add_argument("--no-warmup", action="store_true", help="skip the model warm-up request (local only)")
    p.add_argument("--quiet", action="store_true", help="do not print token/reasoning text")
    return p.parse_args(argv)


def _preflight_model(
    base_url: str,
    api_key: str,
    model: str,
    *,
    warmup: bool,
    wait_s: float = 0.0,
    poll_s: float = 5.0,
    transport: Optional[httpx.BaseTransport] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> float:
    """Wait until ``GET /models`` lists ``model`` (up to ``wait_s``), then
    optionally one tiny completion.  Returns the seconds waited.

    Raises:
        RuntimeError: The model is not served (after the wait) or the
            endpoint keeps failing.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = now()
    last_error = "not checked"
    while True:
        try:
            with httpx.Client(timeout=30.0, headers=headers, transport=transport) as client:
                r = client.get(base_url.rstrip("/") + "/models")
                r.raise_for_status()
                ids = [m.get("id") for m in r.json().get("data", [])]
            if model in ids:
                break
            last_error = f"model {model!r} not served at {base_url} (served: {ids[:20]})"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        waited = now() - started
        if waited >= wait_s:
            raise RuntimeError(f"{last_error} (waited {waited:.0f}s)")
        print(f"[preflight] model not ready ({last_error[:80]}); waiting… {waited:.0f}/{wait_s:.0f}s", flush=True)
        sleep(poll_s)
    waited = now() - started
    if waited > 0:
        print(f"[preflight] model listed after {waited:.0f}s", flush=True)
    if warmup:
        t0 = now()
        with httpx.Client(timeout=900.0, headers=headers, transport=transport) as client:
            r = client.post(base_url.rstrip("/") + "/chat/completions", json={
                "model": model, "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "max_tokens": 64,
            })
            r.raise_for_status()
        print(f"[preflight] model warm-up ok in {now() - t0:.1f}s", flush=True)
    return waited


def _print_event(event: Any, quiet: bool) -> None:
    payload: Dict[str, Any] = dict(event.payload)
    nested = f" (in {event.parent_tool_call_id})" if event.parent_tool_call_id else ""
    if event.event_type in ("token", "reasoning"):
        text = payload.get("text", "")
        shown = "" if quiet else (text[:200].replace("\n", " ") + ("…" if len(text) > 200 else ""))
        print(f"[{event.seq:03d}] {event.event_type}{nested} chars={len(text)} {shown}", flush=True)
        return
    print(f"[{event.seq:03d}] {event.event_type}{nested} {json.dumps(payload, ensure_ascii=False, default=str)[:300]}", flush=True)


def _run_prefix(out_dir: Path, log_id: Any, cloud: bool, stamp: Optional[str] = None) -> Path:
    """``<out_dir>/run_<utc stamp>_<log id[:8]>_<local|cloud>`` -- never the VIN (FM-25 / FM-2)."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return out_dir / f"run_{stamp}_{str(log_id)[:8]}_{'cloud' if cloud else 'local'}"


def _endpoint(settings: Any, cloud: bool) -> tuple:
    if cloud:
        return settings.cloud_llm_base_url, settings.cloud_api_key, settings.cloud_llm_model
    return settings.llm_base_url, settings.llm_api_key, settings.llm_model


async def _main(args: argparse.Namespace) -> int:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.bootstrap import DepsNotFound, load_diag_deps
    from stf_v3.diagnosis.agent.main_agent import stream_diagnosis
    from stf_v3.diagnosis.agent.memory import messages_to_jsonable
    from stf_v3.diagnosis.agent.model import ModelConfigError, build_model, describe, source_label
    from stf_v3.ingest.loader import LogOwnershipError
    from stf_v3.settings import settings

    out_dir = Path(args.out_dir).resolve()
    for forbidden in (Path(settings.obd_log_storage_path).resolve(), Path(settings.manual_storage_path).resolve()):
        if out_dir == forbidden or forbidden in out_dir.parents:
            print(f"[preflight] refusing to write into a data volume: {out_dir}", file=sys.stderr)
            return 4
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        model = build_model(settings, cloud=args.cloud)
    except ModelConfigError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return 4
    base_url, api_key, model_name = _endpoint(settings, args.cloud)
    wait_s = args.model_wait_s if args.model_wait_s is not None else (0.0 if args.cloud else 600.0)
    try:
        _preflight_model(base_url, api_key, model_name, warmup=not args.no_warmup and not args.cloud, wait_s=wait_s)
    except Exception as exc:  # noqa: BLE001
        print(f"[preflight] model endpoint check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    async with SessionLocal() as session:
        try:
            deps = await load_diag_deps(session, args.vehicle_id, args.log_id, settings, locale=args.locale,
                                        model_is_local=False if args.cloud else None)
        except (DepsNotFound, LogOwnershipError) as exc:
            print(f"[preflight] {exc}", file=sys.stderr)
            return 4
    if args.cloud:
        from stf_v3.diagnosis.agent.deps import Budgets
        from stf_v3.diagnosis.agent.model import select_profile

        deps.budgets = Budgets.from_settings(settings, select_profile(settings, cloud=True))
    if args.wall_clock_s:
        deps.budgets.wall_clock_s = args.wall_clock_s
    print(f"[preflight] vehicle={deps.vehicle.manufacturer} {deps.vehicle.model} log={deps.log.id} "
          f"format={deps.log.format} manuals={len(deps.manuals)} model={describe(model)} "
          f"source={source_label(model)} vin_in_prompt={'raw' if deps.model_is_local else 'pseudonym'} "
          f"locale={deps.locale} wall_clock={deps.budgets.wall_clock_s:.0f}s", flush=True)

    prefix = _run_prefix(out_dir, deps.log.id, args.cloud)
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
    print(f"\n== {outcome.stopped_reason.upper()}{' (PARTIAL)' if rep.partial else ''} in {outcome.elapsed_s:.0f}s: "
          f"requests={rep.requests} tool_calls={rep.tool_calls} tokens={rep.total_tokens} "
          f"report_chars={len(rep.content_md)} citations={len(rep.citations)} events={len(outcome.events)} "
          f"thinking_chars={rep.thinking_chars} filter_hits={rep.filter_hits} source={rep.model_source}", flush=True)
    print(f"== files: {prefix}.report.md / .report.json / .events.jsonl / .messages.json", flush=True)
    if outcome.error:
        print(f"== error: {outcome.error}", flush=True)
    if outcome.stopped_reason == "complete":
        return 2 if rep.partial else 0
    return 3 if outcome.stopped_reason == "error" else 2


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    from stf_v3.diagnosis.agent.deps import Budgets
    from stf_v3.diagnosis.agent.model import ModelConfigError, select_profile
    from stf_v3.settings import settings

    try:
        wall = args.wall_clock_s or Budgets.from_settings(settings, select_profile(settings, cloud=args.cloud)).wall_clock_s
    except ModelConfigError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return 4
    model_wait = args.model_wait_s if args.model_wait_s is not None else (0.0 if args.cloud else 600.0)
    deadline = wall + model_wait + 900.0 + 120.0
    try:
        return asyncio.run(asyncio.wait_for(_main(args), timeout=deadline))
    except asyncio.TimeoutError:
        print(f"[deadline] script exceeded {deadline:.0f}s — aborting", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())

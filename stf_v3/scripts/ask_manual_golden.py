"""Ask the manual sub-agent ONE golden question, locally or on the cloud model.

PROD-09 acceptance helper ("同一 golden 用例本地与云端各跑通一次"): takes a
question (or one entry of a V2 golden ``.jsonl``), pins the vehicle the
question is about, runs the manual sub-agent against the V3 manual
library and writes summary + citations + events to a file whose name
ends in ``_local`` / ``_cloud``.  Formal scoring is PROD-10.

    STF_V3_DATABASE_URL=... python scripts/ask_manual_golden.py \\
        --question "What is the recommended drive-chain slack for the MWS-150-A?" \\
        [--manufacturer Yamaha --model TRICITY155] [--cloud] [--out-dir /tmp/runs]
    python scripts/ask_manual_golden.py --golden-file mws150a.jsonl --id <golden id> [--cloud]

Exit codes: 0 complete, 2 stopped by a budget, 4 preflight / config error.

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

FAKE_VIN = "JHMGK5830HX202404"   # the fixture VIN: this run has no real vehicle


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--question", help="the inquiry text")
    src.add_argument("--golden-file", help="a V2 golden .jsonl; use with --id")
    p.add_argument("--id", help="golden entry id (with --golden-file)")
    p.add_argument("--manufacturer", default="Yamaha")
    p.add_argument("--model", default="TRICITY155")
    p.add_argument("--cloud", action="store_true", help="use the cloud comparison model")
    p.add_argument("--out-dir", default="./diagnosis_runs")
    return p.parse_args(argv)


def _load_golden(path: str, gid: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                if row.get("id") == gid:
                    return row
    raise SystemExit(f"golden id {gid!r} not in {path}")


async def _main(args: argparse.Namespace) -> int:
    from pydantic_ai.usage import RunUsage

    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.bootstrap import load_manual_inventory
    from stf_v3.diagnosis.agent.deps import Budgets, DiagDeps, LogInfo, VehicleInfo
    from stf_v3.diagnosis.agent.events import EventSink
    from stf_v3.diagnosis.agent.manual_agent import run_manual_agent
    from stf_v3.diagnosis.agent.model import ModelConfigError, build_model, select_profile, source_label
    from stf_v3.settings import settings

    question, obd_context, gid = args.question, None, "adhoc"
    if args.golden_file:
        if not args.id:
            print("--golden-file needs --id", file=sys.stderr)
            return 4
        row = _load_golden(args.golden_file, args.id)
        question, obd_context, gid = row["question"], row.get("obd_context") or None, row["id"]
    try:
        model = build_model(settings, cloud=args.cloud)
    except ModelConfigError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return 4
    async with SessionLocal() as session:
        manuals = await load_manual_inventory(session)
    if not manuals:
        print("[preflight] no ingested manuals", file=sys.stderr)
        return 4
    deps = DiagDeps(
        vehicle=VehicleInfo(id=uuid.uuid4(), manufacturer=args.manufacturer, model=args.model, vin=FAKE_VIN),
        log=LogInfo(id=uuid.uuid4(), vehicle_id=uuid.uuid4(), raw_path="", format="tsv"),
        manuals=manuals,
        log_root=Path(settings.obd_log_storage_path),
        manual_root=Path(settings.manual_storage_path),
        budgets=Budgets.from_settings(settings, select_profile(settings, cloud=args.cloud)),
        model_is_local=not args.cloud and settings.llm_is_local,
        images_enabled=settings.manual_images_enabled,
        events=EventSink(),
    )
    print(f"[preflight] manuals={[m.id[:8] for m in manuals]} vehicle={args.manufacturer} {args.model} "
          f"source={source_label(model)} vin_in_prompt={'raw' if deps.model_is_local else 'pseudonym'}", flush=True)
    started = time.monotonic()
    result = await run_manual_agent(deps, model, question, obd_context, None, RunUsage())
    elapsed = time.monotonic() - started
    await deps.events.drain()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"golden_{stamp}_{gid[-12:]}_{'cloud' if args.cloud else 'local'}.json"
    payload = {
        "golden_id": gid, "question": question, "source": source_label(model), "elapsed_s": round(elapsed, 1),
        "stopped_reason": result.stopped_reason, "summary": result.summary,
        "citations": [c.model_dump() for c in result.citations],
        "raw_sections": [{"manual_id": s.manual_id, "slug": s.slug} for s in result.raw_sections],
        "tool_calls": len(deps.trace), "total_tokens": result.total_tokens,
        "thinking_chars": sum(len(e.payload.get("text", "")) for e in deps.events.events if e.event_type == "reasoning"),
        "events": [e.to_dict() for e in deps.events.events],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n== {result.stopped_reason.upper()} in {elapsed:.0f}s: tool_calls={len(deps.trace)} "
          f"tokens={result.total_tokens} citations={len(result.citations)} "
          f"thinking_chars={payload['thinking_chars']} source={source_label(model)}")
    print(f"== summary: {result.summary[:600]}")
    for c in result.citations:
        print(f"== citation: {c.manual_id}#{c.slug}")
    print(f"== file: {out}")
    return 0 if result.stopped_reason == "complete" else 2


def main(argv: Optional[list] = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

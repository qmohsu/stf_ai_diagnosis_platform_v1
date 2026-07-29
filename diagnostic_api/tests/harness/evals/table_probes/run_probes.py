"""Table-probe runner (S3.4) — deterministic, no LLM.

Runs inside the one-shot eval container (tools + manuals volume):

    python /app/tests/harness/evals/table_probes/run_probes.py

For each probe: find (search hits) → read (hit node contains the
value) → assoc (row qualifier within ±200 chars of the value).
Emits a JSON report next to this file and exits non-zero when the
pass rate is below the 85% ladder threshold.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import yaml

from app.harness_tools.manual_tools import (
    read_manual_section,
    search_manual_text,
)

_HERE = Path(__file__).parent
_THRESHOLD = 0.85
_ASSOC_WINDOW = 200
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})


def _norm(text: str) -> str:
    return text.translate(_DASHES)


def _flatten(result) -> str:
    if isinstance(result, str):
        return result
    return " ".join(
        str(b.get("text", "")) if isinstance(b, dict) else str(b)
        for b in result
    )


async def _run() -> int:
    spec = yaml.safe_load(
        (_HERE / "probes.yaml").read_text(encoding="utf-8"),
    )
    manual_id = spec["manual_id"]
    results = []
    for probe in spec["probes"]:
        value = _norm(probe["value"])
        qualifier = _norm(probe["qualifier"])
        record = {
            "id": probe["id"], "find": False,
            "read": False, "assoc": False, "node": None,
        }

        search = _norm(await search_manual_text({
            "manual_id": manual_id,
            "query": probe["value"],
            "max_hits": 5,
        }))
        nodes = re.findall(r"\[node: ([^\]]+)\]", search)
        if nodes and "0 matches" not in search:
            record["find"] = True
            for node_id in dict.fromkeys(nodes):
                section = _norm(_flatten(
                    await read_manual_section({
                        "manual_id": manual_id,
                        "section": node_id,
                    }),
                ))
                pos = section.find(value)
                if pos < 0:
                    continue
                record["read"] = True
                record["node"] = node_id
                window = section[
                    max(0, pos - _ASSOC_WINDOW):
                    pos + _ASSOC_WINDOW
                ]
                if qualifier in window:
                    record["assoc"] = True
                    break
        record["passed"] = (
            record["find"] and record["read"]
            and record["assoc"]
        )
        results.append(record)

    passed = sum(1 for r in results if r["passed"])
    rate = passed / len(results)
    verdict = (
        "LADDER-SHELVED (rung 1 sufficient)"
        if rate >= _THRESHOLD else
        "ESCALATE (rung 2: pdfplumber)"
    )
    report = {
        "passed": passed, "total": len(results),
        "rate": round(rate, 3), "threshold": _THRESHOLD,
        "verdict": verdict, "probes": results,
    }
    out = _HERE / "probe_report.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[probes] {passed}/{len(results)} rate={rate:.0%} "
          f"-> {verdict}")
    for r in results:
        if not r["passed"]:
            print(f"[probes]   FAIL {r['id']}: "
                  f"find={r['find']} read={r['read']} "
                  f"assoc={r['assoc']} node={r['node']}")
    return 0 if rate >= _THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))

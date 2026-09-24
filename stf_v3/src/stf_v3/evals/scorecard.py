"""The eval scorecard: V2-compatible JSON + V3 run metadata (PROD-10).

Shape (V2 ``EvalReport`` + extra keys V2 readers ignore)::

    {"started_at", "ended_at", "count",
     "meta": {schema, stamp, git_commit, mode{backend, thinking, purpose,
              budget_scale}, lanes, ids, expected, completed, complete,
              valid, invalid_reasons, failed_items, config{...whitelist},
              pipeline{...}, secrets_redacted, slim},
     "records": [{"entry", "result", "grade", "item": {status, stats…}}]}

* ``complete`` = every expected golden produced a graded record (FM-18).
* ``valid``    = complete, no judge failure, no eval-level timeout or
  error, the tokenizer was the real one (FM-19 / FM-24).  Only valid,
  complete scorecards may feed the gate or a baseline.
* Every string is scrubbed for secret values / key patterns before it
  is written (FM-33); the count is recorded.
* Files are written atomically; ``<name>.partial.json`` is rewritten
  after every finished golden and replaced by the final file (FM-14).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = "stf_v3.eval.scorecard/1"

PURPOSES = ("baseline", "gate", "calibration", "comparison", "demo", "adhoc")
GATE_PURPOSES = ("baseline", "gate")

_SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[0-9a-fA-F]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:[^@\s]+@"),
]
_SECRET_ENV_RE = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|DATABASE_URL)", re.I)
_NOT_SECRET = {"", "none", "null", "true", "false", "changeme"}
REDACTED = "<redacted>"


def secret_values(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Values of secret-looking environment variables (≥ 8 chars)."""
    env = dict(os.environ if env is None else env)
    out: List[str] = []
    for name, value in env.items():
        if not _SECRET_ENV_RE.search(name):
            continue
        value = (value or "").strip()
        if len(value) >= 8 and value.lower() not in _NOT_SECRET:
            out.append(value)
            m = re.match(r"^[a-z0-9+]+://[^:/@]+:([^@]+)@", value)   # a DB URL's password alone
            if m and len(m.group(1)) >= 6:
                out.append(m.group(1))
    return sorted(set(out), key=len, reverse=True)


def scrub(obj: Any, secrets: Iterable[str]) -> Tuple[Any, int]:
    """Replace secret values / key patterns in every string; returns (obj, count)."""
    secrets = [s for s in secrets if s]
    count = 0

    def _s(text: str) -> str:
        nonlocal count
        for s in secrets:
            if s in text:
                count += text.count(s)
                text = text.replace(s, REDACTED)
        for pat in _SECRET_PATTERNS:
            text, n = pat.subn(REDACTED, text)
            count += n
        return text

    def _walk(x: Any) -> Any:
        if isinstance(x, str):
            return _s(x)
        if isinstance(x, dict):
            return {_walk(k) if isinstance(k, str) else k: _walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_walk(v) for v in x]
        return x

    return _walk(obj), count


def find_secrets(text: str, secrets: Iterable[str] = ()) -> List[str]:
    """Names of what would be redacted in ``text`` (for CI scans)."""
    hits = [f"value#{i}" for i, s in enumerate(secrets) if s and s in text]
    hits += [p.pattern[:24] for p in _SECRET_PATTERNS if p.search(text)]
    return hits


def validity(card: Dict[str, Any]) -> Tuple[bool, bool, List[str]]:
    """``(complete, valid, reasons)`` from the records and meta."""
    meta = card.get("meta", {})
    records = card.get("records", [])
    reasons: List[str] = []
    expected = int(meta.get("expected", len(records)))
    failed = meta.get("failed_items", [])
    complete = len(records) == expected and not failed
    if len(records) != expected:
        reasons.append(f"{len(records)}/{expected} goldens graded")
    for f in failed:
        reasons.append(f"{f.get('lane')}:{f.get('id')} {f.get('status')}")
    judge_failed = [r["entry"]["id"] for r in records if (r.get("item") or {}).get("status") == "judge_failed"]
    if judge_failed:
        reasons.append(f"judge failed on {len(judge_failed)}: {', '.join(judge_failed[:5])}")
    run_errors = [r["entry"]["id"] for r in records if (r.get("item") or {}).get("status") == "run_error"]
    if run_errors:
        reasons.append(f"sub-agent error (infrastructure) on {len(run_errors)}: {', '.join(run_errors[:5])}")
    if meta.get("config", {}).get("tokenizer") not in (None, "cl100k_base"):
        reasons.append(f"tokenizer fell back to {meta['config']['tokenizer']}")
    for extra in meta.get("extra_invalid_reasons", []):
        reasons.append(extra)
    valid = complete and not reasons
    return complete, valid, reasons


def finalize(card: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute counts / completeness / validity in place."""
    meta = card.setdefault("meta", {})
    card["count"] = len(card.get("records", []))
    complete, valid, reasons = validity(card)
    meta["completed"] = card["count"]
    meta["complete"] = complete
    meta["valid"] = valid
    meta["invalid_reasons"] = reasons
    return card


def slim(card: Dict[str, Any]) -> Dict[str, Any]:
    """A small copy for gate PRs (FM-51): no cited-section text, no tool inputs."""
    out = json.loads(json.dumps(card))
    out["meta"]["slim"] = True
    for r in out.get("records", []):
        res = r.get("result", {})
        text = res.get("output_text") or ""
        res["output_text"] = text.split("\n\n--- Cited sections", 1)[0]
        res["tool_trace"] = [{"name": t.get("name"), "input": {}, "latency_ms": t.get("latency_ms", 0.0),
                              "is_error": t.get("is_error", False)} for t in res.get("tool_trace", [])]
        for img in res.get("surfaced_images", []) or []:
            img["vision_descriptions"] = []
    return out


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dumps(card: Dict[str, Any], secrets: Iterable[str]) -> str:
    """Scrub, record the redaction count, serialise."""
    clean, n = scrub(card, secrets)
    clean.setdefault("meta", {})["secrets_redacted"] = int(card.get("meta", {}).get("secrets_redacted", 0)) + n
    return json.dumps(clean, ensure_ascii=False, indent=1, default=str)


def unique_base(out_dir: Path, base: str) -> str:
    """``base`` or ``base-2`` … so two runs never overwrite each other (FM-14)."""
    candidate, n = base, 1
    while any((out_dir / f"{candidate}{suffix}").exists() for suffix in (".json", ".partial.json")):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def write_partial(out_dir: Path, base: str, card: Dict[str, Any], secrets: Iterable[str]) -> Path:
    path = out_dir / f"{base}.partial.json"
    _atomic_write(path, dumps(finalize(card), secrets))
    return path


def write_final(out_dir: Path, base: str, card: Dict[str, Any], secrets: Iterable[str]) -> Tuple[Path, Path]:
    """Write ``<base>.json`` + ``<base>.slim.json``; drop the partial file."""
    finalize(card)
    full = out_dir / f"{base}.json"
    lite = out_dir / f"{base}.slim.json"
    secrets = list(secrets)
    _atomic_write(full, dumps(card, secrets))
    _atomic_write(lite, dumps(slim(json.loads(full.read_text(encoding="utf-8"))), secrets))
    partial = out_dir / f"{base}.partial.json"
    if partial.exists():
        partial.unlink()
    return full, lite


def load(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def base_name(stamp: str, commit: str, purpose: str, backend: str, thinking: bool) -> str:
    """``<stamp>_<commit8>_<purpose>_<local|cloud>_think-<on|off>``."""
    return f"{stamp}_{(commit or 'unknown')[:8]}_{purpose}_{backend}_think-{'on' if thinking else 'off'}"


REQUIRED_RECORD_KEYS = ("entry", "result", "grade", "item")
REQUIRED_GRADE_KEYS = ("section_recall", "claim_precision", "exploration_cost", "fact_recall", "fact_density",
                       "hallucination_penalty", "citation_quality", "answer_quality", "trajectory_efficiency",
                       "value_accuracy", "overall", "reasoning")
REQUIRED_META_KEYS = ("schema", "stamp", "git_commit", "mode", "lanes", "expected", "completed", "complete",
                      "valid", "invalid_reasons", "failed_items", "config", "pipeline", "secrets_redacted")


__all__ = ["GATE_PURPOSES", "PURPOSES", "REDACTED", "REQUIRED_GRADE_KEYS", "REQUIRED_META_KEYS",
           "REQUIRED_RECORD_KEYS", "SCHEMA", "base_name", "dumps", "finalize", "find_secrets", "load",
           "scrub", "secret_values", "slim", "unique_base", "validity", "write_final", "write_partial"]

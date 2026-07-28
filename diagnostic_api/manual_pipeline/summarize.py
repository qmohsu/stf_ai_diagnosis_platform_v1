"""Node-summary enrichment via OpenRouter (S2.5).

One-time build-cost pass (user decision 2026-07-23: DeepSeek via
OpenRouter — same egress class as the production V2 agent, no new
exposure).  LLM output is accepted ONLY through two mechanical
gates; a rejected summary falls back to a deterministic extract so
I6 can still pass and the failure is visible in the report.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List

from manual_pipeline.index_schema import IndexNode
from manual_pipeline.stream import NormalizedItem

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_SUMMARY_CHARS = 300
_MAX_SECTION_CHARS = 6000
_DTC_OR_NUM_RE = re.compile(
    r"\b[PCBU]\d[0-9A-F]{3}\b|\d+(?:\.\d+)?",
    re.IGNORECASE,
)

_SYSTEM = (
    "You summarize one service-manual section for an AI "
    "navigation index. Reply with ONE sentence (max 60 words / "
    "120 CJK chars) in the section's own language stating what a "
    "technician finds there: the task, component, and any DTC "
    "codes or key spec values COPIED VERBATIM from the text. "
    "Never add codes or numbers that are not in the text. No "
    "preamble, no quotes, just the sentence."
)


@dataclass
class SummaryStats:
    """Enrichment provenance for the build report."""

    generated: int = 0
    gate_rejected: int = 0
    fallback_extractive: int = 0
    rejected_nodes: List[str] = field(default_factory=list)


def _section_text(
    node: IndexNode, items: List[NormalizedItem],
) -> str:
    parts = [node.title]
    for it in items[node.span[0]:node.span[1]]:
        if it.text:
            parts.append(it.text)
        if it.html:
            parts.append(re.sub(r"<[^>]+>", " ", it.html))
    return "\n".join(parts)[:_MAX_SECTION_CHARS]


def _mechanical_gates(summary: str, section: str) -> bool:
    """Gate 1: non-empty + bounded.  Gate 2: every DTC/number in
    the summary must literally exist in the section text."""
    s = summary.strip()
    if not s or len(s) > _MAX_SUMMARY_CHARS:
        return False
    for token in _DTC_OR_NUM_RE.findall(s):
        if token not in section:
            return False
    return True


def _extractive_fallback(section: str) -> str:
    """Deterministic fallback: first content line, bounded."""
    for line in section.split("\n")[1:]:
        line = line.strip()
        if len(line) >= 8:
            return line[:_MAX_SUMMARY_CHARS]
    return section.split("\n")[0][:_MAX_SUMMARY_CHARS]


def _call_openrouter(
    api_key: str, model: str, section: str,
    timeout: float = 60.0,
) -> str:
    payload = json.dumps({
        "model": model,
        "temperature": 0.1,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": section},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"].strip()


def enrich_summaries(
    nodes: List[IndexNode],
    items: List[NormalizedItem],
    api_key: str,
    model: str,
    retries: int = 2,
    throttle_s: float = 0.2,
) -> SummaryStats:
    """Fill ``node.summary`` for every node, gated mechanically.

    Args:
        nodes: Flat node list (mutated in place).
        items: Normalized stream for section text.
        api_key: OpenRouter key.
        model: Model slug (user decision: DeepSeek).
        retries: Per-node retry count on API/gate failure.
        throttle_s: Sleep between calls.

    Returns:
        SummaryStats for the build report.
    """
    stats = SummaryStats()
    total = len(nodes)
    for pos, node in enumerate(nodes, 1):
        if pos % 25 == 0 or pos == total:
            print(
                f"[summaries] {pos}/{total} "
                f"(rejected={stats.gate_rejected})",
                flush=True,
            )
        section = _section_text(node, items)
        accepted = ""
        for _ in range(retries + 1):
            try:
                candidate = _call_openrouter(
                    api_key, model, section,
                )
            except Exception:
                time.sleep(1.0)
                continue
            if _mechanical_gates(candidate, section):
                accepted = candidate
                break
            stats.gate_rejected += 1
        if accepted:
            node.summary = accepted
            stats.generated += 1
        else:
            node.summary = _extractive_fallback(section)
            stats.fallback_extractive += 1
            stats.rejected_nodes.append(node.node_id)
        time.sleep(throttle_s)
    return stats

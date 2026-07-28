"""I0 reconciliation gate + build report (spec §1.3 ⑥, §6 I0/6.1).

The audit is deliberately independent of the composer's own
bookkeeping: it re-derives the baseline from the PDF and checks
the FINAL markdown, so a composer bug cannot vouch for itself.
A single missing line fails the build.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Tuple

from manual_pipeline.authority import TextAuthority, normalize
from manual_pipeline.compose import ComposeResult


class ReconciliationError(AssertionError):
    """Raised when the I0 gate fails — the build must not publish."""


@dataclass
class AuditResult:
    """I0 outcome + coverage statistics for the build report."""

    baseline_lines: int
    missing_lines: int
    missing_samples: List[Tuple[int, str]] = field(
        default_factory=list,
    )
    char_recall: float = 1.0

    @property
    def passed(self) -> bool:
        """Exact gate: zero missing lines."""
        return self.missing_lines == 0


def reconcile(
    markdown: str,
    authority: TextAuthority,
    max_samples: int = 30,
) -> AuditResult:
    """Run the I0 exact reconciliation over the final markdown.

    Args:
        markdown: The composed content (frontmatter included).
        authority: Text authority for the same source PDF.
        max_samples: Cap on reported missing-line samples.

    Returns:
        AuditResult; ``passed`` is the gate.
    """
    corpus = normalize(markdown)
    total = 0
    total_chars = 0
    missing = 0
    missing_chars = 0
    samples: List[Tuple[int, str]] = []
    for page in range(1, authority.page_count + 1):
        for ln in authority.content_lines(page):
            total += 1
            total_chars += len(ln.norm)
            if ln.norm not in corpus:
                missing += 1
                missing_chars += len(ln.norm)
                if len(samples) < max_samples:
                    samples.append((page, ln.raw[:80]))
    return AuditResult(
        baseline_lines=total,
        missing_lines=missing,
        missing_samples=samples,
        char_recall=(
            1.0 - missing_chars / total_chars
            if total_chars else 1.0
        ),
    )


def write_build_report(
    out_path: Path,
    audit: AuditResult,
    compose_result: ComposeResult,
    meta: dict,
) -> None:
    """Write the human-auditable build report (spec §6.1).

    Args:
        out_path: Destination JSON path.
        audit: I0 outcome.
        compose_result: Composition provenance (rescues, backfill).
        meta: Build metadata (source, parser, timestamps).
    """
    report = {
        "meta": meta,
        "i0_gate": {
            "passed": audit.passed,
            "baseline_lines": audit.baseline_lines,
            "missing_lines": audit.missing_lines,
            "char_recall": round(audit.char_recall, 6),
            "missing_samples": audit.missing_samples,
        },
        "rescues": {
            "count": len(compose_result.rescues),
            "table_markdown_recovered": sum(
                1 for r in compose_result.rescues
                if r.table_markdown_recovered
            ),
            "records": [asdict(r) for r in compose_result.rescues],
        },
        "recovered_text": {
            "lines": compose_result.recovered_lines,
            "pages": sorted(set(compose_result.recovered_pages)),
        },
        "images_emitted": compose_result.images_emitted,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def gate_or_raise(audit: AuditResult) -> None:
    """Enforce I0: raise (build-failing) when any line is missing."""
    if not audit.passed:
        preview = "; ".join(
            f"p{p}:{t}" for p, t in audit.missing_samples[:5]
        )
        raise ReconciliationError(
            f"I0 FAILED: {audit.missing_lines} baseline line(s) "
            f"missing from the composed markdown "
            f"(char recall {audit.char_recall:.4%}). "
            f"First misses: {preview}"
        )

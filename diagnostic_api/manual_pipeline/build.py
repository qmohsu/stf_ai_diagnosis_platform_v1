"""Storage-pipeline orchestrator + CLI (HARNESS-30 Phase 1).

Usage (build host, e.g. inside ``~/bakeoff`` venv on the server):

    python -m manual_pipeline.build \\
        --pdf tricity155.pdf \\
        --mineru-dir out_mineru/tricity155/hybrid_auto \\
        --out out_content \\
        --frontmatter-from prod_marker110.md

Produces ``out/<stem>.md`` + ``out/images/`` + ``out/build_report
.json``.  Exits non-zero when the I0 gate fails.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from manual_pipeline.audit import (
    gate_or_raise,
    reconcile,
    write_build_report,
)
from manual_pipeline.authority import TextAuthority
from manual_pipeline.compose import compose
from manual_pipeline.stream import load_mineru_stream


def extract_frontmatter(md_path: Path) -> str:
    """Copy the YAML frontmatter block from an existing manual md.

    Keeps the identity contract (``vehicle_model``,
    ``factory_code`` …) that ``list_manuals`` reads.

    Args:
        md_path: Existing manual markdown with frontmatter.

    Returns:
        The frontmatter block including both ``---`` fences, or
        '' when the file has none.
    """
    text = md_path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return ""
    end = stripped.find("---", 3)
    if end < 0:
        return ""
    return stripped[: end + 3] + "\n"


def run_build(
    pdf: Path,
    mineru_dir: Path,
    out_dir: Path,
    frontmatter_from: Path | None = None,
) -> bool:
    """Run the full storage pipeline for one manual.

    Args:
        pdf: Source PDF.
        mineru_dir: MinerU output dir containing
            ``*_content_list_v2.json`` and ``images/``.
        out_dir: Destination directory.
        frontmatter_from: Optional existing md whose frontmatter
            (manual identity) is carried over.

    Returns:
        True when the I0 gate passed.
    """
    content_list = next(
        mineru_dir.glob("*_content_list_v2.json"),
    )
    items = load_mineru_stream(content_list)
    authority = TextAuthority(pdf)
    frontmatter = (
        extract_frontmatter(frontmatter_from)
        if frontmatter_from else ""
    )

    result = compose(
        items, authority, mineru_dir, out_dir, frontmatter,
    )
    md_path = out_dir / f"{pdf.stem}.md"
    md_path.write_text(result.markdown, encoding="utf-8")
    # Item → markdown-line mapping for the index build (Phase 3:
    # nodes get md_lines so the runtime can slice content).
    (out_dir / "item_lines.json").write_text(
        json.dumps({
            str(k): v for k, v in result.item_lines.items()
        }),
        encoding="utf-8",
    )

    audit = reconcile(result.markdown, authority)
    write_build_report(
        out_dir / "build_report.json",
        audit,
        result,
        meta={
            "source_pdf": pdf.name,
            "parser": "mineru-3.4.4/hybrid-engine "
                      "+ pymupdf-authority",
            "content_list": content_list.name,
            "built_at": datetime.datetime.now(
                datetime.timezone.utc,
            ).isoformat(),
            "spec": "manual_index_spec v0.3 §1.3",
        },
    )
    print(
        f"[build] items={len(items)} "
        f"baseline_lines={audit.baseline_lines} "
        f"missing={audit.missing_lines} "
        f"char_recall={audit.char_recall:.4%} "
        f"rescues={len(result.rescues)} "
        f"(tables_recovered="
        f"{sum(1 for r in result.rescues if r.table_markdown_recovered)}) "
        f"backfilled_lines={result.recovered_lines} "
        f"images={result.images_emitted}"
    )
    authority.close()
    gate_or_raise(audit)
    return True


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build manual content.md (Phase 1 pipeline)",
    )
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--mineru-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--frontmatter-from", type=Path, default=None,
    )
    args = parser.parse_args()
    try:
        run_build(
            args.pdf, args.mineru_dir, args.out,
            args.frontmatter_from,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[build] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

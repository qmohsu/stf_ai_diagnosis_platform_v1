"""Index-build orchestrator + CLI (S2.6).

Usage (build host):

    PYTHONPATH=. python -m manual_pipeline.index_build \\
        --mineru-dir out_mineru/tricity155/hybrid_auto \\
        --content-md out_content/tricity155.md \\
        --manual-id 0a2ba199-… \\
        --out out_content \\
        [--summaries --model deepseek/deepseek-chat]

Writes ``<out>/<manual_id>.index.yaml`` + extends the build report.
Exits non-zero when ANY invariant gate fails.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import fitz  # noqa: F401 — asserts build-host deps early.
import yaml

from manual_pipeline.entities import (
    build_fault_entities,
    sweep_codes,
)
from manual_pipeline.index_schema import ManualIndex, Vocab
from manual_pipeline.invariants import validate
from manual_pipeline.stream import load_mineru_stream
from manual_pipeline.summarize import (
    enrich_summaries,
    summary_conforms,
)
from manual_pipeline.tree_builder import build_tree


def run_index_build(
    mineru_dir: Path,
    content_md_path: Path,
    manual_id: str,
    out_dir: Path,
    applicability: dict | None = None,
    with_summaries: bool = False,
    model: str = "deepseek/deepseek-chat",
    item_lines_path: Path | None = None,
    reuse_summaries_from: Path | None = None,
    alias_map_path: Path | None = None,
) -> bool:
    """Build + validate + write the index sidecar.

    Args:
        mineru_dir: Engine output dir (geometry stream source).
        content_md_path: Phase-1 content markdown (hash target +
            entity sweep source).
        manual_id: The manual's id (md filename stem).
        out_dir: Destination directory.
        applicability: Manufacturer/models dict; defaults to the
            TRICITY155 identity when omitted.
        with_summaries: Run S2.5 enrichment (needs
            OPENROUTER_API_KEY in the environment).
        model: OpenRouter model slug for summaries.

    Returns:
        True when every gate passed AND the artifact was written.
    """
    content_list = next(
        mineru_dir.glob("*_content_list_v2.json"),
    )
    items = load_mineru_stream(content_list)
    content_md = content_md_path.read_text(encoding="utf-8")
    vocab = Vocab.load()
    total_pages = max(it.page for it in items)

    tree_result = build_tree(items, vocab, total_pages)
    all_nodes = [
        n for root in tree_result.roots for n in root.walk()
    ]
    codes = sweep_codes(content_md)
    faults = build_fault_entities(codes, items, all_nodes)

    # ── md_lines stamping (Phase 3 runtime anchor) ───────────
    if item_lines_path and item_lines_path.is_file():
        raw_lines = json.loads(
            item_lines_path.read_text(encoding="utf-8"),
        )
        item_lines = {int(k): v for k, v in raw_lines.items()}
        for node in all_nodes:
            ranges = [
                item_lines[i]
                for i in range(node.span[0], node.span[1])
                if i in item_lines
            ]
            if ranges:
                node.md_lines = (
                    min(r[0] for r in ranges),
                    max(r[1] for r in ranges),
                )

    # ── Legacy-slug aliases: feed the makeup slug_map back so
    # old slugs (and the natural titles the model uses) resolve
    # at runtime (S3.2 → S3.1 backflow) ──────────────────────
    if alias_map_path and alias_map_path.is_file():
        amap = yaml.safe_load(
            alias_map_path.read_text(encoding="utf-8"),
        )
        by_id = {n.node_id: n for n in all_nodes}
        all_titles = {n.title for n in all_nodes}
        added = 0
        for old_slug, m in (amap or {}).items():
            node = by_id.get((m or {}).get("node_id") or "")
            # Skip aliases that now collide with ANY node's real
            # title (e.g. after an R6 promotion creates the very
            # node the alias used to stand in for) — the title
            # match must win unambiguously.
            if node and old_slug not in node.aliases \
                    and old_slug not in all_titles:
                node.aliases.append(old_slug)
                added += 1
        print(f"[index] legacy aliases added: {added}")

    # ── Summary reuse: stable node_ids make prior LLM output
    # transferable across rebuilds (no re-spend) ─────────────
    reused = 0
    if reuse_summaries_from and reuse_summaries_from.is_file():
        prior = ManualIndex.load_yaml(reuse_summaries_from)
        prior_map = {
            n.node_id: n.summary
            for n in prior.all_nodes() if n.summary.strip()
        }
        by_idx = {it.idx: it for it in items}
        for node in all_nodes:
            if node.node_id not in prior_map:
                continue
            section = " ".join(
                by_idx[i].text
                for i in range(node.span[0], node.span[1])
                if i in by_idx
            )
            # Language gate applies to reuse too — nonconforming
            # prior summaries (the 348 English-on-CJK batch) fall
            # through to regeneration.
            if summary_conforms(
                prior_map[node.node_id], section,
            ):
                node.summary = prior_map[node.node_id]
                reused += 1

    summary_stats = None
    if with_summaries:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            print(
                "[index] OPENROUTER_API_KEY missing",
                file=sys.stderr,
            )
            return False
        pending = [
            n for n in all_nodes if not n.summary.strip()
        ]
        summary_stats = enrich_summaries(
            pending, items, api_key, model,
        )

    index = ManualIndex(
        manual_id=manual_id,
        source={
            "content_file": content_md_path.name,
            "content_sha256": hashlib.sha256(
                content_md.encode("utf-8"),
            ).hexdigest(),
            "parser": "mineru-3.4.4/hybrid-engine "
                      "+ pymupdf-authority",
            "built_at": datetime.datetime.now(
                datetime.timezone.utc,
            ).isoformat(),
        },
        applicability=applicability or {
            "manufacturer": "Yamaha",
            "models": ["TRICITY155", "MWS150-A"],
        },
        vocab_version=vocab.vocab_version,
        tree=tree_result.roots,
        faults=faults,
    )

    result = validate(
        index, items, content_md, codes, vocab,
        tree_result.noise_item_idxs,
        require_summaries=with_summaries,
    )

    report_path = out_dir / "index_build_report.json"
    report = {
        "manual_id": manual_id,
        "publishable": result.passed and with_summaries,
        "gates": [
            {"gate": g.gate, "passed": g.passed,
             "detail": g.detail}
            for g in result.gates
        ],
        "tree": {
            "chapters": len(index.tree),
            "nodes": len(all_nodes),
            "synthesized_dtc_boundaries":
                tree_result.synthesized_boundaries,
            "noise_items": len(tree_result.noise_item_idxs),
            "unclassified_nodes":
                tree_result.unclassified_nodes,
        },
        "entities": {
            "swept_codes": len(codes),
            "cards": len(faults),
            "cards_with_isolate_ref": sum(
                1 for f in faults if f.isolate_ref
            ),
        },
        "summaries": (
            summary_stats.__dict__ if summary_stats else None
        ),
        "summaries_reused": reused,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"[index] nodes={len(all_nodes)} "
        f"chapters={len(index.tree)} "
        f"synthesized={tree_result.synthesized_boundaries} "
        f"codes={len(codes)} "
        f"cards_resolved="
        f"{sum(1 for f in faults if f.isolate_ref)}/{len(codes)} "
        f"unclassified={len(tree_result.unclassified_nodes)} "
        f"gates={'ALL-GREEN' if result.passed else 'FAILED'}"
    )
    for gate in result.gates:
        mark = "PASS" if gate.passed else "FAIL"
        print(f"[index]   {gate.gate}: {mark}"
              + (f" — {gate.detail}" if gate.detail else ""))

    if not result.passed:
        return False
    index.dump_yaml(out_dir / f"{manual_id}.index.yaml")
    return True


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build the manual index sidecar (Phase 2)",
    )
    parser.add_argument(
        "--mineru-dir", type=Path, required=True,
    )
    parser.add_argument(
        "--content-md", type=Path, required=True,
    )
    parser.add_argument("--manual-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--summaries", action="store_true", default=False,
    )
    parser.add_argument(
        "--model", default="deepseek/deepseek-chat",
    )
    parser.add_argument(
        "--item-lines", type=Path, default=None,
    )
    parser.add_argument(
        "--reuse-summaries-from", type=Path, default=None,
    )
    parser.add_argument(
        "--alias-map", type=Path, default=None,
    )
    parser.add_argument("--manufacturer", default=None)
    parser.add_argument(
        "--models", default=None,
        help="comma-separated vehicle models",
    )
    args = parser.parse_args()
    applicability = None
    if args.manufacturer and args.models:
        applicability = {
            "manufacturer": args.manufacturer,
            "models": [
                m.strip() for m in args.models.split(",")
            ],
        }
    ok = run_index_build(
        args.mineru_dir, args.content_md, args.manual_id,
        args.out,
        applicability=applicability,
        with_summaries=args.summaries,
        model=args.model,
        item_lines_path=args.item_lines,
        reuse_summaries_from=args.reuse_summaries_from,
        alias_map_path=args.alias_map,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

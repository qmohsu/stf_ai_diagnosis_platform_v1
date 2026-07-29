"""Golden "makeup" — asset-preserving anchor remap (S3.2, spec §8).

The 30 locked goldens' expert semantics are untouchable; only the
POSITIONAL anchors change:

- ``golden_citations[].slug``  → the covering node_id
- ``expected_recall_slugs[]``  → node_ids
- ``must_contain``             → verified literally against the v2
  content (misses reported for human confirmation, never edited
  silently)

Outputs (out dir): the remapped jsonl, ``slug_map.yaml`` (the
reviewable mapping table with per-slug method + confidence), and
``makeup_report.json``.  The original locked file is NEVER touched.

Usage::

    PYTHONPATH=. python -m manual_pipeline.golden_makeup \\
        --locked …/locked/mws150a.jsonl \\
        --index current_index.yaml \\
        --content v2_content.md \\
        --out out_makeup
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

_SLUG_KEEP = re.compile(r"[a-z0-9⺀-鿿豈-﫿]+")


def _norm(text: str) -> str:
    """Old-slug-compatible normalization (CJK+alnum, hyphens)."""
    return "-".join(_SLUG_KEEP.findall(text.lower()))


class _Index:
    """Flat view over the sidecar for mapping lookups."""

    def __init__(self, index_path: Path, content_path: Path):
        raw = yaml.safe_load(
            index_path.read_text(encoding="utf-8"),
        )
        self.nodes: List[dict] = []

        def walk(ns):
            for n in ns:
                self.nodes.append(n)
                walk(n.get("children") or [])

        walk(raw["tree"])
        self.content_lines = content_path.read_text(
            encoding="utf-8",
        ).split("\n")
        self.content = "\n".join(self.content_lines)

    def enclosing(self, line_idx: int) -> Optional[str]:
        best, width = None, None
        for n in self.nodes:
            ml = n.get("md_lines")
            if not ml:
                continue
            if ml[0] <= line_idx < ml[1]:
                w = ml[1] - ml[0]
                if width is None or w < width:
                    best, width = n["node_id"], w
        return best


def map_slug(
    old_slug: str,
    index: _Index,
    quote: str = "",
) -> Tuple[Optional[str], str]:
    """Map one legacy slug to a node_id.

    Strategy order (method string recorded for review):
      title-exact   normalized node title == the old slug
      title-sub     old slug's text contained in exactly one title
      quote-locate  the citation quote found in v2 content → its
                    enclosing node (quotes are content literals,
                    the most robust anchor when titles diverge)

    Returns:
        (node_id | None, method).
    """
    norm_slug = _norm(old_slug)
    exact = [
        n for n in index.nodes
        if _norm(n["title"]) == norm_slug
        or any(
            _norm(a) == norm_slug
            for a in (n.get("aliases") or [])
        )
    ]
    if len(exact) == 1:
        return exact[0]["node_id"], "title-exact"
    if len(exact) > 1:
        # Prefer the deepest (most specific) among duplicates.
        exact.sort(
            key=lambda n: (
                (n.get("md_lines") or [0, 10**9])[1]
                - (n.get("md_lines") or [0, 10**9])[0]
            ),
        )
        return exact[0]["node_id"], "title-exact-dedup"

    subs = [
        n for n in index.nodes
        if norm_slug and (
            norm_slug in _norm(n["title"])
            or any(
                norm_slug in _norm(a)
                for a in (n.get("aliases") or [])
            )
        )
    ]
    if len(subs) == 1:
        return subs[0]["node_id"], "title-sub"

    # Legacy dedupe suffix (``引擎規格-2``): the old -N ordinal is
    # meaningless in the new tree — retry on the base title.
    base = re.sub(r"-\d+$", "", old_slug)
    if base != old_slug:
        node_id, method = map_slug(base, index, quote)
        if node_id:
            return node_id, f"{method}+desuffixed"

    if quote:
        # Space-insensitive quote location: the v2 rendering drops
        # marker's spurious spaces (``綠色 / 紅色`` → ``綠色/紅色``).
        tight_q = re.sub(r"\s+", "", quote)
        for i, line in enumerate(index.content_lines):
            if quote in line or (
                tight_q and tight_q in re.sub(r"\s+", "", line)
            ):
                node = index.enclosing(i)
                if node:
                    return node, "quote-locate"

    # content-locate: the old slug's title exists as a BARE text
    # line (engine missed it as a title — the #186 unheaded-title
    # family), so no node carries it.  Map to the node enclosing
    # the bare-title occurrence: coarser granularity, but the
    # content is inside that node's slice.  Manual-TOC listing
    # lines (dotted leaders) are excluded.
    tight_slug = re.sub(r"[\s/－—-]+", "", old_slug)
    if tight_slug:
        for i, line in enumerate(index.content_lines):
            tight_line = re.sub(r"[\s/－—-]+", "", line)
            if tight_line != tight_slug:
                continue
            if re.search(r"\.{3,}", line):
                continue  # manual's own TOC page
            node = index.enclosing(i)
            if node:
                return node, "content-locate"
    return None, "UNRESOLVED"


def run_makeup(
    locked_path: Path,
    index_path: Path,
    content_path: Path,
    out_dir: Path,
) -> bool:
    """Remap all goldens; write artifacts; True when clean.

    "Clean" = every positional anchor resolved AND every
    must_contain string literally present in the v2 content.
    Semantic fields are copied verbatim (never edited).
    """
    index = _Index(index_path, content_path)
    goldens = [
        json.loads(l)
        for l in locked_path.read_text(encoding="utf-8")
            .splitlines()
        if l.strip()
    ]

    slug_map: Dict[str, dict] = {}
    report: List[dict] = []
    remapped: List[dict] = []
    clean = True

    for g in goldens:
        entry_report = {
            "id": g["id"],
            "slugs": [],
            "must_contain_missing": [],
        }
        new_g = json.loads(json.dumps(g, ensure_ascii=False))

        def _map(slug: str, quote: str = "") -> str:
            if slug in slug_map:
                return slug_map[slug]["node_id"] or slug
            node_id, method = map_slug(slug, index, quote)
            slug_map[slug] = {
                "node_id": node_id, "method": method,
            }
            return node_id or slug

        for cit in new_g.get("golden_citations") or []:
            old = cit.get("slug", "")
            new = _map(old, cit.get("quote", ""))
            entry_report["slugs"].append(
                {"old": old, "new": new,
                 "method": slug_map[old]["method"]},
            )
            cit["slug"] = new

        if new_g.get("expected_recall_slugs"):
            new_g["expected_recall_slugs"] = [
                _map(s) for s in new_g["expected_recall_slugs"]
            ]

        # must_contain semantics: for CONTENT-QUOTED strings the
        # literal must exist in the v2 content (the agent will
        # quote the new rendering); answer-shape expectations
        # (adversarial phrasing like 'no chain') are NOT manual
        # text and are skipped.  Whitespace variants get an
        # explicit proposed fix — applied to the remapped file
        # AND listed for human confirmation, never silent.
        fixed_mc: List[str] = []
        for needle in new_g.get("must_contain") or []:
            if needle in index.content:
                fixed_mc.append(needle)
                continue
            tight = re.sub(r"\s+", "", needle)
            variant = None
            if tight and re.search(r"[⺀-鿿豈-﫿]", needle):
                pattern = r"\s*".join(
                    re.escape(ch) for ch in tight
                )
                m = re.search(pattern, index.content)
                if m:
                    variant = m.group(0)
            if variant:
                fixed_mc.append(variant)
                entry_report.setdefault(
                    "must_contain_variant_fixes", [],
                ).append({"old": needle, "new": variant})
            elif re.search(r"[⺀-鿿豈-﫿]", needle):
                # CJK content literal genuinely absent → human.
                fixed_mc.append(needle)
                entry_report["must_contain_missing"].append(
                    needle,
                )
                clean = False
            else:
                # Answer-shape expectation (non-CJK): keep as-is,
                # not a content check.
                fixed_mc.append(needle)
        if new_g.get("must_contain"):
            new_g["must_contain"] = fixed_mc

        if any(
            s["method"] == "UNRESOLVED"
            for s in entry_report["slugs"]
        ):
            clean = False
        report.append(entry_report)
        remapped.append(new_g)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mws150a_indexed.jsonl").write_text(
        "\n".join(
            json.dumps(g, ensure_ascii=False) for g in remapped
        ) + "\n",
        encoding="utf-8",
    )
    (out_dir / "slug_map.yaml").write_text(
        yaml.safe_dump(slug_map, allow_unicode=True,
                       sort_keys=True),
        encoding="utf-8",
    )
    unresolved = [
        s for s, m in slug_map.items()
        if m["method"] == "UNRESOLVED"
    ]
    missing_total = sum(
        len(r["must_contain_missing"]) for r in report
    )
    (out_dir / "makeup_report.json").write_text(
        json.dumps({
            "clean": clean,
            "goldens": len(goldens),
            "distinct_slugs": len(slug_map),
            "unresolved_slugs": unresolved,
            "must_contain_missing_total": missing_total,
            "entries": report,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    methods: Dict[str, int] = {}
    for m in slug_map.values():
        methods[m["method"]] = methods.get(m["method"], 0) + 1
    print(
        f"[makeup] goldens={len(goldens)} "
        f"slugs={len(slug_map)} methods={methods} "
        f"must_contain_missing={missing_total} "
        f"clean={clean}"
    )
    return clean


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    clean = run_makeup(
        args.locked, args.index, args.content, args.out,
    )
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())

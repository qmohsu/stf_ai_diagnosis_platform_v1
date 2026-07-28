"""Runtime consumption of the manual index sidecar (HARNESS-30
Phase 3, S3.1 dual-track).

When a validated ``<manual_id>.index.yaml`` (built by the offline
``manual_pipeline`` and gated by I1–I8) is present next to its v2
content markdown, the manual tools serve navigation from the INDEX
— labeled logical tree, complete DTC entity cards, node_id
addressing with md-line content slicing — instead of runtime
heading-tree parsing.  Without a sidecar (or with the track forced
off) behaviour is byte-identical to the legacy path.

Deployment layout (inside the manuals volume)::

    MWS-150-A/
      <manual_id>.md            # legacy marker content (untouched)
      <manual_id>/…             # (legacy images etc.)
      index/
        <manual_id>.index.yaml  # the sidecar
        <manual_id>.md          # v2 content it hashes
        images/…                # v2 images

Track control: env ``MANUAL_INDEX_TRACK`` — ``auto`` (default:
use the sidecar when present) or ``off`` (force legacy; used as
the A/B lane switch in the Phase-3 eval).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog
import yaml

from app.config import settings

logger = structlog.get_logger(__name__)

_MANUAL_DIR = Path(settings.manual_storage_path)
_TRACK_ENV = "MANUAL_INDEX_TRACK"

_SLUG_KEEP_RE = None  # lazily built in _slugify


def track_enabled() -> bool:
    """True unless the operator forced the legacy track."""
    return os.environ.get(_TRACK_ENV, "auto").lower() != "off"


@dataclass
class RuntimeNode:
    """One index node, flattened for runtime lookup."""

    node_id: str
    title: str
    aliases: List[str]
    node_type: str
    subsystem: str
    page_range: Tuple[int, int]
    md_lines: Optional[Tuple[int, int]]
    summary: str
    depth: int
    children: List["RuntimeNode"] = field(default_factory=list)


class RuntimeIndex:
    """Parsed sidecar + content, ready to serve the tools."""

    def __init__(self, sidecar_path: Path) -> None:
        raw = yaml.safe_load(
            sidecar_path.read_text(encoding="utf-8"),
        )
        self.manual_id: str = raw["manual_id"]
        self.dir = sidecar_path.parent
        content_file = raw["source"]["content_file"]
        self._content_lines = (
            (self.dir / content_file)
            .read_text(encoding="utf-8")
            .split("\n")
        )
        self.faults: List[dict] = raw.get("faults", [])
        self.roots: List[RuntimeNode] = [
            self._parse_node(n, 0) for n in raw.get("tree", [])
        ]
        self.nodes: List[RuntimeNode] = []
        for root in self.roots:
            self._flatten(root)
        self._by_id: Dict[str, RuntimeNode] = {
            n.node_id: n for n in self.nodes
        }

    def _parse_node(self, raw: dict, depth: int) -> RuntimeNode:
        node = RuntimeNode(
            node_id=raw["node_id"],
            title=raw["title"],
            aliases=list(raw.get("aliases") or []),
            node_type=raw["node_type"],
            subsystem=raw["subsystem"],
            page_range=tuple(raw.get("page_range") or (0, 0)),
            md_lines=(
                tuple(raw["md_lines"])
                if raw.get("md_lines") else None
            ),
            summary=raw.get("summary") or "",
            depth=depth,
            children=[
                self._parse_node(c, depth + 1)
                for c in raw.get("children") or []
            ],
        )
        return node

    def _flatten(self, node: RuntimeNode) -> None:
        self.nodes.append(node)
        for child in node.children:
            self._flatten(child)

    # ── Resolution ───────────────────────────────────────────

    def resolve(
        self, query: str,
    ) -> Tuple[Optional[RuntimeNode], List[RuntimeNode]]:
        """Resolve a section query to a node.

        Strategy order: exact node_id → exact title/alias →
        slug-normalized title → substring over titles+aliases.
        Substring hits are returned as candidates when ambiguous
        (the caller lists them instead of silently picking the
        first — the P2.2 fix).

        Returns:
            (node, []) on a unique match; (None, candidates) on
            ambiguity; (None, []) on a miss.
        """
        q = query.strip()
        if q in self._by_id:
            return self._by_id[q], []
        norm_q = _slugify(q)
        exact: List[RuntimeNode] = []
        for node in self.nodes:
            names = [node.title, *node.aliases]
            if any(q == n for n in names):
                exact.append(node)
            elif any(norm_q == _slugify(n) for n in names):
                exact.append(node)
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact
        subs = [
            node for node in self.nodes
            if any(
                q in n or (norm_q and norm_q in _slugify(n))
                for n in [node.title, *node.aliases]
            )
        ]
        if len(subs) == 1:
            return subs[0], []
        return None, subs

    # ── Rendering ────────────────────────────────────────────

    def section_text(self, node: RuntimeNode) -> Optional[str]:
        """Slice the v2 content for a node (children included by
        construction — md_lines spans the whole subtree)."""
        if node.md_lines is None:
            return None
        start, end = node.md_lines
        return "\n".join(self._content_lines[start:end])

    def toc_text(self, max_depth: int = 3) -> str:
        """Labeled tree + complete DTC quick index + guard."""
        lines: List[str] = [
            "(index-driven TOC — every entry shows "
            "[node_id] (subsystem/type); pass a node_id to "
            "read_manual_section)",
        ]

        def render(node: RuntimeNode, indent: int) -> None:
            pad = "  " * indent
            lines.append(
                f"{pad}- {node.title}  [{node.node_id}] "
                f"({node.subsystem}/{node.node_type})"
            )
            if node.summary and indent < 2:
                lines.append(f"{pad}  {node.summary[:110]}")
            if indent + 1 < max_depth:
                for child in node.children:
                    render(child, indent + 1)
            elif node.children:
                lines.append(
                    f"{pad}  …{len(node.children)} nested "
                    f"sections (read the parent node_id to get "
                    f"them all)"
                )

        for root in self.roots:
            render(root, 0)

        if self.faults:
            lines.append("")
            lines.append(
                "DTC Quick Index (complete — one card per code "
                "in this manual; pass the node_id to "
                "read_manual_section):"
            )
            lines.append(
                "| DTC | symptom | fail-safe | node_id |"
            )
            lines.append("|-----|---------|-----------|---------|")
            for fault in self.faults:
                lines.append(
                    f"| {fault['code']} "
                    f"| {fault.get('symptom', '')} "
                    f"| {fault.get('fail_safe', '')} "
                    f"| {fault.get('isolate_ref', '')} |"
                )
            lines.append(
                "\nEvery code above IS in the manual. For "
                "anything not listed, verify absence with "
                "search_manual_text before claiming the manual "
                "lacks it."
            )
        return "\n".join(lines)

    def enclosing_node_id(self, line_idx: int) -> str:
        """Deepest node whose md_lines contain a content line —
        used by search_manual_text hit attribution."""
        best = ""
        best_width = None
        for node in self.nodes:
            if node.md_lines is None:
                continue
            s, e = node.md_lines
            if s <= line_idx < e:
                width = e - s
                if best_width is None or width < best_width:
                    best, best_width = node.node_id, width
        return best or "(no enclosing node)"

    @property
    def content_lines(self) -> List[str]:
        """The v2 content, line-split (for literal search)."""
        return self._content_lines


def _slugify(text: str) -> str:
    """Loose normalization for query matching (independent of
    the build-side slugger; used only for comparisons)."""
    global _SLUG_KEEP_RE
    if _SLUG_KEEP_RE is None:
        import re
        _SLUG_KEEP_RE = re.compile(r"[a-z0-9⺀-鿿豈-﫿]+")
    return "-".join(_SLUG_KEEP_RE.findall(text.lower()))


# ── Sidecar discovery + cache ─────────────────────────────────────

_cache: Dict[str, Tuple[float, RuntimeIndex]] = {}


def load_runtime_index(
    manual_id: str,
) -> Optional[RuntimeIndex]:
    """Find + parse the sidecar for a manual (mtime-cached).

    Returns None when the track is off or no sidecar exists —
    the tools then fall back to the legacy path.
    """
    if not track_enabled():
        return None
    if not _MANUAL_DIR.is_dir():
        return None
    sidecar = None
    for candidate in _MANUAL_DIR.rglob(
        f"{manual_id}.index.yaml",
    ):
        sidecar = candidate
        break
    if sidecar is None:
        return None
    mtime = sidecar.stat().st_mtime
    cached = _cache.get(manual_id)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        index = RuntimeIndex(sidecar)
    except Exception as exc:
        logger.error(
            "manual_index_load_failed",
            manual_id=manual_id,
            path=str(sidecar),
            error=str(exc),
        )
        return None
    _cache[manual_id] = (mtime, index)
    logger.info(
        "manual_index_loaded",
        manual_id=manual_id,
        nodes=len(index.nodes),
        faults=len(index.faults),
    )
    return index

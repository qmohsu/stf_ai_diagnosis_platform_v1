"""Index sidecar schema + vocab loader (spec §3, Phase 2 S2.1).

The ``index.yaml`` artifact is Pydantic-validated on write AND on
read.  Spans reference the normalized item stream (§3.4), never
markdown headings — the storage/index decoupling in schema form.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

_VOCAB_PATH = Path(__file__).parent / "vocab.yaml"

_SLUG_KEEP = re.compile(r"[a-z0-9⺀-鿿豈-﫿]+")
_SLUG_MAX = 48

_TYPE_ABBREV = {
    "specification": "spec",
    "operation": "op",
    "remove_install": "ri",
    "inspection": "insp",
    "fault_isolation": "fault",
    "troubleshooting_tree": "tree",
    "wiring": "wire",
    "parts": "parts",
    "index": "idx",
    "description": "desc",
}


class Vocab(BaseModel):
    """Loaded controlled vocabulary (spec §3.5)."""

    vocab_version: str
    subsystems: Dict[str, List[str]]
    node_types: Dict[str, List[str]]
    troubleshooting_cause_groups: List[str]

    @classmethod
    def load(cls, path: Path = _VOCAB_PATH) -> "Vocab":
        """Read and validate vocab.yaml."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            vocab_version=str(raw["vocab_version"]),
            subsystems={
                k: v.get("aliases", [])
                for k, v in raw["subsystems"].items()
            },
            node_types={
                k: v.get("patterns", [])
                for k, v in raw["node_types"].items()
            },
            troubleshooting_cause_groups=raw[
                "troubleshooting_cause_groups"
            ],
        )

    def subsystem_for(self, text: str) -> Optional[str]:
        """Best subsystem for a title/header text via aliases.

        Longest-alias match wins so ``點火系統`` beats ``引擎``
        when both appear.
        """
        best: Tuple[int, Optional[str]] = (0, None)
        for name, aliases in self.subsystems.items():
            for alias in aliases:
                if alias and alias in text:
                    if len(alias) > best[0]:
                        best = (len(alias), name)
        return best[1]

    def node_type_for(self, title: str) -> str:
        """Node type for a title via pattern lists ('description'
        when nothing matches)."""
        for ntype, patterns in self.node_types.items():
            for pat in patterns:
                if pat and pat in title:
                    return ntype
        return "description"


def slugify(title: str) -> str:
    """Semantic slug: lowercase ASCII + CJK, hyphen-joined."""
    parts = _SLUG_KEEP.findall(title.lower())
    slug = "-".join(parts)[:_SLUG_MAX].strip("-")
    return slug or "untitled"


def make_node_id(
    subsystem: str, node_type: str, title: str,
) -> str:
    """Stable node id: ``{subsystem}-{type-abbrev}-{slug}``.

    Collision suffixes (``-2`` …) are assigned by the tree
    builder in document order.
    """
    return (
        f"{subsystem}-{_TYPE_ABBREV[node_type]}-{slugify(title)}"
    )


class IndexNode(BaseModel):
    """One logical section (spec §3.2)."""

    node_id: str
    title: str
    aliases: List[str] = Field(default_factory=list)
    node_type: str
    subsystem: str
    span: Tuple[int, int]  # [start_item, end_item) in the stream
    page_range: Tuple[int, int]
    md_lines: Optional[Tuple[int, int]] = None
    """[start, end) line range in the content markdown — the
    runtime's content-slicing anchor (stamped from item_lines)."""
    summary: str = ""      # filled by S2.5 enrichment
    children: List["IndexNode"] = Field(default_factory=list)

    def walk(self):
        """Yield self and all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()


class FaultEntity(BaseModel):
    """One DTC card (spec §3.3, S1000D fault-model shape)."""

    code: str
    item: str = ""
    symptom: str = ""
    fail_safe: str = ""
    detect_ref: Optional[str] = None
    isolate_ref: Optional[str] = None
    related_refs: List[str] = Field(default_factory=list)


class ManualIndex(BaseModel):
    """The sidecar artifact (spec §3.1)."""

    spec_version: str = "0.3"
    manual_id: str
    source: Dict[str, str]
    applicability: Dict[str, object]
    vocab_version: str
    tree: List[IndexNode]
    faults: List[FaultEntity]

    def all_nodes(self) -> List[IndexNode]:
        """Flat list of every node in document order."""
        out: List[IndexNode] = []
        for root in self.tree:
            out.extend(root.walk())
        return out

    def dump_yaml(self, path: Path) -> None:
        """Write the validated artifact."""
        path.write_text(
            yaml.safe_dump(
                self.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load_yaml(cls, path: Path) -> "ManualIndex":
        """Read + re-validate an artifact."""
        return cls.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8")),
        )

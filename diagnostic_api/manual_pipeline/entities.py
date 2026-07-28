"""FaultEntity extraction — DTC cards (spec §3.3, S2.4).

Coverage is guaranteed by construction + gate: the code list comes
from an independent regex sweep of the CONTENT (not the tree), and
I3 fails the build if any swept code lacks a resolving card.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from manual_pipeline.index_schema import (
    FaultEntity,
    IndexNode,
)
from manual_pipeline.stream import ItemKind, NormalizedItem

_DTC_RE = re.compile(r"\b([PCBU]\d[0-9A-F]{3})\b", re.IGNORECASE)
# The self-diagnostic function table carries symptom + fail-safe
# columns; its rows are the card payload source.
_SELF_DIAG_SIG = "故障防護系統"
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def sweep_codes(content_md: str) -> List[str]:
    """Independent full-content DTC sweep (the I3 reference set)."""
    return sorted({
        m.upper() for m in _DTC_RE.findall(content_md)
    })


def _clean_cell(cell: str) -> str:
    return _TAG_RE.sub("", cell).replace("\n", " ").strip()


def _self_diag_rows(
    items: List[NormalizedItem],
) -> Dict[str, List[str]]:
    """code → cleaned row cells from the self-diag table html."""
    rows: Dict[str, List[str]] = {}
    for it in items:
        if it.kind != ItemKind.TABLE or _SELF_DIAG_SIG not in it.html:
            continue
        for row_html in _ROW_RE.findall(it.html):
            cells = [
                _clean_cell(c) for c in _CELL_RE.findall(row_html)
            ]
            if not cells:
                continue
            m = _DTC_RE.fullmatch(cells[0].strip())
            if m:
                rows[m.group(1).upper()] = cells
    return rows


def build_fault_entities(
    codes: List[str],
    items: List[NormalizedItem],
    all_nodes: List[IndexNode],
) -> List[FaultEntity]:
    """Build one card per swept code.

    isolate_ref: the deepest node whose span contains a
    ``故障代碼編號 <code>`` occurrence (R2 guarantees such a node
    exists for every code with a procedure block).
    detect_ref: the node holding the self-diagnostic table.

    Args:
        codes: Swept code list (independent reference set).
        items: Normalized stream.
        all_nodes: Flat node list in document order.

    Returns:
        One FaultEntity per code (fields best-effort; I3 enforces
        isolate_ref resolution for codes with procedure blocks).
    """
    rows = _self_diag_rows(items)

    def deepest_containing(idx: int) -> Optional[IndexNode]:
        best: Optional[IndexNode] = None
        best_width = None
        for node in all_nodes:
            s, e = node.span
            if s <= idx < e:
                width = e - s
                if best_width is None or width < best_width:
                    best, best_width = node, width
        return best

    # The AGGREGATE self-diag table (all codes, one row each) is
    # the detect target — per-code ch.8 blocks also carry the
    # 故障防護系統 signature, so pick the table with the most
    # code-first rows rather than the first match.
    detect_node: Optional[IndexNode] = None
    best_rows = 0
    for it in items:
        if it.kind != ItemKind.TABLE or _SELF_DIAG_SIG not in it.html:
            continue
        n_rows = sum(
            1 for row_html in _ROW_RE.findall(it.html)
            for cells in [_CELL_RE.findall(row_html)]
            if cells and _DTC_RE.fullmatch(
                _clean_cell(cells[0]).strip(),
            )
        )
        if n_rows > best_rows:
            best_rows = n_rows
            detect_node = deepest_containing(it.idx)

    # code → item idx of its 故障代碼編號 block.
    block_idx: Dict[str, int] = {}
    boundary_re = re.compile(
        r"故障代碼編號\s*([PCBU]\d[0-9A-F]{3})", re.IGNORECASE,
    )
    for it in items:
        payload = f"{it.text} {it.html}"
        for code in boundary_re.findall(payload):
            block_idx.setdefault(code.upper(), it.idx)

    entities: List[FaultEntity] = []
    for code in codes:
        row = rows.get(code, [])
        isolate = (
            deepest_containing(block_idx[code])
            if code in block_idx else None
        )
        # Codes with no per-code procedure block (block_idx miss)
        # fall back to the self-diag table node: it IS the
        # manual's isolation information for them, and I3
        # requires every card to resolve somewhere real.
        isolate_ref = (
            isolate.node_id if isolate
            else (detect_node.node_id if detect_node else None)
        )
        entities.append(FaultEntity(
            code=code,
            item=row[1] if len(row) > 1 else "",
            symptom=row[3] if len(row) > 3 else "",
            fail_safe=row[4] if len(row) > 4 else "",
            detect_ref=(
                detect_node.node_id if detect_node else None
            ),
            isolate_ref=isolate_ref,
        ))
    return entities

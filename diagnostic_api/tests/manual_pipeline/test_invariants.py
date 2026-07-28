"""Mutation tests for the I1–I8 gates (spec §6.2).

Validator-of-the-validator: start from a known-good index, inject
each defect class, and assert the corresponding gate goes red.  A
gate that misses its mutation is a validator bug — fix the
validator first (ratchet protocol).
"""

from __future__ import annotations

import hashlib
from typing import List, Tuple

import pytest

from manual_pipeline.index_schema import (
    FaultEntity,
    IndexNode,
    ManualIndex,
    Vocab,
)
from manual_pipeline.invariants import validate
from manual_pipeline.stream import ItemKind, NormalizedItem

CONTENT_MD = (
    "---\nvehicle_model: MINI\n---\n"
    "## 煞車系統\n"
    "煞車卡鉗的檢查步驟,扭力 10 Nm,行程 20 mm。\n"
    "故障代碼編號 P0335 曲軸位置感知器排查程序,共六步。\n"
)
SHA = hashlib.sha256(CONTENT_MD.encode("utf-8")).hexdigest()


def _items() -> List[NormalizedItem]:
    return [
        NormalizedItem(
            idx=0, page=1, kind=ItemKind.TITLE_CANDIDATE,
            text="煞車系統",
        ),
        NormalizedItem(
            idx=1, page=1, kind=ItemKind.PARA,
            text="煞車卡鉗的檢查步驟,扭力 10 Nm,行程 20 mm。"
                 "內容足夠長以通過空殼檢查的五十字符門檻要求。",
        ),
        NormalizedItem(
            idx=2, page=2, kind=ItemKind.PARA,
            text="故障代碼編號 P0335 曲軸位置感知器排查程序,"
                 "共六步,包含接頭檢查、電線導通測量、感知器"
                 "安裝狀況與 ECU 更換判定等完整步驟。",
        ),
    ]


def _good_index() -> ManualIndex:
    fault_node = IndexNode(
        node_id="electrical-fault-p0335",
        title="故障代碼編號 P0335",
        node_type="fault_isolation",
        subsystem="electrical",
        span=(2, 3),
        page_range=(2, 2),
        summary="P0335 排查程序。",
    )
    chapter = IndexNode(
        node_id="brakes-desc-煞車系統",
        title="煞車系統",
        node_type="description",
        subsystem="brakes",
        span=(0, 3),
        page_range=(1, 2),
        summary="煞車章節。",
        children=[
            IndexNode(
                node_id="brakes-insp-煞車卡鉗的檢查",
                title="煞車卡鉗的檢查",
                node_type="inspection",
                subsystem="brakes",
                span=(0, 2),
                page_range=(1, 1),
                summary="卡鉗檢查,扭力 10 Nm。",
            ),
            fault_node,
        ],
    )
    return ManualIndex(
        manual_id="mini",
        source={
            "content_file": "mini.md",
            "content_sha256": SHA,
            "parser": "test",
            "built_at": "2026-07-23T00:00:00Z",
        },
        applicability={"manufacturer": "T", "models": ["MINI"]},
        vocab_version="1",
        tree=[chapter],
        faults=[FaultEntity(
            code="P0335",
            isolate_ref="electrical-fault-p0335",
        )],
    )


def _run(index: ManualIndex, **kw):
    return validate(
        index, _items(), CONTENT_MD, ["P0335"], Vocab.load(),
        noise_item_idxs=set(), require_summaries=True, **kw,
    )


def _gate(result, name: str):
    return next(g for g in result.gates if g.gate.startswith(name))


class TestGoodIndexPasses:
    """The unmutated fixture is all-green (mutation baseline)."""

    def test_all_gates_green(self) -> None:
        """Sanity: every gate passes on the good index."""
        result = _run(_good_index())
        assert result.passed, [
            (g.gate, g.detail) for g in result.failures()
        ]


class TestMutations:
    """Each injected defect class must trip exactly its gate."""

    def test_i1_catches_uncovered_item(self) -> None:
        """Shrinking the chapter span orphans item 2."""
        idx = _good_index()
        idx.tree[0].span = (0, 2)
        idx.tree[0].children[1].span = (2, 2)
        assert not _gate(_run(idx), "I1").passed

    def test_i1_catches_sibling_overlap(self) -> None:
        """Two children claiming the same item overlap."""
        idx = _good_index()
        idx.tree[0].children[0].span = (0, 3)
        assert not _gate(_run(idx), "I1").passed

    def test_i2_catches_empty_shell(self) -> None:
        """A leaf spanning zero content chars is a shell."""
        idx = _good_index()
        idx.tree[0].children[0].span = (0, 1)  # title only
        # keep tiling valid: sibling takes over item 1.
        idx.tree[0].children[1].span = (1, 3)
        assert not _gate(_run(idx), "I2").passed

    def test_i3_catches_missing_card(self) -> None:
        """A swept code without a FaultEntity fails."""
        idx = _good_index()
        idx.faults = []
        assert not _gate(_run(idx), "I3").passed

    def test_i4_catches_duplicate_ids(self) -> None:
        """Two nodes sharing a node_id fail."""
        idx = _good_index()
        idx.tree[0].children[0].node_id = (
            "electrical-fault-p0335"
        )
        assert not _gate(_run(idx), "I4").passed

    def test_i4_catches_dangling_ref(self) -> None:
        """A fault ref to a nonexistent node fails."""
        idx = _good_index()
        idx.faults[0].detect_ref = "ghost-node"
        assert not _gate(_run(idx), "I4").passed

    def test_i5_catches_junk_title(self) -> None:
        """A banner-shaped node title fails."""
        idx = _good_index()
        idx.tree[0].children[0].title = "警 告 EWA13120"
        assert not _gate(_run(idx), "I5").passed

    def test_i6_catches_off_vocab_label(self) -> None:
        """A subsystem outside the menu fails."""
        idx = _good_index()
        idx.tree[0].subsystem = "warp-drive"
        assert not _gate(_run(idx), "I6").passed

    def test_i6_catches_missing_summary(self) -> None:
        """Empty summary fails when summaries are required."""
        idx = _good_index()
        idx.tree[0].children[0].summary = ""
        assert not _gate(_run(idx), "I6").passed

    def test_i7_catches_content_drift(self) -> None:
        """A stale content hash fails."""
        idx = _good_index()
        idx.source["content_sha256"] = "0" * 64
        assert not _gate(_run(idx), "I7").passed

    def test_i8_catches_noise_flood(self) -> None:
        """Noise ratio above budget fails."""
        result = validate(
            _good_index(), _items(), CONTENT_MD, ["P0335"],
            Vocab.load(), noise_item_idxs={0, 1},
            require_summaries=True,
        )
        assert not _gate(result, "I8").passed

    def test_unclassified_is_legal(self) -> None:
        """The escape hatch is vocab-legal (labels may fail,
        coverage may not — spec §3.5)."""
        idx = _good_index()
        idx.tree[0].children[0].subsystem = "unclassified"
        assert _gate(_run(idx), "I6").passed

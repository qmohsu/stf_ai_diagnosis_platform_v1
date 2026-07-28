"""Unit tests for repair rules R1–R5 (tree_builder) + entities."""

from __future__ import annotations

from typing import List

from manual_pipeline.entities import (
    build_fault_entities,
    sweep_codes,
)
from manual_pipeline.index_schema import Vocab
from manual_pipeline.stream import ItemKind, NormalizedItem
from manual_pipeline.tree_builder import (
    build_tree,
    is_noise_title,
)


def _item(idx, page, kind, text="", html=""):
    return NormalizedItem(
        idx=idx, page=page, kind=kind, text=text, html=html,
    )


class TestR1NoiseTitles:
    """R1: banners/flowchart/steps/sentences are never nodes."""

    def test_noise_families(self) -> None:
        """All measured junk families are rejected."""
        for junk in (
            "注 意", "警 告 EWA13120", "註", "OK ↓", "OK↓",
            "▲ ▲ ▲▲▲", "4. 檢查:",
            "• 排氣管螺栓",
            "車輛需支撐穩固,不可有傾倒的危險。",
        ):
            assert is_noise_title(junk), junk

    def test_real_titles_survive(self) -> None:
        """Genuine section titles pass."""
        for real in (
            "煞車卡鉗的拆卸", "自我診斷功能表",
            "故障代碼編號 P0335", "引擎規格",
        ):
            assert not is_noise_title(real), real


class TestTreeBuilding:
    """R2/R3/R4 on a synthetic troubleshooting manual."""

    def _stream(self) -> List[NormalizedItem]:
        return [
            # p1: chapter 規格 via page header.
            _item(0, 1, ItemKind.PAGE_HEADER, "規格"),
            _item(1, 1, ItemKind.TITLE_CANDIDATE, "引擎規格"),
            _item(2, 1, ItemKind.PARA,
                  "排氣量 155 cc,壓縮比 10.5:1,內容足夠長"
                  "以通過最小字符門檻的規格說明文字。"),
            # p2: troubleshooting chapter.
            _item(3, 2, ItemKind.PAGE_HEADER, "故障排除"),
            _item(4, 2, ItemKind.TITLE_CANDIDATE,
                  "無法起動 / 起動困難"),
            _item(5, 2, ItemKind.PARA,
                  "症狀說明:起動馬達運轉但引擎無法發動,"
                  "以下按引擎、汽油、電裝各系統列出全部"
                  "可能原因與檢查入口,供技師逐一排查。"),
            _item(6, 2, ItemKind.TITLE_CANDIDATE, "引擎"),
            _item(7, 2, ItemKind.PARA,
                  "汽缸壓縮不足、汽門間隙不正確、汽缸頭墊片"
                  "損壞、活塞環磨耗、汽門正時不正確等機械性"
                  "原因的完整排查清單與對應檢查程序在此。"),
            _item(8, 2, ItemKind.TITLE_CANDIDATE, "電裝系統"),
            _item(9, 2, ItemKind.PARA,
                  "點火系統故障、曲軸位置感知器故障、ECU 供電"
                  "異常、主開關接觸不良、保險絲熔斷等電氣性"
                  "原因的完整排查清單與對應檢查程序在此。"),
            # p3: orphan DTC block (no title) — R2 target.
            _item(10, 3, ItemKind.PAGE_HEADER, "故障排除"),
            _item(11, 3, ItemKind.TABLE,
                  html="<table><tr><td>故障代碼編號 P0335"
                       "</td><td>曲軸位置感知器排查六步程序,"
                       "接頭、導通、安裝、轉子、感知器、ECU。"
                       "</td></tr></table>"),
        ]

    def test_r4_chapters_from_headers(self) -> None:
        """Top level follows the running headers."""
        result = build_tree(self._stream(), Vocab.load(), 3)
        titles = [r.title for r in result.roots]
        assert titles == ["規格", "故障排除"]

    def test_r3_cause_groups_nest_under_symptom(self) -> None:
        """引擎/電裝系統 become children of 無法起動."""
        result = build_tree(self._stream(), Vocab.load(), 3)
        trouble = result.roots[1]
        symptom = next(
            c for c in trouble.children
            if "無法起動" in c.title
        )
        child_titles = [c.title for c in symptom.children]
        assert "引擎" in child_titles
        assert "電裝系統" in child_titles

    def test_r2_synthesizes_dtc_node(self) -> None:
        """The titleless P0335 table gets its own node."""
        result = build_tree(self._stream(), Vocab.load(), 3)
        all_nodes = [
            n for r in result.roots for n in r.walk()
        ]
        dtc = [n for n in all_nodes if "P0335" in n.title]
        assert len(dtc) == 1
        assert dtc[0].node_type == "fault_isolation"
        assert result.synthesized_boundaries == 1

    def test_spans_tile_the_stream(self) -> None:
        """Root spans cover every item exactly once."""
        stream = self._stream()
        result = build_tree(stream, Vocab.load(), 3)
        covered = set()
        for root in result.roots:
            span = range(root.span[0], root.span[1])
            assert not (covered & set(span))
            covered.update(span)
        assert covered == set(range(len(stream)))


class TestEntities:
    """Fault-card extraction against the synthetic stream."""

    def test_card_resolves_isolate_ref(self) -> None:
        """P0335's card points at its synthesized node."""
        stream = TestTreeBuilding()._stream()
        result = build_tree(stream, Vocab.load(), 3)
        all_nodes = [
            n for r in result.roots for n in r.walk()
        ]
        codes = sweep_codes("故障代碼編號 P0335 …")
        cards = build_fault_entities(codes, stream, all_nodes)
        assert cards[0].code == "P0335"
        assert cards[0].isolate_ref is not None
        assert "p0335" in cards[0].isolate_ref

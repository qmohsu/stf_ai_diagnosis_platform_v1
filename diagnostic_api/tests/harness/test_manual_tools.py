"""Tests for manual filesystem navigation tool handlers.

Tests list_manuals, get_manual_toc, and read_manual_section
handlers with mocked filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from app.harness_tools.manual_tools import (
    get_manual_toc,
    list_manuals,
    read_manual_section,
    search_manual_text,
)


# ── Sample data ───────────────────────────────────────────────────


SAMPLE_MANUAL = """\
---
source_pdf: MWS150A_Service_Manual.pdf
vehicle_model: MWS-150-A
language: zh-CN
translated: true
exported_at: "2026-03-30T12:00:00Z"
page_count: 415
section_count: 3
---

# MWS-150-A Service Manual

## Chapter 1: General Information

### 1.1 Specifications

| Spec | Value |
|------|-------|
| Displacement | 155 cc |

## Chapter 3: Fuel System

### 3.1 Fuel System Overview

The fuel system has a tank and pump.

### 3.2 Fuel System Troubleshooting

![Fuel injector](images/MWS150A_Service_Manual/p045-1.png)

*Vision description: Exploded view of injector.*

#### DTC: P0171 — System Too Lean

Check intake manifold for vacuum leaks.

## Appendix: DTC Index

| DTC | Description | Section |
|-----|-------------|---------|
| P0171 | System Too Lean | 3.2 Fuel System |
"""

SECOND_MANUAL = """\
---
source_pdf: STF850_Workshop.pdf
vehicle_model: STF-850
language: en
page_count: 200
section_count: 2
---

# STF-850 Workshop Manual

## Chapter 1: Engine

Basic engine information.
"""

# Minimal 1x1 PNG.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDAT\x08\xd7c\xf8\x0f\x00\x00\x01\x01"
    b"\x00\x05\x18\xd8N\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def manual_dir(tmp_path: Path) -> Path:
    """Create a temp manual directory with sample manuals."""
    # Write manual files.
    (tmp_path / "MWS150A_Service_Manual.md").write_text(
        SAMPLE_MANUAL, encoding="utf-8",
    )
    (tmp_path / "STF850_Workshop.md").write_text(
        SECOND_MANUAL, encoding="utf-8",
    )
    # Create image file.
    img_dir = (
        tmp_path / "images" / "MWS150A_Service_Manual"
    )
    img_dir.mkdir(parents=True)
    (img_dir / "p045-1.png").write_bytes(TINY_PNG)
    # Create dirs that should be skipped.
    (tmp_path / "uploads").mkdir()
    (tmp_path / ".queue").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _mock_manual_dir(manual_dir: Path):
    """Patch _MANUAL_DIR to point at the temp directory."""
    with patch(
        "app.harness_tools.manual_tools._MANUAL_DIR",
        manual_dir,
    ):
        yield


# ── list_manuals ──────────────────────────────────────────────────


class TestListManuals:
    """Tests for list_manuals handler."""

    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        """Lists all available manuals."""
        result = await list_manuals({})
        assert "MWS150A_Service_Manual" in result
        assert "STF850_Workshop" in result
        assert "Available manuals (2)" in result

    @pytest.mark.asyncio
    async def test_filter_by_model(self) -> None:
        """Filters by vehicle_model."""
        result = await list_manuals(
            {"vehicle_model": "MWS-150-A"},
        )
        assert "MWS150A_Service_Manual" in result
        assert "STF850_Workshop" not in result

    @pytest.mark.asyncio
    async def test_filter_case_insensitive(self) -> None:
        """Vehicle model filter is case-insensitive."""
        result = await list_manuals(
            {"vehicle_model": "mws-150-a"},
        )
        assert "MWS150A_Service_Manual" in result

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        """Returns helpful message when no match."""
        result = await list_manuals(
            {"vehicle_model": "NONEXISTENT"},
        )
        assert "No manuals found" in result
        assert "without a filter" in result

    @pytest.mark.asyncio
    async def test_canonical_name_and_match_note(
        self, tmp_path: Path,
    ) -> None:
        """Canonical name + honest match-or-refuse note (HARNESS-25)."""
        d = tmp_path / "vault"
        d.mkdir()
        (d / "hiace.md").write_text(
            "---\nmanufacturer: Toyota\nvehicle_model: Hiace\n"
            "page_count: 100\nsection_count: 5\n---\n\n# Body\n",
            encoding="utf-8",
        )
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR", d,
        ):
            result = await list_manuals({})
        assert 'vehicle="Toyota Hiace"' in result
        # Honest match-or-refuse guidance is present.
        assert (
            "no service manual is available for this vehicle"
            in result
        )
        assert "do NOT adopt" in result

    @pytest.mark.asyncio
    async def test_filter_by_manufacturer(
        self, tmp_path: Path,
    ) -> None:
        """Filter matches on manufacturer, not just model (HARNESS-25)."""
        d = tmp_path / "vault2"
        d.mkdir()
        (d / "hiace.md").write_text(
            "---\nmanufacturer: Toyota\nvehicle_model: Hiace\n"
            "---\n\n# B\n",
            encoding="utf-8",
        )
        (d / "tricity.md").write_text(
            "---\nmanufacturer: Yamaha\nvehicle_model: TRICITY155\n"
            "---\n\n# B\n",
            encoding="utf-8",
        )
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR", d,
        ):
            result = await list_manuals({"vehicle_model": "Toyota"})
        assert "hiace" in result
        assert "tricity" not in result

    @pytest.mark.asyncio
    async def test_renders_factory_code(
        self, tmp_path: Path,
    ) -> None:
        """factory_code frontmatter is surfaced + footer notes it (APP-61)."""
        d = tmp_path / "vault_fc"
        d.mkdir()
        (d / "tricity.md").write_text(
            "---\nmanufacturer: Yamaha\nvehicle_model: TRICITY155\n"
            "factory_code: MWS150-A\n---\n\n# Body\n",
            encoding="utf-8",
        )
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR", d,
        ):
            result = await list_manuals({})
        assert 'vehicle="Yamaha TRICITY155"' in result
        assert 'factory_code="MWS150-A"' in result
        # Footer explains the factory code is an alias match signal.
        assert "factory_code" in result.split("IMPORTANT")[1]

    @pytest.mark.asyncio
    async def test_filter_by_factory_code(
        self, tmp_path: Path,
    ) -> None:
        """A question naming the factory code matches the manual (APP-61)."""
        d = tmp_path / "vault_fcf"
        d.mkdir()
        (d / "tricity.md").write_text(
            "---\nmanufacturer: Yamaha\nvehicle_model: TRICITY155\n"
            "factory_code: MWS150-A\n---\n\n# Body\n",
            encoding="utf-8",
        )
        (d / "hiace.md").write_text(
            "---\nmanufacturer: Toyota\nvehicle_model: Hiace\n"
            "---\n\n# Body\n",
            encoding="utf-8",
        )
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR", d,
        ):
            # The model name is TRICITY155, so this only matches via
            # the factory_code alias.
            result = await list_manuals({"vehicle_model": "MWS150-A"})
        assert "tricity" in result
        assert "hiace" not in result

    @pytest.mark.asyncio
    async def test_omits_factory_code_when_absent(
        self, tmp_path: Path,
    ) -> None:
        """No factory_code= token for manuals without one (APP-61)."""
        d = tmp_path / "vault_nofc"
        d.mkdir()
        (d / "hiace.md").write_text(
            "---\nmanufacturer: Toyota\nvehicle_model: Hiace\n"
            "---\n\n# Body\n",
            encoding="utf-8",
        )
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR", d,
        ):
            result = await list_manuals({})
        # The footer always documents factory_code; assert the manual's
        # own entry line carries no factory_code token.
        entry_line = next(
            ln for ln in result.splitlines()
            if ln.startswith("- hiace")
        )
        assert "factory_code=" not in entry_line

    @pytest.mark.asyncio
    async def test_empty_directory(
        self, tmp_path: Path,
    ) -> None:
        """Returns message for empty storage."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "app.harness_tools.manual_tools._MANUAL_DIR",
            empty_dir,
        ):
            result = await list_manuals({})
        assert "No manuals found" in result


# ── DTC index slug mapping ────────────────────────────────────────


class TestDtcIndexSlugMap:
    """Tests for _build_dtc_slug_map and _augment_dtc_index."""

    # Mirrors the real MWS-150-A layout: diagnostic sections are
    # #### headings naming two codes each, and the appendix table
    # is the pipeline's two-column | DTC | Occurrences | shape.
    _REALISTIC_MD = (
        "# Manual\n\n"
        "### 汽油噴射系統\n\nintro\n\n"
        "#### 故障代碼編號 P0107、P0108\n\nprocedure text\n\n"
        "#### 故障代碼編號 P062F\n\nmore text\n\n"
        "## Appendix: DTC Index\n\n"
        "| DTC | Occurrences |\n"
        "|-----|-------------|\n"
        "| P0107 | 20 |\n"
        "| P0108 | 18 |\n"
        "| P062F | 3 |\n"
        "| P0500 | 2 |\n"
    )

    def test_map_covers_codes_in_deep_headings(self) -> None:
        """Depth-4 headings naming two codes map both codes."""
        from app.harness_tools.manual_tools import (
            _build_dtc_slug_map,
        )

        mapping = _build_dtc_slug_map(self._REALISTIC_MD)
        assert (
            mapping["P0107"] == "故障代碼編號-p0107、p0108"
        )
        assert mapping["P0108"] == mapping["P0107"]
        assert mapping["P062F"] == "故障代碼編號-p062f"
        assert "P0500" not in mapping

    def test_augment_appends_slug_column(self) -> None:
        """Two-column index rows gain the section-slug cell."""
        from app.harness_tools.manual_tools import (
            _augment_dtc_index,
            _build_dtc_slug_map,
            _extract_dtc_index,
        )

        index = _extract_dtc_index(self._REALISTIC_MD)
        assert index is not None
        augmented = _augment_dtc_index(
            index, _build_dtc_slug_map(self._REALISTIC_MD),
        )
        lines = augmented.splitlines()
        assert lines[0] == "| DTC | Occurrences | Section slug |"
        assert lines[1] == "|-----|-------------|-----|"
        assert (
            "| P0107 | 20 | 故障代碼編號-p0107、p0108 |"
            in lines
        )
        # Unmapped code carries the HARNESS-30a instruction marker
        # rather than a bare '-' (which cross-006 showed the agent
        # reading as proof of absence).
        assert (
            "| P0500 | 2 | "
            "NOT-INDEXED(use search_manual_text) |" in lines
        )


# ── Index dual-track (HARNESS-30 Phase 3) ─────────────────────────


V2_CONTENT = """\
---
vehicle_model: MWS-150-A
---
## 煞車系統
煞車卡鉗的檢查步驟,扭力 10 Nm。
## 故障代碼編號 P0335
曲軸位置感知器排查程序,共六步,包含接頭檢查。
"""

V2_SIDECAR = {
    "spec_version": "0.3",
    "manual_id": "MWS150A_Service_Manual",
    "source": {
        "content_file": "MWS150A_Service_Manual.md",
        "content_sha256": "x", "parser": "t", "built_at": "t",
    },
    "applicability": {
        "manufacturer": "Yamaha", "models": ["MWS-150-A"],
    },
    "vocab_version": "1",
    "tree": [{
        "node_id": "brakes-desc-煞車系統",
        "title": "煞車系統",
        "aliases": [], "node_type": "description",
        "subsystem": "brakes",
        "span": [0, 4], "page_range": [1, 2],
        "md_lines": [3, 9],
        "summary": "刹车章节。",
        "children": [
            {
                "node_id": "brakes-insp-煞車卡鉗的檢查",
                "title": "煞車卡鉗的檢查",
                "aliases": ["舊slug別名"],
                "node_type": "inspection",
                "subsystem": "brakes",
                "span": [0, 2], "page_range": [1, 1],
                "md_lines": [3, 5],
                "summary": "卡鉗檢查。", "children": [],
            },
            {
                "node_id": "electrical-fault-p0335",
                "title": "故障代碼編號 P0335",
                "aliases": [], "node_type": "fault_isolation",
                "subsystem": "electrical",
                "span": [2, 4], "page_range": [2, 2],
                "md_lines": [5, 9],
                "summary": "P0335 排查。", "children": [],
            },
        ],
    }],
    "faults": [{
        "code": "P0335",
        "item": "曲軸位置感知器",
        "symptom": "引擎無法起動。",
        "fail_safe": "無法運轉。",
        "detect_ref": "brakes-desc-煞車系統",
        "isolate_ref": "electrical-fault-p0335",
        "related_refs": [],
    }],
}


@pytest.fixture()
def index_track(manual_dir: Path):
    """Install a v2 sidecar for the sample manual + patch the
    index module's storage root; clears the runtime cache."""
    import yaml as _yaml

    from app.harness_tools import manual_index as mi

    idx_dir = manual_dir / "sub" / "index"
    idx_dir.mkdir(parents=True)
    (idx_dir / "MWS150A_Service_Manual.md").write_text(
        V2_CONTENT, encoding="utf-8",
    )
    (idx_dir / "MWS150A_Service_Manual.index.yaml").write_text(
        _yaml.safe_dump(V2_SIDECAR, allow_unicode=True),
        encoding="utf-8",
    )
    mi._cache.clear()
    with patch.object(mi, "_MANUAL_DIR", manual_dir):
        yield idx_dir
    mi._cache.clear()


class TestIndexDualTrack:
    """Phase-3 dual-track behaviour of the three tools."""

    @pytest.mark.asyncio
    async def test_toc_served_from_index(
        self, index_track,
    ) -> None:
        """TOC carries node_ids, labels, and the fault card."""
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "index-driven TOC" in result
        assert "[electrical-fault-p0335]" in result
        assert "(brakes/inspection)" in result
        assert "無法運轉。" in result  # quick-index card row

    @pytest.mark.asyncio
    async def test_read_by_node_id_slices_v2(
        self, index_track,
    ) -> None:
        """node_id addressing returns the md_lines slice."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "electrical-fault-p0335",
        })
        assert "曲軸位置感知器排查程序" in result
        assert "煞車卡鉗" not in result

    @pytest.mark.asyncio
    async def test_read_by_legacy_alias(
        self, index_track,
    ) -> None:
        """Old slugs kept as aliases still resolve (makeup)."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "舊slug別名",
        })
        assert "煞車卡鉗的檢查步驟" in result

    @pytest.mark.asyncio
    async def test_ambiguous_query_lists_candidates(
        self, index_track,
    ) -> None:
        """Substring matching lists ALL matches (P2.2 fix)."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "煞車",
        })
        assert "ambiguous" in result
        assert "[brakes-desc-煞車系統]" in result
        assert "[brakes-insp-煞車卡鉗的檢查]" in result

    @pytest.mark.asyncio
    async def test_search_attributes_hits_to_nodes(
        self, index_track,
    ) -> None:
        """Grep runs over v2 content with node attribution."""
        result = await search_manual_text({
            "manual_id": "MWS150A_Service_Manual",
            "query": "P0335",
        })
        assert "[node: electrical-fault-p0335]" in result

    @pytest.mark.asyncio
    async def test_node_id_guess_salvaged_via_cjk_tail(
        self, index_track,
    ) -> None:
        """A fabricated-but-plausible node_id resolves via its
        CJK tail (the cross-005 failure mode)."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "brakes-op-煞車卡鉗的檢查",
        })
        assert "煞車卡鉗的檢查步驟" in result

    @pytest.mark.asyncio
    async def test_miss_offers_closest_candidates(
        self, index_track,
    ) -> None:
        """A near-miss query lists bigram-closest node_ids
        instead of a dead end."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "煞車卡鉗檢查程序",
        })
        assert "[brakes-insp-煞車卡鉗的檢查]" in result

    @pytest.mark.asyncio
    async def test_track_off_forces_legacy(
        self, index_track, monkeypatch,
    ) -> None:
        """MANUAL_INDEX_TRACK=off restores legacy behaviour."""
        monkeypatch.setenv("MANUAL_INDEX_TRACK", "off")
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "index-driven TOC" not in result
        assert "Chapter 1: General Information" in result

    @pytest.mark.asyncio
    async def test_no_sidecar_is_byte_identical_legacy(
        self,
    ) -> None:
        """Without a sidecar the legacy path is untouched."""
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "index-driven TOC" not in result
        assert "Chapter 3: Fuel System" in result


# ── search_manual_text (HARNESS-30a) ──────────────────────────────


class TestSearchManualText:
    """Tests for the literal full-text search handler."""

    @pytest.mark.asyncio
    async def test_hit_reports_enclosing_section_slug(
        self,
    ) -> None:
        """Each hit is attributed to its enclosing section so the
        agent can jump straight to read_manual_section."""
        result = await search_manual_text({
            "manual_id": "MWS150A_Service_Manual",
            "query": "vacuum leaks",
        })
        assert "1 line(s) match" in result
        assert "dtc-p0171-system-too-lean" in result
        assert "Check intake manifold" in result

    @pytest.mark.asyncio
    async def test_case_insensitive(self) -> None:
        """Lower-cased DTC query still matches P0171 lines."""
        result = await search_manual_text({
            "manual_id": "MWS150A_Service_Manual",
            "query": "p0171",
        })
        assert "0 matches" not in result
        assert "P0171" in result

    @pytest.mark.asyncio
    async def test_zero_matches_is_explicit(self) -> None:
        """Zero hits produce the explicit absence statement the
        search gate relies on."""
        result = await search_manual_text({
            "manual_id": "MWS150A_Service_Manual",
            "query": "P9999",
        })
        assert "0 matches for 'P9999'" in result
        assert "does not contain" in result

    @pytest.mark.asyncio
    async def test_max_hits_caps_output(self) -> None:
        """Hit list is capped but the total count is reported."""
        result = await search_manual_text({
            "manual_id": "MWS150A_Service_Manual",
            "query": "Fuel",
            "max_hits": 2,
        })
        assert "showing first 2" in result
        assert result.count("- [section:") == 2

    @pytest.mark.asyncio
    async def test_manual_not_found(self) -> None:
        """Unknown manual returns the available list."""
        result = await search_manual_text({
            "manual_id": "nope",
            "query": "anything",
        })
        assert "not found" in result
        assert "MWS150A_Service_Manual" in result


# ── get_manual_toc ────────────────────────────────────────────────


class TestGetManualToc:
    """Tests for get_manual_toc handler."""

    @pytest.mark.asyncio
    async def test_correct_tree(self) -> None:
        """Returns heading tree with slugs."""
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "Chapter 1: General Information" in result
        assert "Chapter 3: Fuel System" in result
        assert "1-1-specifications" in result

    @pytest.mark.asyncio
    async def test_includes_dtc_index(self) -> None:
        """Includes DTC quick index from appendix."""
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "DTC Quick Index" in result
        assert "P0171" in result

    @pytest.mark.asyncio
    async def test_dtc_index_maps_code_to_section_slug(
        self,
    ) -> None:
        """Index rows carry the slug of the code's own section.

        The P0171 diagnostic section is a ``####`` heading —
        below the default TOC depth — so the index row must
        surface its slug for direct read_manual_section access.
        """
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        index_row = next(
            ln for ln in result.splitlines()
            if ln.startswith("| P0171")
        )
        assert "dtc-p0171-system-too-lean" in index_row
        # Header advertises the new column.
        assert "Section slug" in result

    @pytest.mark.asyncio
    async def test_toc_carries_absence_guard_text(self) -> None:
        """The quick index is followed by the HARNESS-30a guard
        forbidding NOT-INDEXED marks being read as absence."""
        result = await get_manual_toc(
            {"manual_id": "MWS150A_Service_Manual"},
        )
        assert "NEVER cite a NOT-INDEXED" in result
        assert "search_manual_text" in result

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        """Returns error with available manuals."""
        result = await get_manual_toc(
            {"manual_id": "NonexistentManual"},
        )
        assert "not found" in result
        assert "MWS150A_Service_Manual" in result

    @pytest.mark.asyncio
    async def test_manual_without_dtc_index(
        self,
    ) -> None:
        """Works for manuals without DTC appendix."""
        result = await get_manual_toc(
            {"manual_id": "STF850_Workshop"},
        )
        assert "Chapter 1: Engine" in result
        assert "DTC Quick Index" not in result

    @pytest.mark.asyncio
    async def test_max_depth_caps_tree(self) -> None:
        """``max_depth=2`` hides deeper sections.

        Default depth=3 includes subsections; depth=2 should
        clip to chapters + sections only and emit a placeholder
        line telling the agent how many were hidden.
        """
        result = await get_manual_toc({
            "manual_id": "MWS150A_Service_Manual",
            "max_depth": 2,
        })
        # Top-level chapter is visible.
        assert "Chapter 1: General Information" in result
        # Subsection (would be at depth 3) is hidden.
        assert "1-1-specifications" not in result
        # Hidden-count placeholder is shown.
        assert "more nested sections" in result

    @pytest.mark.asyncio
    async def test_max_depth_full_tree(self) -> None:
        """High max_depth includes everything."""
        result = await get_manual_toc({
            "manual_id": "MWS150A_Service_Manual",
            "max_depth": 99,
        })
        assert "1-1-specifications" in result
        assert "more nested sections" not in result


# ── read_manual_section ───────────────────────────────────────────


class TestReadManualSection:
    """Tests for read_manual_section handler."""

    @pytest.mark.asyncio
    async def test_by_slug(self) -> None:
        """Finds section by exact slug."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "1-1-specifications",
        })
        assert isinstance(result, str)
        assert "Displacement" in result

    @pytest.mark.asyncio
    async def test_by_heading_text(self) -> None:
        """Finds section by heading text (slugified)."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "1.1 Specifications",
        })
        assert isinstance(result, str)
        assert "Displacement" in result

    @pytest.mark.asyncio
    async def test_multimodal_with_images(self) -> None:
        """Returns multimodal blocks for sections with images."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": (
                "3-2-fuel-system-troubleshooting"
            ),
        })
        # Should be a list with image blocks.
        assert isinstance(result, list)
        types = [b.get("type") for b in result]
        assert "image_url" in types
        assert "text" in types

    @pytest.mark.asyncio
    async def test_text_only_section(self) -> None:
        """Returns plain string for sections without images."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "3-1-fuel-system-overview",
        })
        assert isinstance(result, str)
        assert "tank and pump" in result.lower()

    @pytest.mark.asyncio
    async def test_not_found_with_suggestion(
        self,
    ) -> None:
        """Returns actionable error with suggestion."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "Fuel Systme",  # typo
        })
        assert isinstance(result, str)
        assert "not found" in result
        # Should suggest the correct section.
        assert "Fuel System" in result or "fuel" in result

    @pytest.mark.asyncio
    async def test_manual_not_found(self) -> None:
        """Returns error for nonexistent manual."""
        result = await read_manual_section({
            "manual_id": "NonexistentManual",
            "section": "anything",
        })
        assert isinstance(result, str)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_include_subsections(self) -> None:
        """Includes child sections by default."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "chapter-3-fuel-system",
            "include_subsections": True,
        })
        # Should contain both overview and troubleshooting.
        text = (
            result if isinstance(result, str)
            else " ".join(
                b.get("text", "")
                for b in result
                if b.get("type") == "text"
            )
        )
        assert "Fuel System Overview" in text
        assert "Troubleshooting" in text

    @pytest.mark.asyncio
    async def test_exclude_subsections(self) -> None:
        """Stops at first child when subsections disabled."""
        result = await read_manual_section({
            "manual_id": "MWS150A_Service_Manual",
            "section": "chapter-3-fuel-system",
            "include_subsections": False,
        })
        assert isinstance(result, str)
        assert "## Chapter 3" in result
        assert "Troubleshooting" not in result

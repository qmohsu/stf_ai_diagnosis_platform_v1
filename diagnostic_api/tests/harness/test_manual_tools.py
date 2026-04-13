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

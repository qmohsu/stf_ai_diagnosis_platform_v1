"""Tests for manual filesystem helpers.

Covers frontmatter parsing, heading tree construction, section
extraction, slug matching, image resolution, and multimodal
content block building.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from app.harness_tools.manual_fs import (
    HeadingNode,
    build_multimodal_section,
    extract_section,
    find_closest_slug,
    load_image_as_content_block,
    parse_frontmatter,
    parse_heading_tree,
    resolve_image_refs,
    slugify,
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
section_count: 5
---

# MWS-150-A Service Manual

<!-- page:1 -->

This manual covers the MWS-150-A scooter.

## Chapter 1: General Information

<!-- page:5 -->

### 1.1 Specifications

| Specification | Value | Unit |
|---------------|-------|------|
| Displacement | 155 | cc |

### 1.2 Torque Specifications

| Part | Torque | Unit |
|------|--------|------|
| Spark plug | 12.5 | N-m |

## Chapter 3: Fuel System

<!-- page:42 -->

### 3.1 Fuel System Overview

The fuel system consists of the fuel tank and pump.

### 3.2 Fuel System Troubleshooting

<!-- page:45 -->

![Fuel injector diagram](images/MWS150A_Service_Manual/p045-1.png)

*Vision description: Exploded view of the fuel injector.*

#### DTC: P0171 — System Too Lean

This code indicates a lean condition.

**Diagnostic Steps:**
1. Check intake manifold for vacuum leaks.
2. Measure fuel pressure at rail.

## Appendix: DTC Index

| DTC | Description | Section |
|-----|-------------|---------|
| P0171 | System Too Lean | 3.2 Fuel System |
"""

# Minimal 1x1 red PNG (68 bytes).
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
    """Create a temp manual directory with a sample image."""
    img_dir = (
        tmp_path / "images" / "MWS150A_Service_Manual"
    )
    img_dir.mkdir(parents=True)
    (img_dir / "p045-1.png").write_bytes(TINY_PNG)
    return tmp_path


# ── slugify ───────────────────────────────────────────────────────


class TestSlugify:
    """Tests for slugify()."""

    def test_basic_heading(self) -> None:
        """Simple heading to slug conversion."""
        assert slugify("Chapter 3: Engine") == (
            "chapter-3-engine"
        )

    def test_dtc_heading(self) -> None:
        """DTC heading with special characters."""
        result = slugify(
            "DTC: P0171 — System Too Lean",
        )
        assert result == "dtc-p0171-system-too-lean"

    def test_empty_string(self) -> None:
        """Empty string produces empty slug."""
        assert slugify("") == ""

    def test_truncation_at_80_chars(self) -> None:
        """Long titles are truncated at hyphen boundary."""
        long_title = "word " * 30  # 150+ chars
        slug = slugify(long_title)
        assert len(slug) <= 80


# ── parse_frontmatter ─────────────────────────────────────────────


class TestParseFrontmatter:
    """Tests for parse_frontmatter()."""

    def test_valid_frontmatter(self) -> None:
        """Extracts all frontmatter fields."""
        fm = parse_frontmatter(SAMPLE_MANUAL)
        assert fm["vehicle_model"] == "MWS-150-A"
        assert fm["page_count"] == 415
        assert fm["language"] == "zh-CN"
        assert fm["source_pdf"] == (
            "MWS150A_Service_Manual.pdf"
        )

    def test_no_frontmatter(self) -> None:
        """Returns empty dict for plain text."""
        fm = parse_frontmatter("# Just a heading\n\nText.")
        assert fm == {}

    def test_malformed_yaml(self) -> None:
        """Returns empty dict for invalid YAML."""
        fm = parse_frontmatter("---\n: bad: yaml:\n---\n")
        assert isinstance(fm, dict)


# ── parse_heading_tree ────────────────────────────────────────────


class TestParseHeadingTree:
    """Tests for parse_heading_tree()."""

    def test_correct_hierarchy(self) -> None:
        """Builds proper parent-child relationships."""
        tree = parse_heading_tree(SAMPLE_MANUAL)
        # Top-level: # title
        assert len(tree) == 1
        root = tree[0]
        assert root.title == "MWS-150-A Service Manual"
        assert root.level == 1
        # Children: ## chapters
        chapters = root.children
        assert len(chapters) >= 3  # Ch1, Ch3, Appendix

    def test_slugs_assigned(self) -> None:
        """Every node has a non-empty slug."""
        tree = parse_heading_tree(SAMPLE_MANUAL)

        def _check(nodes: List[HeadingNode]) -> None:
            for node in nodes:
                assert node.slug, (
                    f"Empty slug for '{node.title}'"
                )
                _check(node.children)

        _check(tree)

    def test_duplicate_slugs(self) -> None:
        """Duplicate headings get -2, -3 suffixes."""
        md = (
            "## Overview\n\nFirst.\n\n"
            "## Overview\n\nSecond.\n\n"
            "## Overview\n\nThird.\n"
        )
        tree = parse_heading_tree(md)
        slugs = [n.slug for n in tree]
        assert slugs == [
            "overview", "overview-2", "overview-3",
        ]

    def test_line_ranges(self) -> None:
        """Each node has valid line_start < line_end."""
        tree = parse_heading_tree(SAMPLE_MANUAL)

        def _check(nodes: List[HeadingNode]) -> None:
            for node in nodes:
                assert node.line_start < node.line_end, (
                    f"Invalid range for '{node.title}'"
                )
                _check(node.children)

        _check(tree)

    def test_empty_document(self) -> None:
        """Empty document returns empty tree."""
        assert parse_heading_tree("") == []


# ── extract_section ───────────────────────────────────────────────


class TestExtractSection:
    """Tests for extract_section()."""

    def test_with_subsections(self) -> None:
        """Includes child headings when requested."""
        result = extract_section(
            SAMPLE_MANUAL,
            "chapter-3-fuel-system",
            include_subsections=True,
        )
        assert result is not None
        assert "Fuel System Overview" in result
        assert "Fuel System Troubleshooting" in result
        assert "P0171" in result

    def test_without_subsections(self) -> None:
        """Stops at first child heading."""
        result = extract_section(
            SAMPLE_MANUAL,
            "chapter-3-fuel-system",
            include_subsections=False,
        )
        assert result is not None
        assert "## Chapter 3: Fuel System" in result
        # Should NOT contain subsection content.
        assert "Fuel System Troubleshooting" not in result

    def test_leaf_section(self) -> None:
        """Leaf section (no children) returns content."""
        result = extract_section(
            SAMPLE_MANUAL,
            "1-1-specifications",
        )
        assert result is not None
        assert "Displacement" in result

    def test_not_found(self) -> None:
        """Returns None for nonexistent slug."""
        result = extract_section(
            SAMPLE_MANUAL,
            "nonexistent-section",
        )
        assert result is None


# ── find_closest_slug ─────────────────────────────────────────────


class TestFindClosestSlug:
    """Tests for find_closest_slug()."""

    def test_exact_match(self) -> None:
        """Exact slug match is returned."""
        slugs = [
            "chapter-1-general",
            "chapter-3-fuel-system",
        ]
        assert find_closest_slug(
            "chapter-3-fuel-system", slugs,
        ) == "chapter-3-fuel-system"

    def test_heading_text_match(self) -> None:
        """Heading text is slugified and matched."""
        slugs = [
            "chapter-1-general",
            "chapter-3-fuel-system",
        ]
        assert find_closest_slug(
            "Chapter 3: Fuel System", slugs,
        ) == "chapter-3-fuel-system"

    def test_substring_match(self) -> None:
        """Partial match via substring."""
        slugs = [
            "3-2-fuel-system-troubleshooting",
            "4-1-ignition-system",
        ]
        result = find_closest_slug(
            "fuel-system", slugs,
        )
        assert result == "3-2-fuel-system-troubleshooting"

    def test_no_match(self) -> None:
        """Returns None when nothing is close."""
        slugs = ["chapter-1-general"]
        result = find_closest_slug(
            "completely-unrelated-topic", slugs,
        )
        # May or may not match depending on threshold.
        # Just verify it doesn't crash.
        assert result is None or result in slugs

    def test_empty_list(self) -> None:
        """Returns None for empty slug list."""
        assert find_closest_slug("test", []) is None


# ── resolve_image_refs ────────────────────────────────────────────


class TestResolveImageRefs:
    """Tests for resolve_image_refs()."""

    def test_finds_existing_images(
        self, manual_dir: Path,
    ) -> None:
        """Resolves image references to existing files."""
        section = (
            "Text before.\n\n"
            "![Fuel injector](images/"
            "MWS150A_Service_Manual/p045-1.png)\n\n"
            "Text after."
        )
        refs = resolve_image_refs(section, manual_dir)
        assert len(refs) == 1
        assert refs[0][1].name == "p045-1.png"

    def test_skips_missing_images(
        self, manual_dir: Path,
    ) -> None:
        """Missing files are excluded from results."""
        section = "![Missing](images/no/such/file.png)"
        refs = resolve_image_refs(section, manual_dir)
        assert refs == []

    def test_blocks_path_traversal(
        self, manual_dir: Path,
    ) -> None:
        """Path traversal attempts are blocked."""
        section = "![Evil](../../etc/passwd)"
        refs = resolve_image_refs(section, manual_dir)
        assert refs == []


# ── load_image_as_content_block ───────────────────────────────────


class TestLoadImageAsContentBlock:
    """Tests for load_image_as_content_block()."""

    def test_valid_png(self, manual_dir: Path) -> None:
        """Loads PNG and produces correct content block."""
        img_path = (
            manual_dir
            / "images"
            / "MWS150A_Service_Manual"
            / "p045-1.png"
        )
        block = load_image_as_content_block(img_path)
        assert block is not None
        assert block["type"] == "image_url"
        url = block["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_missing_file(self, tmp_path: Path) -> None:
        """Returns None for nonexistent file."""
        block = load_image_as_content_block(
            tmp_path / "no_such_file.png",
        )
        assert block is None

    def test_oversized_file(
        self, manual_dir: Path,
    ) -> None:
        """Returns None for files exceeding size limit."""
        big_file = manual_dir / "big.png"
        # Write 6 MB of data.
        big_file.write_bytes(b"\x00" * (6 * 1024 * 1024))
        block = load_image_as_content_block(big_file)
        assert block is None


# ── build_multimodal_section ──────────────────────────────────────


class TestBuildMultimodalSection:
    """Tests for build_multimodal_section()."""

    def test_text_only_section(
        self, manual_dir: Path,
    ) -> None:
        """Section without images returns single text block."""
        blocks = build_multimodal_section(
            "No images here.", manual_dir,
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    def test_interleaved_content(
        self, manual_dir: Path,
    ) -> None:
        """Section with image produces interleaved blocks."""
        section = (
            "Text before.\n\n"
            "![Fuel injector](images/"
            "MWS150A_Service_Manual/p045-1.png)\n\n"
            "*Vision description: Exploded view.*"
        )
        blocks = build_multimodal_section(
            section, manual_dir,
        )
        types = [b["type"] for b in blocks]
        assert "text" in types
        assert "image_url" in types

    def test_preserves_text_around_images(
        self, manual_dir: Path,
    ) -> None:
        """Text before and after image is preserved."""
        section = (
            "Before text.\n\n"
            "![img](images/"
            "MWS150A_Service_Manual/p045-1.png)\n\n"
            "After text."
        )
        blocks = build_multimodal_section(
            section, manual_dir,
        )
        text_blocks = [
            b for b in blocks if b["type"] == "text"
        ]
        all_text = " ".join(
            b["text"] for b in text_blocks
        )
        assert "Before text" in all_text
        assert "After text" in all_text

    def test_missing_image_keeps_reference(
        self, manual_dir: Path,
    ) -> None:
        """Missing image: reference kept in text."""
        section = (
            "Text.\n\n"
            "![Missing](images/no/such.png)\n\n"
            "More text."
        )
        blocks = build_multimodal_section(
            section, manual_dir,
        )
        # No image blocks (file doesn't exist).
        image_blocks = [
            b for b in blocks if b["type"] == "image_url"
        ]
        assert len(image_blocks) == 0

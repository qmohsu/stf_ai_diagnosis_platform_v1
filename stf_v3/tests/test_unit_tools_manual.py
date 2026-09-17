"""PROD-08 T-4: the four manual tools (offline, fixture manual).

Covers the vehicle-match flag table (FM-6), the index track (FM-39) and
image delivery off/on (FM-42).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import base64
import pathlib
import shutil

import pytest
import yaml
from pydantic_ai import BinaryContent, ToolReturn

from stf_v3.diagnosis.tools import manual_tools
from stf_v3.knowledge import manual_index
from tests.agent_helpers import FIXTURES, make_deps, small_manual, stub_ctx

# 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.mark.parametrize(
    "manual_mfr, manual_model, mfr, model, expected",
    [
        ("Toyota", "Corolla E11", "Toyota", "Corolla", True),
        ("Toyota", "Corolla E11", "toyota", "COROLLA E11", True),
        ("Yamaha", "TRICITY155", "Yamaha", "Tricity 155", True),
        ("Yamaha", "TRICITY155", "Yamaha", "Tricity", False),      # 'tricity' ⊂ 'tricity155' but short? no: len 7 → match
        ("Toyota", "Corolla E11", "Toyota", "Hiace", False),
        ("Yamaha", "TRICITY155", "Toyota", "Hiace", False),
        ("Toyota", "Corolla E11", "Honda", "Corolla", False),
        ("Honda", "GT", "Honda", "GTR", False),                      # tokens < 4 chars must be equal
    ],
)
def test_manual_matches_vehicle_table(manual_mfr: str, manual_model: str, mfr: str, model: str, expected: bool) -> None:
    """Manufacturer must be equal; model tokens normalised and contained
    either way; tiny tokens must match exactly (FM-6)."""
    m = small_manual(manufacturer=manual_mfr, vehicle_model=manual_model)
    result = manual_tools.manual_matches_vehicle(m, mfr, model)
    if (manual_model, model) == ("TRICITY155", "Tricity"):
        assert result is True   # 7-char token 'tricity' is a genuine prefix match
    else:
        assert result is expected


async def test_list_manuals_only_inventory_with_match_flag() -> None:
    """The listing comes from the deps inventory (never a directory scan)
    and flags whether each manual matches the vehicle record."""
    manuals = [small_manual("m1", manufacturer="Toyota", vehicle_model="Corolla E11", factory_code=None),
               small_manual("m2", manufacturer="Yamaha", vehicle_model="TRICITY155", factory_code="MWS150-A")]
    out = await manual_tools.list_manuals(stub_ctx(make_deps(manufacturer="Toyota", model="Corolla", manuals=manuals)))
    assert "- m1  vehicle=\"Toyota Corolla E11\"" in out and "matches_this_vehicle=yes" in out
    assert "- m2" in out and 'factory_code="MWS150-A"' in out
    assert out.count("matches_this_vehicle=no") == 1
    filtered = await manual_tools.list_manuals(stub_ctx(make_deps(manuals=manuals)), vehicle_model="MWS150")
    assert "- m2" in filtered and "- m1" not in filtered
    empty = await manual_tools.list_manuals(stub_ctx(make_deps(manuals=[])))
    assert "No manuals found" in empty


async def test_toc_read_and_search_on_markdown_track() -> None:
    """Without an index sidecar the heading tree is used: TOC lists slugs,
    a section reads by slug or title, search reports enclosing sections."""
    ctx = stub_ctx(make_deps())
    toc = await manual_tools.get_manual_toc(ctx, manual_id="m1")
    assert "[1-1-specifications]" in toc and "[2-2-dtc-p0171-system-too-lean]" in toc
    section = await manual_tools.read_manual_section(ctx, manual_id="m1", section="Fuel Pump Troubleshooting")
    assert isinstance(section, str) and "320 kPa" in section
    by_slug = await manual_tools.read_manual_section(ctx, manual_id="m1", section="3-1-battery")
    assert "12.6 V" in by_slug
    missing = await manual_tools.read_manual_section(ctx, manual_id="m1", section="Turbocharger")
    assert "not found" in missing
    hits = await manual_tools.search_manual_text(ctx, manual_id="m1", query="P0171")
    assert "match 'P0171'" in hits and "[section: 2-2-dtc-p0171-system-too-lean]" in hits
    none = await manual_tools.search_manual_text(ctx, manual_id="m1", query="turbocharger")
    assert none.startswith("0 matches")
    unknown = await manual_tools.get_manual_toc(ctx, manual_id="nope")
    assert "not found" in unknown and "m1" in unknown


async def test_images_omitted_by_default_and_delivered_when_enabled(tmp_path: pathlib.Path) -> None:
    """FM-42: with images off the section carries an ``[image: … omitted]``
    marker and stays a plain string; with images on the tool returns a
    ``ToolReturn`` whose content includes a ``BinaryContent``."""
    root = tmp_path / "manuals"
    (root / "images" / "manual_small").mkdir(parents=True)
    shutil.copy(FIXTURES / "manual_small.md", root / "manual_small.md")
    (root / "images" / "manual_small" / "p3-1.png").write_bytes(_PNG)
    off = stub_ctx(make_deps(manual_root=root))
    text = await manual_tools.read_manual_section(off, manual_id="m1", section="1-1-specifications")
    assert isinstance(text, str) and "[image: engine bay omitted]" in text and "![" not in text
    on = stub_ctx(make_deps(manual_root=root, images_enabled=True))
    result = await manual_tools.read_manual_section(on, manual_id="m1", section="1-1-specifications")
    assert isinstance(result, ToolReturn)
    assert any(isinstance(c, BinaryContent) for c in result.content)
    assert "[image: engine bay omitted]" in result.return_value


async def test_index_track_is_used_when_sidecar_exists(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FM-39: the manual id is the database id; a ``<id>.index.yaml`` sidecar
    switches TOC / read / search to the index track (node ids, not slugs)."""
    root = tmp_path / "manuals"
    (root / "Jazz" / "index").mkdir(parents=True)
    content = "# Fuel\n\nFuel pressure 320 kPa.\n\n# Brakes\n\nPad limit 1.5 mm.\n"
    (root / "Jazz" / "index" / "content.md").write_text(content, encoding="utf-8")
    sidecar = {
        "manual_id": "m1",
        "source": {"content_file": "content.md"},
        "faults": [],
        "tree": [
            {"node_id": "fuel-system", "title": "Fuel", "node_type": "system", "subsystem": "fuel",
             "page_range": [1, 1], "md_lines": [0, 3], "summary": "fuel", "aliases": []},
            {"node_id": "brake-system", "title": "Brakes", "node_type": "system", "subsystem": "brake",
             "page_range": [2, 2], "md_lines": [4, 7], "summary": "brakes", "aliases": []},
        ],
    }
    (root / "Jazz" / "index" / "m1.index.yaml").write_text(yaml.safe_dump(sidecar), encoding="utf-8")
    monkeypatch.setattr(manual_index, "_MANUAL_DIR", root)
    manual_index._cache.clear()
    ctx = stub_ctx(make_deps(manual_root=root, manuals=[small_manual("m1", md_file_path="Jazz/index/content.md")]))
    toc = await manual_tools.get_manual_toc(ctx, manual_id="m1")
    assert "fuel-system" in toc and "brake-system" in toc
    section = await manual_tools.read_manual_section(ctx, manual_id="m1", section="brake-system")
    assert "1.5 mm" in section
    hits = await manual_tools.search_manual_text(ctx, manual_id="m1", query="320 kPa")
    assert "[node: fuel-system]" in hits
    assert manual_tools.resolve_section_slug(ctx.deps, "m1", "Brakes") == "brake-system"

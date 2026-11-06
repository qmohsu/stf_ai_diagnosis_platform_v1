"""Source-section and image safety checks for report citation navigation."""

import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from stf_v3.errors import ApiError
from stf_v3.knowledge import manual_index, service
from stf_v3.settings import settings

FIXTURE = Path(__file__).parent / "fixtures" / "manual_small.md"


@pytest.fixture()
def manual_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "manual_storage_path", str(tmp_path))
    monkeypatch.setattr(manual_index, "_MANUAL_DIR", tmp_path)
    monkeypatch.delenv("MANUAL_INDEX_TRACK", raising=False)
    manual_index._cache.clear()  # noqa: SLF001
    return tmp_path


def make_row(root: Path, *, indexed: bool = False):  # type: ignore[no-untyped-def]
    mid = uuid.uuid4()
    directory = root / "Jazz" / ("index" if indexed else "")
    directory.mkdir(parents=True, exist_ok=True)
    md = directory / f"{mid}.md"
    shutil.copy(FIXTURE, md)
    return SimpleNamespace(
        id=mid, status="ingested", md_file_path=str(md.relative_to(root)),
    )


def test_legacy_source_resolves_raw_titles_and_images(manual_root: Path) -> None:
    row = make_row(manual_root)
    section = service.read_section(row, "1.1 Specifications")
    assert section.section_id == "1-1-specifications"
    assert section.title == "1.1 Specifications"
    assert section.index_track is False and section.page_range is None
    assert "3.5 L" in section.content_md
    assert "![engine bay](images/manual_small/p3-1.png)" in section.content_md

    image = manual_root / "Jazz" / "images" / "manual_small" / "p3-1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    resolved, mime = service.image_asset(row, "images/manual_small/p3-1.png")
    assert resolved == image and mime == "image/png"


def test_legacy_duplicate_title_never_picks_a_guess(manual_root: Path) -> None:
    row = make_row(manual_root)
    (manual_root / row.md_file_path).write_text(
        "# Engine\n\nFirst.\n\n# Engine\n\nSecond.\n", encoding="utf-8",
    )
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Engine")
    assert err.value.status_code == 409 and err.value.code == "section_ambiguous"
    assert "Second." in service.read_section(row, "engine-2").content_md
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Missing")
    assert err.value.status_code == 404 and err.value.code == "section_not_found"


def test_indexed_source_uses_node_id_and_page_range(manual_root: Path) -> None:
    row = make_row(manual_root, indexed=True)
    md = manual_root / row.md_file_path
    md.write_text("# Fuel System\n\nFuel pressure 320 kPa.\n", encoding="utf-8")
    (md.parent / f"{row.id}.index.yaml").write_text(yaml.safe_dump({
        "manual_id": str(row.id),
        "source": {"content_file": md.name},
        "faults": [],
        "tree": [{
            "node_id": "fuel-system", "title": "Fuel System", "aliases": ["Pump"],
            "node_type": "chapter", "subsystem": "fuel", "page_range": [5, 9],
            "md_lines": [0, 3], "summary": "", "children": [],
        }, {
            "node_id": "brake-system", "title": "Brakes", "aliases": ["Pump"],
            "node_type": "chapter", "subsystem": "brakes", "page_range": [10, 12],
            "md_lines": [0, 3], "summary": "", "children": [],
        }],
    }), encoding="utf-8")
    from_title = service.read_section(row, "Fuel System")
    from_id = service.read_section(row, "fuel-system")
    assert from_title == from_id
    assert from_id.index_track is True and from_id.page_range == (5, 9)
    assert "320 kPa" in from_id.content_md
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Pump")
    assert err.value.status_code == 409 and err.value.code == "section_ambiguous"
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Fuel Systom")
    assert err.value.status_code == 404 and err.value.code == "section_not_found"


def test_unreadable_manual_and_index_cannot_escape_storage(manual_root: Path) -> None:
    row = make_row(manual_root, indexed=True)
    row.status = "converting"
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Fuel")
    assert err.value.code == "manual_not_readable"
    row.status = "ingested"
    other = make_row(manual_root)
    row.md_file_path = other.md_file_path
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Fuel")
    assert err.value.status_code == 409 and err.value.code == "manual_not_readable"

    row.md_file_path = "../outside.md"
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Fuel")
    assert err.value.code == "manual_not_readable"

    row.md_file_path = str(next((manual_root / "Jazz" / "index").glob("*.md")).relative_to(manual_root))
    md = manual_root / row.md_file_path
    (manual_root / "outside.md").write_text("# Secret\n\nDo not serve this.", encoding="utf-8")
    sidecar = md.parent / f"{row.id}.index.yaml"
    sidecar.write_text(yaml.safe_dump({
        "manual_id": str(row.id), "source": {"content_file": "../../outside.md"},
        "tree": [],
    }), encoding="utf-8")
    with pytest.raises(ApiError) as err:
        service.read_section(row, "Fuel")
    assert err.value.status_code == 409 and err.value.code == "manual_not_readable"


@pytest.mark.parametrize("path", [
    "images/../secret.png", "images//photo.png", "images/photo.svg",
    "images\\photo.png", "other/photo.png", "images/missing.png",
])
def test_images_reject_unsafe_or_missing_paths(manual_root: Path, path: str) -> None:
    row = make_row(manual_root)
    with pytest.raises(ApiError) as err:
        service.image_asset(row, path)
    assert err.value.status_code == 404 and err.value.code == "asset_not_found"


async def test_source_routes_return_private_json_and_image(manual_root: Path) -> None:
    """The actual FastAPI routes preserve the same auth boundary and headers."""
    from stf_v3.auth.manager import current_user
    from stf_v3.db import get_session
    from stf_v3.main import app

    row = make_row(manual_root)
    image = manual_root / "Jazz" / "images" / "p.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def get_manual(_session, _manual_id):  # type: ignore[no-untyped-def]
        return row

    async def require_member(_session, _user_id):  # type: ignore[no-untyped-def]
        return None

    from stf_v3.knowledge import service as knowledge_service

    original_get = knowledge_service.get_manual
    original_member = knowledge_service.require_member
    old_overrides = dict(app.dependency_overrides)
    knowledge_service.get_manual = get_manual
    knowledge_service.require_member = require_member
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[get_session] = lambda: object()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get(f"/v3/manuals/{row.id}/sections", params={"section": "1.1 Specifications"})
            assert result.status_code == 200
            assert result.json()["section_id"] == "1-1-specifications"
            assert result.headers["cache-control"] == "private, no-store"
            asset = await client.get(f"/v3/manuals/{row.id}/assets/images/p.png")
            assert asset.status_code == 200 and asset.content == image.read_bytes()
            assert asset.headers["content-type"] == "image/png"
            assert asset.headers["x-content-type-options"] == "nosniff"
            assert asset.headers["cache-control"] == "private, no-store"
            row.status = "queued"
            unreadable = await client.get(f"/v3/manuals/{row.id}/sections", params={"section": "1.1 Specifications"})
            assert unreadable.status_code == 409 and unreadable.json()["code"] == "manual_not_readable"
            row.status = "ingested"

            async def no_membership(_session, _user_id):  # type: ignore[no-untyped-def]
                raise ApiError(403, "membership_required", "Join a workshop first")

            knowledge_service.require_member = no_membership
            denied = await client.get(f"/v3/manuals/{row.id}/sections", params={"section": "1.1 Specifications"})
            assert denied.status_code == 403 and denied.json()["code"] == "membership_required"
            denied_asset = await client.get(f"/v3/manuals/{row.id}/assets/images/p.png")
            assert denied_asset.status_code == 403
    finally:
        knowledge_service.get_manual = original_get
        knowledge_service.require_member = original_member
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)

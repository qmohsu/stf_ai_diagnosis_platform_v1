"""API-level tests for the manual library (PROD-06): T-2, T-3, T-5, T-7,
T-10 and the read endpoints, against a real Postgres (no GPU worker: the
queue rows are inspected directly).

Author: Xiangzhu Yan
"""

import asyncio
import io
import pathlib
import shutil
import uuid
from typing import Dict, Tuple

import pytest
from pypdf import PdfWriter

from tests.conftest import register_and_login, requires_db

pytestmark = requires_db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def make_pdf(pages: int = 2, marker: bytes = b"") -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if marker:
        w.add_metadata({"/Title": marker.decode()})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _manual_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Fresh manual storage root per test."""
    from stf_v3.knowledge import manual_index
    from stf_v3.settings import settings

    monkeypatch.setattr(settings, "manual_storage_path", str(tmp_path))
    monkeypatch.setattr(manual_index, "_MANUAL_DIR", tmp_path)
    manual_index._cache.clear()  # noqa: SLF001
    return tmp_path


@pytest.fixture()
async def opened_queue():  # type: ignore[no-untyped-def]
    """The API defers through the queue app; open it like lifespan does."""
    from stf_v3.jobs.app import app

    async with app.open_async():
        yield app


def _form(data: bytes, name: str = "m.pdf", **fields: str) -> Dict[str, object]:
    return {"files": {"file": (name, data, "application/pdf")},
            "data": {"manufacturer": "Honda", "vehicle_model": "Jazz", **fields}}


async def _setup(client, workshop_with_codes):  # type: ignore[no-untyped-def]
    wid, codes = workshop_with_codes
    manager = await register_and_login(client, "alice", codes["manager"])
    tech = await register_and_login(client, "bob", codes["technician"])
    return wid, manager, tech


async def _jobs(task: str = "knowledge.ingest_manual"):  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from stf_v3.db import SessionLocal

    async with SessionLocal() as s:
        return (await s.execute(text(
            "SELECT id, queue_name, status FROM procrastinate_jobs WHERE task_name = :t ORDER BY id"
        ), {"t": task})).all()


async def _clear_jobs() -> None:
    from sqlalchemy import text

    from stf_v3.db import SessionLocal

    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM procrastinate_jobs WHERE task_name LIKE 'knowledge.%'"))
        await s.commit()


async def _seed_manual(root: pathlib.Path, with_index: bool = False) -> str:
    """Registers a copied-from-V2 style manual (uploaded_by NULL) with files."""
    from sqlalchemy import text

    from stf_v3.db import SessionLocal

    mid = str(uuid.uuid4())
    dirname = f"Honda Jazz {mid[:8]}"
    mdir = root / dirname
    mdir.mkdir(parents=True, exist_ok=True)
    md = FIXTURES / "manual_small.md"
    shutil.copy(md, mdir / f"{mid}.md")
    md_path = f"{dirname}/{mid}.md"
    if with_index:
        idx = mdir / "index"
        idx.mkdir()
        shutil.copy(md, idx / f"{mid}.md")
        (idx / f"{mid}.index.yaml").write_text(
            f"manual_id: {mid}\nsource:\n  content_file: {mid}.md\nfaults: []\ntree:\n"
            "- node_id: n1\n  title: General Information\n  node_type: chapter\n  subsystem: general\n"
            "  page_range: [1, 4]\n  md_lines: [8, 20]\n  summary: intro\n  children: []\n"
            "- node_id: n2\n  title: Fuel System\n  node_type: chapter\n  subsystem: fuel\n"
            "  page_range: [5, 9]\n  md_lines: [20, 30]\n  summary: fuel\n  children: []\n",
            encoding="utf-8",
        )
        md_path = f"{dirname}/index/{mid}.md"
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO manuals (id, uploaded_by, filename, file_hash, manufacturer, vehicle_model, status, "
            "file_size_bytes, page_count, md_file_path, pdf_file_path) VALUES (:id, NULL, 'seed.pdf', :h, "
            "'Honda', 'Jazz', 'ingested', 10, 12, :md, :pdf)"
        ), {"id": mid, "h": uuid.uuid4().hex * 2, "md": md_path, "pdf": f"uploads/{mid}.pdf"})
        await s.commit()
    return mid


# ── read endpoints ──


async def test_list_get_toc_search_legacy_and_index_track(client, workshop_with_codes, _manual_root) -> None:  # type: ignore[no-untyped-def]
    """Members can list, read status, get the toc and search; index track is
    used when a sidecar exists (T-1 reader path, T-19)."""
    _, _, tech = await _setup(client, workshop_with_codes)
    legacy = await _seed_manual(_manual_root)
    indexed = await _seed_manual(_manual_root, with_index=True)
    r = await client.get("/v3/manuals", headers=tech)
    assert r.status_code == 200 and {m["id"] for m in r.json()} == {legacy, indexed}
    assert all(m["seed"] is True for m in r.json())
    r = await client.get(f"/v3/manuals/{legacy}/toc", headers=tech)
    assert r.status_code == 200 and r.json()["index_track"] is False
    assert "Fuel Pump Troubleshooting" in r.json()["toc"]
    r = await client.get(f"/v3/manuals/{indexed}/toc", headers=tech)
    assert r.json()["index_track"] is True and "Fuel System" in r.json()["toc"]
    r = await client.get("/v3/manuals/search", headers=tech, params={"manual_id": legacy, "q": "p0171"})
    assert r.status_code == 200 and r.json()["total_hits"] >= 1
    assert "2-2-dtc-p0171" in r.json()["hits"][0]["section"]
    r = await client.get("/v3/manuals/search", headers=tech, params={"manual_id": indexed, "q": "320 kPa"})
    assert r.json()["index_track"] is True and r.json()["hits"][0]["section"] in ("n1", "n2")
    r = await client.get("/v3/manuals/search", headers=tech, params={"manual_id": indexed, "q": "unicorn"})
    assert r.json()["total_hits"] == 0
    r = await client.get(f"/v3/manuals/{uuid.uuid4()}", headers=tech)
    assert r.status_code == 404


async def test_library_requires_membership(client, workshop_with_codes) -> None:  # type: ignore[no-untyped-def]
    """A user with no workshop membership cannot read the library."""
    _, manager, _ = await _setup(client, workshop_with_codes)
    assert (await client.get("/v3/manuals", headers=manager)).status_code == 200
    assert (await client.get("/v3/manuals")).status_code == 401


# ── upload ──


async def test_manager_upload_queues_gpu_job(client, workshop_with_codes, _manual_root, opened_queue) -> None:  # type: ignore[no-untyped-def]
    """Upload → 201 queued, PDF stored under uploads/, one gpu job (T-19)."""
    await _clear_jobs()
    _, manager, tech = await _setup(client, workshop_with_codes)
    r = await client.post("/v3/manuals", headers=manager, **_form(make_pdf(3), factory_code="GK5"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "queued" and body["page_count"] == 3 and body["job_id"]
    assert body["seed"] is False and body["canonical_name"] == "Honda Jazz"
    assert (_manual_root / "uploads" / f"{body['id']}.pdf").is_file()
    jobs = await _jobs()
    assert len(jobs) == 1 and jobs[0].queue_name == "gpu" and jobs[0].status == "todo"
    assert jobs[0].id == body["job_id"]
    # technician cannot upload; same content again → 409
    assert (await client.post("/v3/manuals", headers=tech, **_form(make_pdf(3)))).status_code == 403
    r = await client.post("/v3/manuals", headers=manager, **_form(make_pdf(3)))
    assert r.status_code == 409 and r.json()["code"] == "manual_exists"


async def test_bad_pdfs_rejected_nothing_stored(client, workshop_with_codes, _manual_root, opened_queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-10: non-PDF / corrupt / too many pages → 422, no file, no job."""
    from stf_v3.settings import settings

    await _clear_jobs()
    _, manager, _ = await _setup(client, workshop_with_codes)
    monkeypatch.setattr(settings, "manual_max_pages", 2)
    cases = [(b"{}", "not_a_pdf"), (b"%PDF-1.7 broken", "pdf_unreadable"), (make_pdf(3), "pdf_too_many_pages")]
    for payload, code in cases:
        r = await client.post("/v3/manuals", headers=manager, **_form(payload))
        assert r.status_code == 422 and r.json()["code"] == code, r.text
    assert not list(_manual_root.rglob("*.pdf")) and await _jobs() == []
    assert (await client.get("/v3/manuals", headers=manager)).json() == []


async def test_defer_failure_undoes_registration(client, workshop_with_codes, _manual_root, opened_queue, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-3 (FM-5): if queuing fails, no row, no file, clear 503."""
    from stf_v3.knowledge import tasks

    await _clear_jobs()
    _, manager, _ = await _setup(client, workshop_with_codes)

    async def boom(_mid):  # type: ignore[no-untyped-def]
        raise RuntimeError("queue down")

    monkeypatch.setattr(tasks, "defer_ingest", boom)
    r = await client.post("/v3/manuals", headers=manager, **_form(make_pdf(1)))
    assert r.status_code == 503 and r.json()["code"] == "queue_unavailable"
    assert (await client.get("/v3/manuals", headers=manager)).json() == []
    assert not list(_manual_root.rglob("*.pdf")) and await _jobs() == []


async def test_concurrent_same_pdf_registers_once(client, workshop_with_codes, _manual_root, opened_queue) -> None:  # type: ignore[no-untyped-def]
    """T-7 (FM-9): two simultaneous uploads of one PDF → one row, one job."""
    await _clear_jobs()
    _, manager, _ = await _setup(client, workshop_with_codes)
    pdf = make_pdf(2, marker=b"race")
    r1, r2 = await asyncio.gather(
        client.post("/v3/manuals", headers=manager, **_form(pdf, name="a.pdf")),
        client.post("/v3/manuals", headers=manager, **_form(pdf, name="b.pdf")),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    assert len((await client.get("/v3/manuals", headers=manager)).json()) == 1
    assert len(await _jobs()) == 1 and len(list(_manual_root.rglob("*.pdf"))) == 1


# ── delete ──


async def test_delete_seed_forbidden_own_upload_removed(client, workshop_with_codes, _manual_root, opened_queue) -> None:  # type: ignore[no-untyped-def]
    """T-2 (FM-4) + T-5 (FM-10): seed → 403; own upload → row, file and job gone."""
    await _clear_jobs()
    _, manager, tech = await _setup(client, workshop_with_codes)
    seed = await _seed_manual(_manual_root)
    r = await client.delete(f"/v3/manuals/{seed}", headers=manager)
    assert r.status_code == 403 and r.json()["code"] == "seed_protected"
    assert (await client.get(f"/v3/manuals/{seed}", headers=manager)).status_code == 200
    up = (await client.post("/v3/manuals", headers=manager, **_form(make_pdf(1)))).json()
    assert (await client.delete(f"/v3/manuals/{up['id']}", headers=tech)).status_code == 403
    assert (await client.delete(f"/v3/manuals/{up['id']}", headers=manager)).status_code == 204
    assert (await client.get(f"/v3/manuals/{up['id']}", headers=manager)).status_code == 404
    assert not (_manual_root / "uploads" / f"{up['id']}.pdf").exists()
    jobs = await _jobs()
    assert jobs and jobs[0].status in ("cancelled", "aborting", "aborted", "failed")


async def test_ingest_runner_aborts_when_row_deleted(_manual_root, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-5 (FM-10/34): the runner stops between stages once the row is gone."""
    from stf_v3.knowledge import ingest
    from stf_v3.settings import settings

    monkeypatch.setattr(settings, "manual_work_dir", str(tmp_path / "work"))
    calls: list = []
    monkeypatch.setattr(ingest, "_row", lambda mid: None if calls else (calls.append(1) or {
        "id": mid, "manufacturer": "H", "vehicle_model": "J", "factory_code": None,
        "status": "queued", "pdf_file_path": "uploads/x.pdf", "filename": "x.pdf"}))
    monkeypatch.setattr(ingest, "preflight", lambda root: None)
    monkeypatch.setattr(ingest, "_update", lambda *a, **k: None)
    (_manual_root / "uploads").mkdir()
    (_manual_root / "uploads" / "x.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(ingest, "run_mineru", lambda pdf, out, t: out)
    with pytest.raises(ingest.IngestCancelled):
        ingest.run_ingest(uuid.uuid4(), context=None)
    assert not list((tmp_path / "work").glob("*"))

"""Offline unit tests for the knowledge module (PROD-06).

Covers: manual reader (T-1 fixture parsing), task registration and queue
names (T-6), retry strategy never retrying permanent errors (T-6),
upload-time PDF validation (T-10), disk preflight (T-10), process-group
timeout (T-12), atomic install and staging cleanup (T-5), summary cache
isolation (T-8), import boundary (T-16), copy-script verify logic (T-1 /
T-17).

Author: Xiangzhu Yan
"""

import io
import json
import pathlib
import sys
import time
import uuid
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SMALL_MD = (FIXTURES / "manual_small.md").read_text(encoding="utf-8")


def make_pdf(pages: int = 2) -> bytes:
    """Builds a tiny valid PDF with ``pages`` blank pages."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ── T-1 / T-16: readers work on a small Markdown, nothing heavy imported ──


def test_manual_fs_parses_fixture_tree_and_sections(tmp_path: pathlib.Path) -> None:
    """Frontmatter, heading tree, section extraction and image refs resolve."""
    from stf_v3.knowledge import manual_fs

    fm = manual_fs.parse_frontmatter(SMALL_MD)
    assert fm["manufacturer"] == "Honda" and fm["vehicle_model"] == "Jazz"
    tree = manual_fs.parse_heading_tree(SMALL_MD)
    assert [n.title for n in tree] == ["1 General Information", "2 Fuel System", "3 Electrical"]
    flat = manual_fs._flatten_tree(tree)  # noqa: SLF001
    assert len(flat) == 8
    section = manual_fs.extract_section(SMALL_MD, flat[2].slug)   # 1.2 Maintenance Schedule
    assert section is not None and "spark plugs" in section
    # image refs resolve only when the file exists next to the manual
    img_dir = tmp_path / "images" / "manual_small"
    img_dir.mkdir(parents=True)
    (img_dir / "p3-1.png").write_bytes(b"\x89PNG")
    refs = manual_fs.resolve_image_refs(SMALL_MD, tmp_path)
    assert any("p3-1.png" in str(r) for r in refs)


def test_api_process_never_imports_pipeline_or_fitz() -> None:
    """FM-29: importing the app must not pull the heavy pipeline (T-16)."""
    for mod in list(sys.modules):
        if mod.startswith("stf_v3.knowledge.pipeline") or mod == "fitz":
            del sys.modules[mod]
    import stf_v3.main  # noqa: F401

    assert not any(m.startswith("stf_v3.knowledge.pipeline") for m in sys.modules)
    assert "fitz" not in sys.modules


# ── T-6: task registration, queue names, retry strategy ──


def test_gpu_tasks_registered_on_gpu_queue_only() -> None:
    """Both knowledge tasks are on the ``gpu`` queue; heartbeat on default."""
    from stf_v3.jobs.app import app
    from stf_v3.knowledge import tasks

    app.perform_import_paths()
    names = {t.name: t for t in app.tasks.values()}
    assert names[tasks.INGEST_TASK].queue == "gpu"
    assert names[tasks.HEARTBEAT_TASK].queue == "gpu"
    assert names["jobs.heartbeat"].queue == "default"
    assert names["jobs.drill_sleep"].queue == "default"


def test_compose_container_worker_listens_default_only() -> None:
    """FM-8 guard: the container worker command names only the default queue."""
    compose_path = pathlib.Path(__file__).parents[2] / "infra" / "docker-compose.v3.yml"
    if not compose_path.is_file():
        pytest.skip("compose file not present (running inside the image)")
    compose = compose_path.read_text()
    worker_cmd = compose.split("stf-v3-worker:", 1)[1].split("restart:", 1)[0]
    assert '"-q", "default"' in worker_cmd and "gpu" not in worker_cmd


def test_retry_strategy_never_retries_permanent_errors() -> None:
    """Permanent errors → no retry; transient → retry until 3 attempts."""
    from stf_v3.knowledge.tasks import IngestRetry, PermanentIngestError

    strategy = IngestRetry(max_attempts=3, wait_s=60)
    job1 = SimpleNamespace(attempts=1)
    assert strategy.get_retry_decision(exception=PermanentIngestError("x"), job=job1) is None
    d = strategy.get_retry_decision(exception=RuntimeError("t"), job=job1)
    assert d is not None
    assert strategy.get_retry_decision(exception=RuntimeError("t"), job=SimpleNamespace(attempts=3)) is None


def test_wrong_worker_preflight_is_permanent(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a MinerU binary the preflight raises a non-retryable error."""
    from stf_v3.knowledge import ingest
    from stf_v3.knowledge.tasks import PermanentIngestError
    from stf_v3.settings import settings

    monkeypatch.setattr(settings, "mineru_bin", str(tmp_path / "no-such-mineru"))
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    with pytest.raises(PermanentIngestError, match="wrong worker"):
        ingest.preflight(tmp_path)


# ── T-10: upload validation + disk preflight ──


def test_validate_pdf_accepts_small_pdf_and_rejects_bad_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """Good PDF → page count; non-PDF, corrupt, too many pages, too large → ApiError."""
    from stf_v3.errors import ApiError
    from stf_v3.knowledge import service
    from stf_v3.settings import settings

    assert service.validate_pdf(make_pdf(3)) == 3
    for payload, code in [(b'{"a":1}', "not_a_pdf"), (b"%PDF-1.4 garbage", "pdf_unreadable")]:
        with pytest.raises(ApiError) as e:
            service.validate_pdf(payload)
        assert e.value.code == code
    monkeypatch.setattr(settings, "manual_max_pages", 2)
    with pytest.raises(ApiError) as e:
        service.validate_pdf(make_pdf(3))
    assert e.value.code == "pdf_too_many_pages"
    monkeypatch.setattr(settings, "manual_max_upload_bytes", 10)
    with pytest.raises(ApiError) as e:
        service.validate_pdf(make_pdf(1))
    assert e.value.status_code == 413


def test_disk_preflight_refuses_below_threshold(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FM-13: free space below the threshold is a permanent refusal."""
    import shutil

    from stf_v3.knowledge import ingest
    from stf_v3.knowledge.tasks import PermanentIngestError
    from stf_v3.settings import settings

    fake_bin = tmp_path / "mineru"
    fake_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(settings, "mineru_bin", str(fake_bin))
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "manual_min_free_gb", 30)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=1, used=1, free=5e9))
    with pytest.raises(PermanentIngestError, match="disk preflight"):
        ingest.preflight(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=1, used=1, free=100e9))
    ingest.preflight(tmp_path)   # passes


# ── T-12: external process timeout kills the whole group ──


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_run_mineru_kills_process_group_on_timeout(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung 'MinerU' (sleep) is killed with its children after the timeout."""
    from stf_v3.knowledge import ingest
    from stf_v3.settings import settings

    fake = tmp_path / "mineru"
    fake.write_text("#!/bin/sh\nsleep 600 &\nsleep 600\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "mineru_bin", str(fake))
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        ingest.run_mineru(tmp_path / "x.pdf", tmp_path / "out", timeout_s=2)
    assert time.monotonic() - t0 < 10


# ── T-5: atomic install ──


def test_install_is_atomic_and_discards_stale_staging(tmp_path: pathlib.Path) -> None:
    """A leftover staging dir is discarded; the final dir appears whole."""
    from stf_v3.knowledge import ingest

    mid = uuid.uuid4()
    out = tmp_path / "out"
    (out / "images").mkdir(parents=True)
    (out / f"{mid}.md").write_text("# m")
    (out / f"{mid}.index.yaml").write_text("manual_id: x")
    (out / "index_build_report.json").write_text("{}")
    (out / "images" / "a.png").write_bytes(b"png")
    dest = tmp_path / "vol" / str(mid)
    stale = tmp_path / "vol" / f".staging-{mid}"
    stale.mkdir(parents=True)
    (stale / "half").write_text("crash leftover")
    ingest._install(mid, out, dest)  # noqa: SLF001
    assert (dest / "index" / f"{mid}.md").is_file()
    assert (dest / "index" / "images" / "a.png").read_bytes() == b"png"
    assert not stale.exists() and not list((tmp_path / "vol").glob(".staging-*"))
    # re-install over an existing dir also leaves no temp dirs behind
    ingest._install(mid, out, dest)  # noqa: SLF001
    assert not list((tmp_path / "vol").glob(".old-*"))


# ── T-8: summary cache is per manual and gate-checked ──


def test_summary_cache_roundtrip_isolated_per_path(tmp_path: pathlib.Path) -> None:
    """Two manuals with the same node ids use separate cache files."""
    from stf_v3.knowledge.pipeline import summarize

    a, b = tmp_path / "a" / "summaries.json", tmp_path / "b" / "summaries.json"
    a.parent.mkdir()
    b.parent.mkdir()
    summarize._save_cache(a, {"n1": "Yamaha oil change"})  # noqa: SLF001
    assert summarize._load_cache(a) == {"n1": "Yamaha oil change"}  # noqa: SLF001
    assert summarize._load_cache(b) == {}  # noqa: SLF001
    assert not list(tmp_path.rglob("*.part"))


# ── T-1 / T-17: copy script verify logic ──


def test_copy_script_verify_detects_missing_and_mismatch(tmp_path: pathlib.Path) -> None:
    """copy_tree copies everything; verify_tree flags a changed byte."""
    sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
    import copy_manuals_from_v2 as cp

    src, dst = tmp_path / "src" / "M", tmp_path / "dst" / "M"
    (src / "index").mkdir(parents=True)
    (src / "m.md").write_text("hello")
    (src / "index" / "m.index.yaml").write_text("manual_id: m")
    assert cp.copy_tree(src, dst) == 2
    assert cp.copy_tree(src, dst) == 0          # idempotent
    checked, problems = cp.verify_tree(src, dst)
    assert checked == 2 and problems == []
    (dst / "m.md").write_text("hellO")
    _, problems = cp.verify_tree(src, dst)
    assert problems == ["hash mismatch m.md"]
    (dst / "index" / "m.index.yaml").unlink()
    _, problems = cp.verify_tree(src, dst)
    assert "missing index/m.index.yaml" in problems


def test_copy_script_has_no_v2_database_access() -> None:
    """FM-35: the script reads a JSON export; it never connects to V2's DB."""
    text = (pathlib.Path(__file__).parents[1] / "scripts" / "copy_manuals_from_v2.py").read_text()
    assert "stf_diagnosis" not in text.replace("-d stf_diagnosis", "")   # only in the usage comment
    assert "POSTGRES_PASSWORD" not in text and "stf_user:" not in text
    assert json.dumps({"ok": True})  # keep json import meaningful

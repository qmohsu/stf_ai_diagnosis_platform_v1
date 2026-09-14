"""One-step manual ingest (PROD-06, decision D1 2026-09-14): runs ONLY in
the host GPU worker.

Stages (each written to ``manuals.pages_phase`` as it starts)::

    converting  MinerU geometry pass (external CLI, own env, GPU)
    building    text-authority content build (PyMuPDF + MinerU stream)
    indexing    heading tree + entities → index sidecar
    summarizing per-node summaries via the cloud model (cached per manual)
    gates       eight invariants I1–I8; any failure = ingest failed
    installing  atomic move of the finished artefacts into the volume

Failure handling (round-2 decisions):
* preflight refuses to start when free disk < ``manual_min_free_gb``,
  MinerU binary missing (wrong worker), or the cloud key is absent —
  permanent errors, no retry (FM-8, FM-12, FM-13).
* MinerU output and accepted summaries persist in the manual's work dir,
  so a retry after a later-stage failure skips the GPU pass (FM-12); the
  work dir is removed only on success and on cancel — a permanent
  failure keeps it so a manual retry resumes instead of re-paying.
* between stages the job checks the abort flag and that its row still
  exists (FM-10 / FM-34); on abort it cleans up and exits without retry.
* install = write to ``<manual dir>/.staging-<id>`` then one ``rename``
  (FM-7); a leftover staging dir from a crash is discarded on rerun.
* the external MinerU process runs in its own session and the whole
  process group is killed on timeout (FM-20).

Author: Xiangzhu Yan
"""

import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from stf_v3.knowledge.tasks import PermanentIngestError
from stf_v3.settings import settings

log = structlog.get_logger("stf_v3.knowledge.ingest")

STAGES = ("converting", "building", "indexing", "summarizing", "gates", "installing")
_engine: Optional[Engine] = None


class IngestCancelled(PermanentIngestError):
    """The manual was deleted / the job aborted while running."""


# ── sync DB helpers (worker process only) ────────────────────────────


def _db() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_size=1, pool_pre_ping=True)
    return _engine


def _row(manual_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    with _db().connect() as conn:
        r = conn.execute(
            text("SELECT id, manufacturer, vehicle_model, factory_code, status, "
                 "pdf_file_path, filename FROM manuals WHERE id = :id"),
            {"id": manual_id},
        ).mappings().first()
    return dict(r) if r else None


def _update(manual_id: uuid.UUID, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with _db().begin() as conn:
        conn.execute(
            text(f"UPDATE manuals SET {sets}, updated_at = now() WHERE id = :id"),
            {"id": manual_id, **fields},
        )


def _set_stage(manual_id: uuid.UUID, stage: str) -> None:
    _update(
        manual_id, status="converting", pages_phase=stage,
        pages_processed=STAGES.index(stage), pages_total=len(STAGES),
    )
    log.info("ingest.stage", manual_id=str(manual_id), stage=stage)


# ── preflight ────────────────────────────────────────────────────────


def preflight(root: Path) -> None:
    """Refuses to start when the environment cannot finish the job."""
    if not shutil.which(settings.mineru_bin) and not Path(settings.mineru_bin).is_file():
        raise PermanentIngestError(
            f"wrong worker: MinerU not found at {settings.mineru_bin} "
            "(gpu queue must be consumed by the host GPU worker)"
        )
    if not settings.openrouter_api_key:
        raise PermanentIngestError("OPENROUTER_API_KEY missing: summaries cannot run")
    free_gb = shutil.disk_usage(root).free / 1e9
    if free_gb < settings.manual_min_free_gb:
        raise PermanentIngestError(
            f"disk preflight refused: {free_gb:.1f} GB free < {settings.manual_min_free_gb} GB"
        )


# ── external MinerU ──────────────────────────────────────────────────


def run_mineru(pdf: Path, out_dir: Path, timeout_s: int) -> Path:
    """Runs MinerU in its own process group; kills the group on timeout.

    Returns:
        The engine directory containing ``*_content_list_v2.json``.
    """
    existing = list(out_dir.rglob("*_content_list_v2.json"))
    if existing:
        log.info("ingest.mineru_skipped", reason="output present", dir=str(out_dir))
        return existing[0].parent
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("CUDA_VISIBLE_DEVICES", os.environ.get("STF_V3_GPU_DEVICE", "1"))
    proc = subprocess.Popen(
        [settings.mineru_bin, "-p", str(pdf), "-o", str(out_dir), "-b", "hybrid-engine"],
        start_new_session=True, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        _, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise RuntimeError(f"MinerU timed out after {timeout_s}s (process group killed)")
    if proc.returncode != 0:
        raise RuntimeError(f"MinerU exited {proc.returncode}: {(err or '')[-800:]}")
    found = list(out_dir.rglob("*_content_list_v2.json"))
    if not found:
        raise RuntimeError("MinerU produced no content_list_v2.json")
    return found[0].parent


# ── the chain ────────────────────────────────────────────────────────


def _check_alive(manual_id: uuid.UUID, context: Any) -> None:
    """Abort between stages if the job was aborted or the row vanished."""
    if context is not None and hasattr(context, "should_abort") and context.should_abort():
        raise IngestCancelled("job aborted")
    if _row(manual_id) is None:
        raise IngestCancelled("manual row deleted during ingest")


class _Ticker(threading.Thread):
    """Keeps the worker status file fresh while a long ingest runs (FM-18)."""

    def __init__(self, manual_id: uuid.UUID, interval_s: float = 30.0) -> None:
        super().__init__(daemon=True, name="gpu-worker-ticker")
        self.manual_id, self.interval_s = manual_id, interval_s
        self._stop = threading.Event()

    def run(self) -> None:
        from stf_v3.knowledge.tasks import write_status

        while not self._stop.is_set():
            try:
                write_status(busy_with=str(self.manual_id))
            except OSError as exc:  # volume hiccup: log, keep going
                log.warning("gpu_worker.status_write_failed", error=str(exc))
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()


def run_ingest(manual_id: uuid.UUID, context: Any = None) -> None:
    """Executes the whole chain for one manual; raises on failure."""
    ticker = _Ticker(manual_id)
    ticker.start()
    try:
        _run_ingest(manual_id, context)
    finally:
        ticker.stop()


def _run_ingest(manual_id: uuid.UUID, context: Any = None) -> None:
    root = Path(settings.manual_storage_path).resolve()
    work = Path(settings.manual_work_dir).resolve() / str(manual_id)
    started = time.monotonic()
    row = _row(manual_id)
    if row is None:
        raise IngestCancelled("manual row missing at start")
    try:
        preflight(root)
        pdf = root / row["pdf_file_path"]
        if not pdf.is_file():
            raise PermanentIngestError(f"source PDF missing: {pdf}")
        work.mkdir(parents=True, exist_ok=True)

        _set_stage(manual_id, "converting")
        engine_dir = run_mineru(pdf, work / "mineru", settings.mineru_timeout_s)
        _check_alive(manual_id, context)

        from stf_v3.knowledge.pipeline.build import run_build
        from stf_v3.knowledge.pipeline.index_build import run_index_build

        _set_stage(manual_id, "building")
        out_dir = work / "out"
        fm_path = work / "frontmatter.md"
        fm_path.write_text(
            "---\n"
            f"manufacturer: {row['manufacturer']}\n"
            f"vehicle_model: {row['vehicle_model']}\n"
            + (f"factory_code: {row['factory_code']}\n" if row["factory_code"] else "")
            + "---\n",
            encoding="utf-8",
        )
        run_build(pdf, engine_dir, out_dir, fm_path)   # raises on I0
        _check_alive(manual_id, context)

        # indexing + summarizing + gates happen inside run_index_build; the
        # stage marker advances at its start and the summary cache lives in
        # this manual's work dir (FM-11: keyed by manual, FM-12: reusable).
        _set_stage(manual_id, "indexing")
        final_dir = root / str(manual_id) / "index"
        prior = final_dir / f"{manual_id}.index.yaml"
        cache = work / "summaries.json"
        _set_stage(manual_id, "summarizing")
        # The copied pipeline reads the cloud key straight from the process
        # environment under V2's name; bridge V3's (aliased) setting to it.
        os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)
        ok = run_index_build(
            engine_dir, out_dir / f"{manual_id}.md", str(manual_id), out_dir,
            applicability={"manufacturer": row["manufacturer"], "models": [row["vehicle_model"]]},
            with_summaries=True, model=settings.summary_model,
            item_lines_path=out_dir / "item_lines.json",
            reuse_summaries_from=prior if prior.is_file() else None,
            summary_cache_path=cache,
        )
        _set_stage(manual_id, "gates")
        report = _read_report(out_dir / "index_build_report.json")
        if not ok:
            failed = [g["gate"] for g in report.get("gates", []) if not g.get("passed")]
            if not report:
                raise RuntimeError(
                    "index build aborted before validation (no build report; "
                    "see worker log for the [index] line) — retryable"
                )
            raise PermanentIngestError(f"index gates failed: {', '.join(failed) or 'unknown'}")
        _check_alive(manual_id, context)

        _set_stage(manual_id, "installing")
        _install(manual_id, out_dir, root / str(manual_id))
        elapsed = int(time.monotonic() - started)
        _update(
            manual_id, status="ingested", pages_phase=None, pages_processed=len(STAGES),
            error_message=None, converter="mineru-3.4.4/hybrid-engine + index-track",
            md_file_path=str((root / str(manual_id) / "index" / f"{manual_id}.md").relative_to(root)),
            section_count=int(report.get("tree", {}).get("nodes") or 0),
            warnings=json.dumps({"elapsed_s": elapsed, "summaries": report.get("summaries")}),
        )
        shutil.rmtree(work, ignore_errors=True)
        log.info("ingest.done", manual_id=str(manual_id), elapsed_s=elapsed)
    except IngestCancelled as exc:
        shutil.rmtree(work, ignore_errors=True)
        log.warning("ingest.cancelled", manual_id=str(manual_id), reason=str(exc))
        raise
    except PermanentIngestError as exc:
        # Work dir is KEPT: a 40-minute MinerU pass must survive a config
        # mistake so `queue_ops.sh retry <job>` resumes instead of re-paying.
        # Orphan work dirs are reclaimed by the PROD-15 cleanup task.
        _fail(manual_id, f"{exc}", permanent=True)
        raise
    except Exception as exc:  # noqa: BLE001 - transient: keep work dir for retry
        _fail(manual_id, f"{type(exc).__name__}: {exc}", permanent=False)
        raise


def _read_report(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fail(manual_id: uuid.UUID, reason: str, permanent: bool) -> None:
    if _row(manual_id) is None:
        return
    _update(
        manual_id, status="failed", error_message=reason[:2000],
        pages_phase=("failed" if permanent else "retrying"),
    )
    log.error("ingest.failed", manual_id=str(manual_id), permanent=permanent, reason=reason[:300])


def _install(manual_id: uuid.uuid4, out_dir: Path, dest: Path) -> None:  # type: ignore[valid-type]
    """Atomic install: stage next to the destination, then rename (FM-7)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".staging-{manual_id}"
    if staging.exists():
        shutil.rmtree(staging)
    idx = staging / "index"
    idx.mkdir(parents=True)
    shutil.copy2(out_dir / f"{manual_id}.md", idx)
    shutil.copy2(out_dir / f"{manual_id}.index.yaml", idx)
    shutil.copy2(out_dir / "index_build_report.json", idx)
    if (out_dir / "images").is_dir():
        shutil.copytree(out_dir / "images", idx / "images")
    (staging / "installed_at.txt").write_text(dt.datetime.now(dt.timezone.utc).isoformat())
    if dest.exists():
        old = dest.parent / f".old-{manual_id}"
        if old.exists():
            shutil.rmtree(old)
        os.rename(dest, old)
        os.rename(staging, dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.rename(staging, dest)

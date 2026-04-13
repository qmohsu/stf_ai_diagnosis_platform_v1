"""Background pipeline for manual PDF conversion and ingestion.

Orchestrates: PDF upload → marker-pdf conversion → filesystem
storage → pgvector RAG ingestion.  All heavy work runs in a
background ``asyncio`` task so the upload endpoint returns
immediately.

Conversion is performed by a **host-side worker** process
(``scripts/marker_worker.py``) that watches a shared queue
directory.  The container writes a request JSON and polls for
the result JSON, avoiding the need to install marker-pdf
(+ PyTorch) inside the container image.

Author: Li-Ta Hsu
Date: April 2026
"""

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import UUID

import structlog

from app.config import settings
from app.db.session import SessionLocal
from app.models_db import Manual, RagChunk
from app.rag.chunker import Chunker

logger = structlog.get_logger(__name__)

# Default chunker for RAG ingestion.
_DEFAULT_CHUNK_SIZE = 500
_DEFAULT_OVERLAP = 50

# Queue subdirectory inside manual_storage_path.
_QUEUE_DIR = ".queue"


def compute_file_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*.

    Args:
        data: Raw file bytes.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(data).hexdigest()


def save_uploaded_pdf(
    data: bytes,
    manual_id: UUID,
) -> str:
    """Persist uploaded PDF to the uploads staging area.

    Args:
        data: Raw PDF bytes.
        manual_id: UUID of the ``Manual`` row.

    Returns:
        Relative path (from ``manual_storage_path``) to the
        saved file, e.g. ``uploads/{manual_id}.pdf``.
    """
    uploads_dir = os.path.join(
        settings.manual_storage_path, "uploads",
    )
    os.makedirs(uploads_dir, exist_ok=True)

    rel_path = f"uploads/{manual_id}.pdf"
    abs_path = os.path.join(
        settings.manual_storage_path, rel_path,
    )
    with open(abs_path, "wb") as f:
        f.write(data)

    logger.info(
        "manual.pdf_saved",
        manual_id=str(manual_id),
        size=len(data),
        path=rel_path,
    )
    return rel_path


async def run_conversion_and_ingestion(
    manual_id: UUID,
) -> None:
    """Background task: convert PDF then ingest to pgvector.

    Updates the ``Manual`` row status through each stage.
    On any failure the status is set to ``'failed'`` with
    an error message.

    Args:
        manual_id: UUID of the ``Manual`` row to process.
    """
    log = logger.bind(manual_id=str(manual_id))

    db = SessionLocal()
    try:
        manual = db.query(Manual).get(manual_id)
        if manual is None:
            log.error("manual.not_found")
            return

        # ── Stage 1: marker-pdf conversion ───────────
        manual.status = "converting"
        db.commit()
        log.info("manual.converting")

        try:
            result = await _run_marker_convert(
                manual.pdf_file_path,
                manual_id,
                log,
            )
        except Exception as exc:
            manual.status = "failed"
            manual.error_message = (
                f"Conversion error: {exc!s:.500}"
            )
            db.commit()
            log.error(
                "manual.conversion_failed",
                error=str(exc),
            )
            return

        # Update manual with conversion metadata.
        vehicle_model = result.get("vehicle_model", "")
        output_path = result.get("output_path", "")
        manual.md_file_path = output_path
        manual.vehicle_model = vehicle_model
        manual.page_count = result.get("page_count")
        manual.section_count = result.get("section_count")
        manual.language = result.get("language")
        manual.converter = result.get(
            "converter", "marker-pdf",
        )
        db.commit()
        log.info(
            "manual.converted",
            vehicle_model=vehicle_model,
            pages=manual.page_count,
        )

        # ── Stage 2: RAG ingestion ──────────────────
        md_abs = os.path.join(
            settings.manual_storage_path, output_path,
        )
        try:
            chunk_count = await _run_ingestion(
                Path(md_abs), db, log,
            )
        except Exception as exc:
            manual.status = "failed"
            manual.error_message = (
                f"Ingestion error: {exc!s:.500}"
            )
            db.commit()
            log.error(
                "manual.ingestion_failed",
                error=str(exc),
            )
            return

        manual.chunk_count = chunk_count
        manual.status = "ingested"
        db.commit()
        log.info(
            "manual.ingested", chunk_count=chunk_count,
        )

    except Exception as exc:
        log.error(
            "manual.pipeline_error", error=str(exc),
        )
        try:
            manual = db.query(Manual).get(manual_id)
            if manual:
                manual.status = "failed"
                manual.error_message = (
                    f"Pipeline error: {exc!s:.500}"
                )
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


async def _run_marker_convert(
    pdf_rel: str,
    manual_id: UUID,
    log: structlog.BoundLogger,
) -> dict:
    """Request marker-pdf conversion via the host worker.

    Writes a request JSON to the shared ``.queue/`` directory
    and polls for a result JSON written by the host-side
    ``marker_worker.py``.

    Args:
        pdf_rel: Relative path to the PDF inside
            ``manual_storage_path`` (e.g. ``uploads/xxx.pdf``).
        manual_id: UUID used as the queue filename.
        log: Bound logger.

    Returns:
        Dict with conversion metadata (vehicle_model,
        language, page_count, section_count, image_count,
        output_path, dtc_codes).

    Raises:
        TimeoutError: If the worker doesn't respond within
            the configured timeout.
        RuntimeError: If the worker reports an error.
    """
    queue_dir = os.path.join(
        settings.manual_storage_path, _QUEUE_DIR,
    )
    os.makedirs(queue_dir, exist_ok=True)

    req_path = os.path.join(
        queue_dir, f"{manual_id}.request.json",
    )
    res_path = os.path.join(
        queue_dir, f"{manual_id}.result.json",
    )

    # Write conversion request.
    request: dict = {
        "pdf_path": pdf_rel,
        "use_llm": settings.manual_use_llm,
        "vehicle_model_subdir": True,
    }
    # Pass LLM credentials if LLM mode is enabled.
    if settings.manual_use_llm:
        request["openai_api_key"] = (
            settings.premium_llm_api_key
        )
        request["openai_base_url"] = (
            settings.premium_llm_base_url
        )
        request["openai_model"] = (
            settings.manual_llm_model
        )
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(request, f)

    log.info(
        "manual.conversion_requested",
        request_path=req_path,
    )

    # Poll for result.
    poll_interval = settings.marker_poll_interval_seconds
    timeout = settings.marker_timeout_seconds
    elapsed = 0

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if not os.path.isfile(res_path):
            continue

        with open(res_path, "r", encoding="utf-8") as f:
            result = json.load(f)

        # Clean up queue files.
        for p in (req_path, res_path):
            try:
                os.unlink(p)
            except OSError:
                pass

        if result.get("status") == "error":
            raise RuntimeError(
                result.get("message", "Unknown error"),
            )

        log.info(
            "manual.conversion_complete",
            vehicle_model=result.get("vehicle_model"),
            elapsed=elapsed,
        )
        return result

    # Timeout — clean up request file.
    try:
        os.unlink(req_path)
    except OSError:
        pass

    raise TimeoutError(
        f"Marker worker did not respond within "
        f"{timeout}s. Is marker-worker running?",
    )


async def _run_ingestion(
    md_path: Path,
    db: "Session",
    log: structlog.BoundLogger,
) -> int:
    """Ingest a converted markdown file into pgvector.

    Args:
        md_path: Path to the .md file.
        db: SQLAlchemy session.
        log: Bound logger.

    Returns:
        Number of chunks inserted.
    """
    from app.rag.ingest import process_file

    chunker = Chunker(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        overlap=_DEFAULT_OVERLAP,
    )
    stats = await process_file(
        md_path, db, chunker,
        describe_images=False,
    )
    return stats.get("inserted", 0)


def delete_manual_files(manual: Manual) -> None:
    """Remove all filesystem artefacts for a manual.

    Deletes the source PDF, the output markdown file, and the
    corresponding images directory.  Also removes associated
    ``rag_chunks`` rows from the database.

    Args:
        manual: The ``Manual`` ORM instance.
    """
    base = settings.manual_storage_path
    log = logger.bind(manual_id=str(manual.id))

    # Delete source PDF.
    if manual.pdf_file_path:
        pdf_abs = os.path.join(base, manual.pdf_file_path)
        if os.path.isfile(pdf_abs):
            os.unlink(pdf_abs)
            log.info(
                "manual.deleted_pdf", path=pdf_abs,
            )

    # Delete markdown and images.
    if manual.md_file_path:
        md_abs = os.path.join(base, manual.md_file_path)
        if os.path.isfile(md_abs):
            os.unlink(md_abs)
            log.info(
                "manual.deleted_md", path=md_abs,
            )

        # Images directory lives alongside the .md
        md_dir = os.path.dirname(md_abs)
        img_dir = os.path.join(md_dir, "images")
        if os.path.isdir(img_dir):
            shutil.rmtree(img_dir, ignore_errors=True)
            log.info(
                "manual.deleted_images", path=img_dir,
            )


def cleanup_orphan_files(
    grace_seconds: int = 3600,
) -> dict:
    """Remove orphaned manual files not backed by DB rows.

    Scans ``uploads/`` and vehicle-model subdirectories for
    files whose manual_id does not exist in the ``manuals``
    table, or whose row has ``status='failed'`` and is older
    than *grace_seconds*.

    Also removes empty vehicle-model directories.

    Args:
        grace_seconds: Keep failed rows for this many seconds
            so the user can see the error in the UI.
            Defaults to 1 hour.

    Returns:
        Dict with ``files_removed`` and ``dirs_removed`` counts.
    """
    from datetime import datetime, timezone

    base = settings.manual_storage_path
    db = SessionLocal()
    files_removed = 0
    dirs_removed = 0

    try:
        # Build sets of valid manual IDs and their paths.
        all_manuals = db.query(Manual).all()
        now = datetime.now(timezone.utc)

        # IDs that are actively in use (not stale-failed).
        active_ids: set[str] = set()
        # Paths referenced by active manuals.
        active_pdf_paths: set[str] = set()
        active_md_paths: set[str] = set()

        for m in all_manuals:
            # Keep failed rows within grace period.
            if m.status == "failed":
                age = (now - m.updated_at).total_seconds()
                if age < grace_seconds:
                    active_ids.add(str(m.id))
                    if m.pdf_file_path:
                        active_pdf_paths.add(
                            m.pdf_file_path,
                        )
                    continue
                # Stale failed — delete row + files.
                delete_manual_files(m)
                doc_id = Path(m.filename).stem
                delete_manual_chunks(doc_id, db)
                db.delete(m)
                files_removed += 1
                logger.info(
                    "cleanup.stale_failed_removed",
                    manual_id=str(m.id),
                )
                continue

            active_ids.add(str(m.id))
            if m.pdf_file_path:
                active_pdf_paths.add(m.pdf_file_path)
            if m.md_file_path:
                active_md_paths.add(m.md_file_path)

        db.commit()

        # Clean orphan PDFs in uploads/.
        uploads_dir = os.path.join(base, "uploads")
        if os.path.isdir(uploads_dir):
            for name in os.listdir(uploads_dir):
                rel = f"uploads/{name}"
                if rel not in active_pdf_paths:
                    abs_path = os.path.join(base, rel)
                    os.unlink(abs_path)
                    files_removed += 1
                    logger.info(
                        "cleanup.orphan_pdf",
                        path=rel,
                    )

        # Clean orphan vehicle-model dirs.
        skip_dirs = {"uploads", ".queue"}
        for entry in os.listdir(base):
            if entry.startswith(".") or entry in skip_dirs:
                continue
            entry_path = os.path.join(base, entry)
            if not os.path.isdir(entry_path):
                continue

            # Check if any active manual references
            # a path under this directory.
            has_active = any(
                p.startswith(entry + "/")
                for p in active_md_paths
            )
            if not has_active:
                shutil.rmtree(
                    entry_path, ignore_errors=True,
                )
                dirs_removed += 1
                logger.info(
                    "cleanup.orphan_dir",
                    path=entry,
                )

        # Clean stale queue files (older than 1 hour).
        queue_dir = os.path.join(base, _QUEUE_DIR)
        if os.path.isdir(queue_dir):
            for name in os.listdir(queue_dir):
                fp = os.path.join(queue_dir, name)
                if not os.path.isfile(fp):
                    continue
                age = now.timestamp() - os.path.getmtime(fp)
                if age > grace_seconds:
                    os.unlink(fp)
                    files_removed += 1
                    logger.info(
                        "cleanup.stale_queue_file",
                        path=name,
                    )

    except Exception as exc:
        db.rollback()
        logger.error(
            "cleanup.error", error=str(exc),
        )
    finally:
        db.close()

    if files_removed or dirs_removed:
        logger.info(
            "cleanup.done",
            files_removed=files_removed,
            dirs_removed=dirs_removed,
        )
    return {
        "files_removed": files_removed,
        "dirs_removed": dirs_removed,
    }


def delete_manual_chunks(
    doc_id: str, db: "Session",
) -> int:
    """Delete all RAG chunks for a given doc_id.

    Args:
        doc_id: The document identifier (filename stem).
        db: SQLAlchemy session.

    Returns:
        Number of rows deleted.
    """
    count = (
        db.query(RagChunk)
        .filter(RagChunk.doc_id == doc_id)
        .delete()
    )
    db.commit()
    logger.info(
        "manual.deleted_chunks",
        doc_id=doc_id,
        count=count,
    )
    return count

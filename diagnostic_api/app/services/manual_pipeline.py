"""Background pipeline for manual PDF conversion and ingestion.

Orchestrates: PDF upload → marker-pdf conversion → filesystem
storage → pgvector RAG ingestion.  All heavy work runs in a
background ``asyncio`` task so the upload endpoint returns
immediately.

A module-level semaphore serialises GPU-intensive conversions
to avoid OOM under concurrent uploads.

Author: Li-Ta Hsu
Date: April 2026
"""

import asyncio
import hashlib
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

# Serialise marker-pdf conversions (GPU-bound).
_conversion_semaphore = asyncio.Semaphore(1)

# Default chunker for RAG ingestion.
_DEFAULT_CHUNK_SIZE = 500
_DEFAULT_OVERLAP = 50


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

        pdf_abs = os.path.join(
            settings.manual_storage_path,
            manual.pdf_file_path,
        )

        try:
            result = await _run_marker_convert(
                pdf_abs, log,
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
        vehicle_model = result.vehicle_model
        rel_md = os.path.relpath(
            str(result.output_path),
            settings.manual_storage_path,
        )
        manual.md_file_path = rel_md
        manual.vehicle_model = vehicle_model
        manual.page_count = result.page_count
        manual.section_count = result.section_count
        manual.language = result.language
        manual.converter = (
            f"marker-pdf"
            if not settings.manual_use_llm
            else f"marker-pdf (LLM)"
        )
        db.commit()
        log.info(
            "manual.converted",
            vehicle_model=vehicle_model,
            pages=result.page_count,
        )

        # ── Stage 2: RAG ingestion ──────────────────
        try:
            chunk_count = await _run_ingestion(
                result.output_path, db, log,
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
    pdf_abs: str,
    log: structlog.BoundLogger,
) -> "ConversionResult":
    """Run marker-pdf conversion under the GPU semaphore.

    Args:
        pdf_abs: Absolute path to the source PDF.
        log: Bound logger.

    Returns:
        ConversionResult from marker_convert.convert().
    """
    from scripts.marker_convert import convert

    async with _conversion_semaphore:
        log.info("manual.marker_started")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: convert(
                pdf_path=pdf_abs,
                output_dir=settings.manual_storage_path,
                use_llm=settings.manual_use_llm,
                vehicle_model_subdir=True,
            ),
        )
        return result


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

"""Manual upload, conversion, and management endpoints.

Provides a CRUD interface for service manual PDFs: upload
triggers background marker-pdf conversion and RAG ingestion;
list/get/delete manage the library.

Author: Li-Ta Hsu
Date: April 2026
"""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Optional

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import get_current_user
from app.config import settings
from app.models_db import Manual, User
from app.services.manual_pipeline import (
    cleanup_orphan_files,
    compute_file_hash,
    delete_manual_files,
    run_conversion_and_ingestion,
    run_reingestion,
    save_uploaded_pdf,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

# ── Response schemas ─────────────────────────────────────────


class ManualSummary(BaseModel):
    """Compact manual metadata for list responses."""

    id: str
    filename: str
    vehicle_model: Optional[str] = None
    status: str
    file_size_bytes: int
    page_count: Optional[int] = None
    section_count: Optional[int] = None
    language: Optional[str] = None
    chunk_count: Optional[int] = None
    # Live per-page progress reported by marker-worker.  Both
    # are nullable; populated only while ``status='converting'``
    # (and pinned to ``page_count`` on success so the UI shows
    # N/N briefly during the chunking handoff).
    pages_processed: Optional[int] = None
    pages_total: Optional[int] = None
    # Current marker-pdf pipeline stage label (e.g. "Layout",
    # "OCR", "Recognition").  Populated only while
    # ``status='converting'``; the API clears it on success.
    pages_phase: Optional[str] = None
    # Ingestion-quality warnings captured during conversion (e.g.
    # silent LLM fallbacks).  ``None`` or empty list = clean.
    warnings: Optional[list] = None
    created_at: str
    updated_at: str


class ManualListResponse(BaseModel):
    """Paginated manual list."""

    items: list[ManualSummary]
    total: int


class ManualDetail(ManualSummary):
    """Full manual metadata with optional markdown body."""

    content: Optional[str] = None
    converter: Optional[str] = None
    error_message: Optional[str] = None
    md_file_path: Optional[str] = None


class ManualUploadResponse(BaseModel):
    """Response after a successful upload."""

    manual_id: str
    status: str
    filename: str


class ManualStatusResponse(BaseModel):
    """Conversion status snapshot."""

    status: str
    error_message: Optional[str] = None
    page_count: Optional[int] = None
    chunk_count: Optional[int] = None
    pages_processed: Optional[int] = None
    pages_total: Optional[int] = None
    pages_phase: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────

_PDF_MAGIC = b"%PDF"


def _to_summary(m: Manual) -> ManualSummary:
    """Map a Manual ORM row to a ManualSummary."""
    return ManualSummary(
        id=str(m.id),
        filename=m.filename,
        vehicle_model=m.vehicle_model,
        status=m.status,
        file_size_bytes=m.file_size_bytes,
        page_count=m.page_count,
        section_count=m.section_count,
        language=m.language,
        chunk_count=m.chunk_count,
        pages_processed=m.pages_processed,
        pages_total=m.pages_total,
        pages_phase=m.pages_phase,
        warnings=m.warnings,
        created_at=m.created_at.isoformat(),
        updated_at=m.updated_at.isoformat(),
    )


# ── Endpoints ────────────────────────────────────────────────


@router.post(
    "/upload",
    response_model=ManualUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_manual(
    file: UploadFile = File(...),
    vehicle_model: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManualUploadResponse:
    """Upload a PDF and start background conversion.

    Validates file type, size, and deduplicates by SHA-256.

    Args:
        file: The uploaded PDF file.
        vehicle_model: Optional vehicle model override.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Manual ID and initial status.

    Raises:
        HTTPException: On validation or duplicate errors.
    """
    # Read the entire file (capped at max + 1 byte to
    # detect oversized uploads without reading everything).
    max_size = settings.manual_max_file_size_bytes
    data = await file.read(max_size + 1)
    if len(data) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds {max_size // (1024 * 1024)}"
                f" MB limit."
            ),
        )

    # Validate PDF magic bytes.
    if not data[:4].startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are supported.",
        )

    # Dedup by file hash.
    file_hash = compute_file_hash(data)
    existing = (
        db.query(Manual)
        .filter(Manual.file_hash == file_hash)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This PDF has already been uploaded.",
        )

    # Create Manual row.
    manual_id = uuid.uuid4()
    manual = Manual(
        id=manual_id,
        user_id=current_user.id,
        filename=file.filename or "unknown.pdf",
        file_hash=file_hash,
        vehicle_model=vehicle_model,
        status="uploading",
        file_size_bytes=len(data),
    )
    db.add(manual)
    db.commit()

    # Save PDF to disk.
    rel_path = save_uploaded_pdf(data, manual_id)
    manual.pdf_file_path = rel_path
    manual.status = "converting"
    db.commit()

    # Kick off background conversion.
    asyncio.create_task(
        run_conversion_and_ingestion(manual_id),
    )

    # Background cleanup of orphan files from past failures.
    asyncio.get_running_loop().run_in_executor(
        None, cleanup_orphan_files,
    )

    logger.info(
        "manual.upload_started",
        manual_id=str(manual_id),
        filename=file.filename,
        user=current_user.username,
    )

    return ManualUploadResponse(
        manual_id=str(manual_id),
        status="converting",
        filename=file.filename or "unknown.pdf",
    )


@router.get("", response_model=ManualListResponse)
async def list_manuals(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(
        default=None, alias="status",
    ),
    vehicle_model: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManualListResponse:
    """List all manuals with optional filters.

    Manuals are shared resources — all authenticated users
    can view all manuals.

    Args:
        limit: Max items per page.
        offset: Pagination offset.
        status_filter: Filter by status string.
        vehicle_model: Filter by vehicle model.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Paginated list of manual summaries.
    """
    query = db.query(Manual)

    if status_filter:
        query = query.filter(
            Manual.status == status_filter,
        )
    if vehicle_model:
        query = query.filter(
            Manual.vehicle_model == vehicle_model,
        )

    total = query.count()
    rows = (
        query.order_by(Manual.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return ManualListResponse(
        items=[_to_summary(r) for r in rows],
        total=total,
    )


@router.get(
    "/{manual_id}",
    response_model=ManualDetail,
)
async def get_manual(
    manual_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManualDetail:
    """Get manual details including markdown content.

    Args:
        manual_id: UUID of the manual.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Full manual metadata and content.

    Raises:
        HTTPException: If manual not found.
    """
    manual = db.query(Manual).get(manual_id)
    if not manual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manual not found.",
        )

    content = None
    if manual.md_file_path and manual.status == "ingested":
        md_abs = os.path.join(
            settings.manual_storage_path,
            manual.md_file_path,
        )
        if os.path.isfile(md_abs):
            content = Path(md_abs).read_text(
                encoding="utf-8",
            )

    summary = _to_summary(manual)
    return ManualDetail(
        **summary.model_dump(),
        content=content,
        converter=manual.converter,
        error_message=manual.error_message,
        md_file_path=manual.md_file_path,
    )


@router.delete("/{manual_id}")
async def delete_manual(
    manual_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a manual and all associated artefacts.

    Removes filesystem files (PDF, markdown, images) and
    all pgvector chunks.

    Args:
        manual_id: UUID of the manual.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Confirmation dict.

    Raises:
        HTTPException: If manual not found.
    """
    manual = db.query(Manual).get(manual_id)
    if not manual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manual not found.",
        )

    # Delete filesystem artefacts.
    delete_manual_files(manual)

    # Delete the Manual row.  RAG chunks are removed
    # automatically via the manual_id FK ON DELETE CASCADE.
    db.delete(manual)
    db.commit()

    logger.info(
        "manual.deleted",
        manual_id=str(manual_id),
        user=current_user.username,
    )
    return {"deleted": True}


@router.get(
    "/{manual_id}/status",
    response_model=ManualStatusResponse,
)
async def get_manual_status(
    manual_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManualStatusResponse:
    """Get current conversion/ingestion status.

    Args:
        manual_id: UUID of the manual.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Status snapshot.

    Raises:
        HTTPException: If manual not found.
    """
    manual = db.query(Manual).get(manual_id)
    if not manual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manual not found.",
        )

    return ManualStatusResponse(
        status=manual.status,
        error_message=manual.error_message,
        page_count=manual.page_count,
        chunk_count=manual.chunk_count,
        pages_processed=manual.pages_processed,
        pages_total=manual.pages_total,
        pages_phase=manual.pages_phase,
    )


_REINGEST_BLOCKED_STATUSES = frozenset(
    {"uploading", "converting", "chunking", "embedding"},
)


@router.post(
    "/{manual_id}/reingest",
    response_model=ManualStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reingest_manual(
    manual_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManualStatusResponse:
    """Re-chunk and re-embed an existing manual's markdown.

    Reuses the previously-converted ``.md`` artefact — does NOT
    re-run marker-pdf.  If the manual has no markdown (Stage 1
    failed or never ran), the user must re-upload the PDF
    instead.  The reingest is atomic: old chunks are deleted
    and new chunks inserted in one transaction.

    Pre-conditions:

    * ``manual.md_file_path`` is not NULL — otherwise 409.
    * ``manual.status`` is in ``{'ingested', 'failed'}`` —
      otherwise 409 (ingestion already in progress).

    Args:
        manual_id: UUID of the manual to reingest.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Status snapshot showing the new ``chunking`` state.

    Raises:
        HTTPException: 404 if not found, 409 if pre-conditions
        are not met.
    """
    manual = db.query(Manual).get(manual_id)
    if not manual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manual not found.",
        )

    if not manual.md_file_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Manual has no converted markdown.  "
                "Re-upload the PDF to retry conversion."
            ),
        )

    if manual.status in _REINGEST_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Manual is in '{manual.status}' state.  "
                f"Reingest can only run when status is "
                f"'ingested' or 'failed'."
            ),
        )

    # Flip to 'chunking' synchronously so the next /status
    # poll sees the new state immediately.
    manual.status = "chunking"
    manual.error_message = None
    db.commit()

    asyncio.create_task(run_reingestion(manual_id))

    logger.info(
        "manual.reingest_requested",
        manual_id=str(manual_id),
        user=current_user.username,
    )

    return ManualStatusResponse(
        status=manual.status,
        error_message=manual.error_message,
        page_count=manual.page_count,
        chunk_count=manual.chunk_count,
        pages_processed=manual.pages_processed,
        pages_total=manual.pages_total,
        pages_phase=manual.pages_phase,
    )

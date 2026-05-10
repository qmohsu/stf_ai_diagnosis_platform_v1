"""Golden-set review dashboard endpoints (HARNESS-17).

Provides the API surface for the workshop-expert review
dashboard at GitHub Issue #82.  Phase 1 scope:

- ``GET /v2/goldens``                — list entries (filterable)
- ``GET /v2/goldens/{id}``           — full entry + caller's
                                       review (if any)
- ``POST /v2/goldens/{id}/review``   — submit / update caller's
                                       review (upsert on
                                       (entry_id, reviewer_id))
- ``POST /v2/goldens/audio/upload``  — stage audio, return token
- ``GET /v2/goldens/{id}/review/audio`` — stream audio for the
                                          caller's review

Auth: every endpoint requires a valid JWT (any authenticated
user can view + grade).  Phase 2 will add admin-gated edit and
stats endpoints.

Author: Li-Ta Hsu
Date: May 2026
"""

from __future__ import annotations

import glob
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import get_current_user
from app.config import settings
from app.models_db import GoldenEntry, GoldenReview, User

logger = structlog.get_logger(__name__)

router = APIRouter()


# ── Audio MIME-type allow-list (mirrors obd_analysis.py) ─────


_MIME_TO_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}

# Magic-byte signatures used for defence-in-depth validation.
# We accept the file ONLY if its first ~16 bytes match one of
# the known audio container headers.
_AUDIO_MAGIC_BYTES = (
    b"\x1aE\xdf\xa3",   # WebM / Matroska EBML header
    b"OggS",            # Ogg
    b"RIFF",            # WAV (followed by "WAVE" 4 bytes later)
    b"fLaC",            # FLAC (rare but harmless)
)


def _has_valid_audio_signature(data: bytes) -> bool:
    """Return True if the head bytes match a known audio header."""
    if len(data) < 4:
        return False
    if data[:4] in (b"RIFF", b"OggS", b"fLaC"):
        return True
    if data[:4] == b"\x1aE\xdf\xa3":
        return True
    # MP4 ftyp atom: bytes 4-8 are "ftyp", first 4 bytes are
    # the box size.
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return True
    return False


# ── Pydantic response schemas ────────────────────────────────


class GoldenCitationOut(BaseModel):
    """One golden citation as exposed via the API."""

    manual_id: str
    slug: str
    quote: str


class GoldenEntrySummary(BaseModel):
    """Compact entry for list responses + listing UI."""

    id: str
    manual_id: str
    category: str
    question_type: str
    difficulty: str
    requires_image: bool
    question_en: str
    question_zh: Optional[str] = None
    has_zh: bool
    # Caller's review state at time of list (so the UI can show
    # progress badges) — None if the caller hasn't reviewed yet.
    my_review_status: Optional[str] = None
    my_review_star: Optional[int] = None


class GoldenReviewOut(BaseModel):
    """One reviewer's full grade payload for an entry."""

    id: str
    golden_entry_id: str
    reviewer_id: str
    star_rating: Optional[int] = None
    question_realism_score: Optional[int] = None
    answer_correctness_score: Optional[int] = None
    citation_faithfulness_score: Optional[int] = None
    status: str
    notes: Optional[str] = None
    has_audio: bool
    audio_duration_seconds: Optional[int] = None
    created_at: str
    updated_at: str


class GoldenEntryDetail(BaseModel):
    """Full entry payload returned by the detail endpoint.

    Includes everything the dashboard needs to render the
    question card + the caller's existing review (if any).
    Eval-only fields (`must_contain`, `pitfall_directives`,
    `expected_recall_slugs`) are EXCLUDED — the dashboard
    grades human-readable substance, not eval scaffolding.
    """

    id: str
    manual_id: str
    category: str
    question_type: str
    difficulty: str
    requires_image: bool
    question_en: str
    question_zh: Optional[str] = None
    obd_context: Optional[str] = None
    golden_summary_en: str
    golden_summary_zh: Optional[str] = None
    golden_citations: List[GoldenCitationOut]
    notes: Optional[str] = None
    my_review: Optional[GoldenReviewOut] = None


class GoldenListResponse(BaseModel):
    """Paginated list of golden entries."""

    items: List[GoldenEntrySummary]
    total: int


class GoldenReviewSubmitRequest(BaseModel):
    """Payload for submitting / updating a review.

    All fields except ``status`` are optional — a "draft"
    review can be saved with just notes, and stars filled
    in later.  When ``audio_token`` is present the staged
    file is moved into permanent storage and linked.
    """

    star_rating: Optional[int] = Field(
        default=None, ge=1, le=5,
    )
    question_realism_score: Optional[int] = Field(
        default=None, ge=1, le=5,
    )
    answer_correctness_score: Optional[int] = Field(
        default=None, ge=1, le=5,
    )
    citation_faithfulness_score: Optional[int] = Field(
        default=None, ge=1, le=5,
    )
    status: str = Field(default="draft")
    notes: Optional[str] = None
    audio_token: Optional[str] = None
    audio_duration_seconds: Optional[int] = None


# ── Mappers ──────────────────────────────────────────────────


_VALID_STATUSES = {
    "draft", "accept", "needs_revision", "reject",
}


def _to_summary(
    e: GoldenEntry,
    my_review: Optional[GoldenReview] = None,
) -> GoldenEntrySummary:
    """Map a GoldenEntry ORM row to a GoldenEntrySummary."""
    return GoldenEntrySummary(
        id=e.id,
        manual_id=e.manual_id,
        category=e.category,
        question_type=e.question_type,
        difficulty=e.difficulty,
        requires_image=bool(e.requires_image),
        question_en=e.question_en,
        question_zh=e.question_zh,
        has_zh=bool(e.question_zh and e.golden_summary_zh),
        my_review_status=(
            my_review.status if my_review else None
        ),
        my_review_star=(
            my_review.star_rating if my_review else None
        ),
    )


def _to_review_out(r: GoldenReview) -> GoldenReviewOut:
    """Map a GoldenReview ORM row to a GoldenReviewOut."""
    return GoldenReviewOut(
        id=str(r.id),
        golden_entry_id=r.golden_entry_id,
        reviewer_id=str(r.reviewer_id),
        star_rating=r.star_rating,
        question_realism_score=r.question_realism_score,
        answer_correctness_score=r.answer_correctness_score,
        citation_faithfulness_score=(
            r.citation_faithfulness_score
        ),
        status=r.status,
        notes=r.notes,
        has_audio=bool(r.audio_file_path),
        audio_duration_seconds=r.audio_duration_seconds,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )


def _coerce_citations(raw: Any) -> List[GoldenCitationOut]:
    """Coerce stored JSONB citations to the response shape."""
    out: List[GoldenCitationOut] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            GoldenCitationOut(
                manual_id=str(item.get("manual_id", "")),
                slug=str(item.get("slug", "")),
                quote=str(item.get("quote", "")),
            )
        )
    return out


# ── Endpoints ────────────────────────────────────────────────


@router.get(
    "",
    response_model=GoldenListResponse,
    summary="List golden Q&A entries",
)
async def list_goldens(
    bucket: Optional[str] = Query(
        default=None,
        description=(
            "Filter by question_type bucket: lookup, "
            "procedural, cross-section, image-required, "
            "adversarial."
        ),
    ),
    difficulty: Optional[str] = Query(default=None),
    has_my_review: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GoldenListResponse:
    """List golden entries with optional bucket / difficulty filters.

    The response includes the caller's review status per entry
    (if any) so the UI can render review-progress badges.
    Goldens are shared resources — every authenticated user
    can see every entry.

    Args:
        bucket: Optional ``question_type`` filter.
        difficulty: Optional ``difficulty`` filter.
        has_my_review: If True, only entries the caller has
            already reviewed (any status).  If False, only
            unreviewed entries.  None = no filter.
        limit: Max items per page.
        offset: Pagination offset.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Paginated list of entry summaries with caller's review
        state attached.
    """
    query = db.query(GoldenEntry)
    if bucket:
        query = query.filter(
            GoldenEntry.question_type == bucket,
        )
    if difficulty:
        query = query.filter(
            GoldenEntry.difficulty == difficulty,
        )

    # Pull caller's reviews in one batch query for the page.
    # (Outer-joining inside the main query would also work; a
    # separate dict lookup is simpler and the entry count is
    # small.)
    total = query.count()
    rows = (
        query.order_by(
            GoldenEntry.question_type.asc(),
            GoldenEntry.id.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    if not rows:
        return GoldenListResponse(items=[], total=total)

    entry_ids = [r.id for r in rows]
    my_reviews = (
        db.query(GoldenReview)
        .filter(
            GoldenReview.reviewer_id == current_user.id,
            GoldenReview.golden_entry_id.in_(entry_ids),
        )
        .all()
    )
    review_by_entry: Dict[str, GoldenReview] = {
        r.golden_entry_id: r for r in my_reviews
    }

    # Apply has_my_review filter post-fetch (cheap; small N).
    items: List[GoldenEntrySummary] = []
    for e in rows:
        my = review_by_entry.get(e.id)
        if has_my_review is True and my is None:
            continue
        if has_my_review is False and my is not None:
            continue
        items.append(_to_summary(e, my))

    # If the filter dropped some, re-derive total.  For phase 1
    # we keep this simple and report the post-filter count.
    if has_my_review is not None:
        total = len(items)

    return GoldenListResponse(items=items, total=total)


@router.get(
    "/{entry_id}",
    response_model=GoldenEntryDetail,
    summary="Get one golden entry + caller's review",
)
async def get_golden(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GoldenEntryDetail:
    """Return full entry payload + caller's review (if any).

    Args:
        entry_id: Stable entry identifier (matches JSONL ``id``).
        current_user: Authenticated user.
        db: Database session.

    Returns:
        Full ``GoldenEntryDetail`` including caller's review.

    Raises:
        HTTPException: 404 if entry not found.
    """
    entry = (
        db.query(GoldenEntry)
        .filter(GoldenEntry.id == entry_id)
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Golden entry not found.",
        )

    my_review = (
        db.query(GoldenReview)
        .filter(
            GoldenReview.golden_entry_id == entry_id,
            GoldenReview.reviewer_id == current_user.id,
        )
        .first()
    )

    return GoldenEntryDetail(
        id=entry.id,
        manual_id=entry.manual_id,
        category=entry.category,
        question_type=entry.question_type,
        difficulty=entry.difficulty,
        requires_image=bool(entry.requires_image),
        question_en=entry.question_en,
        question_zh=entry.question_zh,
        obd_context=entry.obd_context,
        golden_summary_en=entry.golden_summary_en,
        golden_summary_zh=entry.golden_summary_zh,
        golden_citations=_coerce_citations(
            entry.golden_citations,
        ),
        notes=None,  # author-internal notes not exposed
        my_review=(
            _to_review_out(my_review) if my_review else None
        ),
    )


@router.post(
    "/audio/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Stage audio for a golden review",
)
async def upload_review_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Accept an audio recording and return a staging token.

    Mirrors the OBD feedback flow (``/v2/obd/audio/upload``).
    Caller submits the returned ``audio_token`` in the next
    review-submit payload to attach the audio.  Stale staging
    files are cleaned periodically by the existing audio
    storage hygiene job.

    Args:
        file: Audio file (WebM, OGG, MP4, WAV).
        current_user: Authenticated user.

    Returns:
        ``{"audio_token": str, "size_bytes": int}``.

    Raises:
        HTTPException: 415 if MIME type / magic bytes invalid.
        HTTPException: 413 if file exceeds size limit.
    """
    content_type = (file.content_type or "").split(";")[0]
    if content_type not in _MIME_TO_EXT:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported audio type '{content_type}'. "
                f"Allowed: "
                f"{', '.join(_MIME_TO_EXT.keys())}"
            ),
        )

    max_bytes = settings.audio_max_file_size_bytes
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65_536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                ),
                detail=(
                    f"Audio file too large. "
                    f"Max: {max_bytes} bytes."
                ),
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    if not _has_valid_audio_signature(data):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "File content does not match a "
                "recognised audio format."
            ),
        )

    ext = _MIME_TO_EXT[content_type]
    audio_token = str(uuid.uuid4())
    user_prefix = str(current_user.id)
    staging_dir = os.path.join(
        settings.audio_storage_path, "staging",
    )
    os.makedirs(staging_dir, exist_ok=True)
    staging_path = os.path.join(
        staging_dir, f"{user_prefix}_{audio_token}.{ext}",
    )
    with open(staging_path, "wb") as f:
        f.write(data)

    logger.info(
        "golden_audio_uploaded",
        audio_token=audio_token,
        size_bytes=len(data),
        content_type=content_type,
        user_id=str(current_user.id),
    )

    return {
        "audio_token": audio_token,
        "size_bytes": len(data),
    }


def _link_audio_to_review(
    audio_token: str,
    audio_duration_seconds: Optional[int],
    review: GoldenReview,
    db: Session,
) -> None:
    """Move staged audio to permanent storage, update review.

    Mirrors ``obd_analysis._link_audio_to_feedback``: glob for
    a staged file matching the token, validate the resolved
    path stays inside the staging directory (defence-in-depth
    against path traversal), then move into a per-review
    permanent location.

    Args:
        audio_token: UUID token from ``upload_review_audio``.
        audio_duration_seconds: Duration reported by client.
        review: GoldenReview ORM instance.
        db: Database session (caller commits).

    Raises:
        HTTPException: 400 if token references no staged file.
    """
    staging_dir = os.path.join(
        settings.audio_storage_path, "staging",
    )
    matches = glob.glob(
        os.path.join(staging_dir, f"*_{audio_token}.*"),
    )
    if not matches:
        raise HTTPException(
            status_code=400,
            detail="Invalid audio_token — no staged file.",
        )
    staging_path = matches[0]

    resolved = os.path.realpath(staging_path)
    real_staging = os.path.realpath(staging_dir)
    if not resolved.startswith(real_staging + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Invalid audio_token.",
        )

    ext = os.path.splitext(staging_path)[1]

    # Permanent location: per-entry directory keeps audio
    # files organised the same way as OBD feedback (one dir
    # per "session" — here the entry plays that role).
    entry_dir = os.path.join(
        settings.audio_storage_path,
        "goldens",
        review.golden_entry_id,
    )
    os.makedirs(entry_dir, exist_ok=True)
    relative_path = os.path.join(
        "goldens",
        review.golden_entry_id,
        f"{review.id}{ext}",
    )
    dest_path = os.path.join(
        settings.audio_storage_path, relative_path,
    )
    shutil.move(staging_path, dest_path)

    review.audio_file_path = relative_path
    review.audio_duration_seconds = audio_duration_seconds
    review.audio_size_bytes = os.path.getsize(dest_path)

    logger.info(
        "golden_audio_linked",
        review_id=str(review.id),
        audio_path=relative_path,
        size_bytes=review.audio_size_bytes,
    )


@router.post(
    "/{entry_id}/review",
    response_model=GoldenReviewOut,
    status_code=status.HTTP_200_OK,
    summary="Submit / update caller's review of a golden entry",
)
async def submit_review(
    entry_id: str,
    payload: GoldenReviewSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GoldenReviewOut:
    """Upsert the caller's review for one golden entry.

    Each ``(golden_entry, reviewer)`` pair has at most one
    review row.  Re-submitting updates fields in place and
    bumps ``updated_at``.

    Audio handling: if ``audio_token`` is present, the staged
    file is moved into permanent storage and the row updated.
    A subsequent submit without ``audio_token`` keeps the
    existing audio (no replace-with-null behaviour) — explicit
    clearing requires a separate endpoint we'll add later if
    needed.

    Args:
        entry_id: Golden entry ID (matches JSONL ``id``).
        payload: Review fields.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        The updated ``GoldenReviewOut``.

    Raises:
        HTTPException: 404 if entry not found, 400 if status
            invalid, 400 if audio_token references no staged
            file.
    """
    if payload.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid status '{payload.status}'.  "
                f"Allowed: {', '.join(sorted(_VALID_STATUSES))}"
            ),
        )

    entry = (
        db.query(GoldenEntry)
        .filter(GoldenEntry.id == entry_id)
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Golden entry not found.",
        )

    review = (
        db.query(GoldenReview)
        .filter(
            GoldenReview.golden_entry_id == entry_id,
            GoldenReview.reviewer_id == current_user.id,
        )
        .first()
    )
    if review is None:
        review = GoldenReview(
            id=uuid.uuid4(),
            golden_entry_id=entry_id,
            reviewer_id=current_user.id,
        )
        db.add(review)

    review.star_rating = payload.star_rating
    review.question_realism_score = (
        payload.question_realism_score
    )
    review.answer_correctness_score = (
        payload.answer_correctness_score
    )
    review.citation_faithfulness_score = (
        payload.citation_faithfulness_score
    )
    review.status = payload.status
    review.notes = payload.notes

    # Flush so review.id is populated for path-building.
    db.flush()

    if payload.audio_token:
        _link_audio_to_review(
            payload.audio_token,
            payload.audio_duration_seconds,
            review,
            db,
        )

    db.commit()
    db.refresh(review)

    logger.info(
        "golden_review_upserted",
        entry_id=entry_id,
        reviewer_id=str(current_user.id),
        status=review.status,
        star=review.star_rating,
        has_audio=bool(review.audio_file_path),
    )

    return _to_review_out(review)


@router.get(
    "/{entry_id}/review/audio",
    summary="Stream caller's audio attachment for an entry",
)
async def get_review_audio(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream the audio file attached to the caller's review.

    Args:
        entry_id: Golden entry ID.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        ``FileResponse`` streaming the audio file.

    Raises:
        HTTPException: 404 if review or audio not found.
    """
    review = (
        db.query(GoldenReview)
        .filter(
            GoldenReview.golden_entry_id == entry_id,
            GoldenReview.reviewer_id == current_user.id,
        )
        .first()
    )
    if not review or not review.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="No audio attached to your review.",
        )

    abs_path = os.path.join(
        settings.audio_storage_path, review.audio_file_path,
    )
    # Defence-in-depth: ensure resolved path stays inside
    # the audio storage root.
    real_root = os.path.realpath(settings.audio_storage_path)
    real_path = os.path.realpath(abs_path)
    if not real_path.startswith(real_root + os.sep):
        raise HTTPException(
            status_code=404, detail="Audio not found.",
        )
    if not os.path.isfile(real_path):
        raise HTTPException(
            status_code=404, detail="Audio not found.",
        )

    return FileResponse(real_path)

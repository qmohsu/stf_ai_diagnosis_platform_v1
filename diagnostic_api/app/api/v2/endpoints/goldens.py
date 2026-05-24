"""Golden-set review dashboard endpoints (HARNESS-17).

Provides the API surface for the workshop-expert review
dashboard at GitHub Issue #82.  Endpoints:

- ``GET /v2/goldens``                — list entries (filterable);
                                       headline status is the
                                       latest review across ALL
                                       reviewers
- ``GET /v2/goldens/{id}``           — full entry payload (no
                                       caller-specific review;
                                       see ``/reviews`` for the
                                       team history)
- ``POST /v2/goldens/{id}/review``   — APPEND a new review row
                                       (no upsert).  Submitting
                                       twice creates two rows.
- ``GET /v2/goldens/{id}/reviews``   — list ALL team reviews
                                       on one entry (with the
                                       Q+A snapshot frozen at
                                       submit time)
- ``DELETE /v2/goldens/reviews/{id}`` — owner-only hard delete
- ``POST /v2/goldens/audio/upload``  — stage audio, return token
- ``GET /v2/goldens/reviews/{id}/audio`` — stream any review's
                                          audio attachment

Auth: every endpoint requires a valid JWT (any authenticated
user can view + grade).  Reviews are append-only; the entire
team collaborates on the same entry, and the most-recent grade
is the one surfaced in the listing dashboard.

Author: Li-Ta Hsu
Date: May 2026
"""

from __future__ import annotations

import glob
import json as _json
import os
import shutil
import uuid
from pathlib import Path
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
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import get_current_user
from app.config import settings
from app.models_db import GoldenEntry, GoldenReview, Manual, User

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
    """One golden citation as exposed via the API.

    ``figure_image_paths`` is a list of manual-relative image
    paths (as they appear in the manual's markdown source, e.g.
    ``images/{manual_id}/_page_X_Picture_Y.jpeg``) that visually
    support the cited quote.  Image-required citations embed
    these figures directly in the dashboard's QuestionCard so
    reviewers don't have to follow a hyperlink to see the
    answer.  Empty list = no images attached to this citation.
    """

    manual_id: str
    slug: str
    quote: str
    figure_image_paths: List[str] = []


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
    # HARNESS-21 [2b/4]: lane discriminator ('manual' | 'obd').
    # Drives which dashboard route surfaces this entry
    # (`/goldens/manual` vs `/goldens/obd`) and which bucket set
    # the listing's filter dropdown shows.  Default 'manual' for
    # back-compat with pre-[2b/4] callers (they'll never see
    # OBD entries because the filter defaults the same way).
    lane: str = "manual"
    # HARNESS-20 (post-bugfix): true when this entry has been
    # promoted via ``scripts/promote_golden.py`` and now also
    # lives in ``golden/v2/locked/mws150a.jsonl``.  The row's
    # content (text, citations, etc.) always reflects the
    # mutable candidate, so the dashboard can keep editing
    # and re-grading without the locked copy interfering;
    # ``is_locked`` is just the badge the UI uses to mark
    # "this entry counts against the eval harness now, edits
    # require a new id".  Replaces the earlier ``tier`` field
    # which was wrong because both tiers share entry ids.
    is_locked: bool = False
    # Team's latest review state (most-recent submit across ALL
    # reviewers).  None if nobody has reviewed yet.  This is the
    # "headline" status shown on the listing dashboard — every
    # team member sees the same value, regardless of who is
    # logged in.  Reviewer username is included so the listing
    # can attribute the headline grade.
    latest_review_status: Optional[str] = None
    latest_review_star: Optional[int] = None
    latest_reviewer_username: Optional[str] = None
    latest_review_at: Optional[str] = None
    review_count: int = 0


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


class TeamReviewItem(BaseModel):
    """One review in the team-feedback list, with reviewer +
    snapshot fields the cross-user history view needs."""

    review_id: str
    reviewer_id: str
    reviewer_username: str
    star_rating: Optional[int] = None
    question_realism_score: Optional[int] = None
    answer_correctness_score: Optional[int] = None
    citation_faithfulness_score: Optional[int] = None
    status: str
    notes: Optional[str] = None
    has_audio: bool
    audio_duration_seconds: Optional[int] = None
    # Snapshot of the entry's Q+A at the time this review was
    # submitted.  Null for pre-Phase-2 reviews — UI should fall
    # back to the live entry's text in that case.
    snapshot_question_en: Optional[str] = None
    snapshot_question_zh: Optional[str] = None
    snapshot_summary_en: Optional[str] = None
    snapshot_summary_zh: Optional[str] = None
    snapshot_citations: Optional[List[GoldenCitationOut]] = None
    created_at: str
    updated_at: str


class TeamReviewListResponse(BaseModel):
    """Aggregated team-feedback payload for one golden entry."""

    items: List[TeamReviewItem]
    total: int


class GoldenEntryDetail(BaseModel):
    """Full entry payload returned by the detail endpoint.

    Returns everything needed to render the question card.  Per-
    reviewer history lives in ``GET /v2/goldens/{id}/reviews``;
    the submit form on the dashboard always starts blank because
    reviews are append-only.  Eval-only fields (`must_contain`,
    `pitfall_directives`, `expected_recall_slugs`) are EXCLUDED
    — the dashboard grades human-readable substance, not eval
    scaffolding.
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
    # HARNESS-21 [2b/4]: lane discriminator + OBD-specific fields.
    # ``lane`` is always populated; OBD fields are empty/false for
    # manual entries (the dashboard branches on ``lane`` to decide
    # which detail components to render).
    lane: str = "manual"
    expected_signal_citations: List[Dict[str, Any]] = Field(
        default_factory=list,
    )
    expected_dtcs: List[Dict[str, Any]] = Field(default_factory=list)
    expected_no_evidence: bool = False
    # OBD-side pitfall directives — shown to the reviewer alongside
    # the question.  Manual entries already render
    # pitfall_directives elsewhere; OBD entries need the same on
    # the OBD detail page.  Both lanes populate from
    # ``GoldenEntry.pitfall_directives``.
    pitfall_directives: List[str] = Field(default_factory=list)
    # Relative path to the manual's markdown file (mirrors the
    # ``Manual.md_file_path`` column).  None when the manual_id
    # is the adversarial sentinel ``(none)`` or the manual was
    # deleted.  The frontend uses this to compute the same
    # ``imageBaseUrl`` the ManualViewer uses, so embedded figure
    # images on the question card resolve to the right URL.
    md_file_path: Optional[str] = None
    # HARNESS-20: see ``GoldenEntrySummary.is_locked`` for the
    # semantics.  Mirrored here so a single-entry fetch carries
    # the same lock signal as the listing.
    is_locked: bool = False


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
    latest_review: Optional[GoldenReview] = None,
    latest_reviewer_username: Optional[str] = None,
    review_count: int = 0,
) -> GoldenEntrySummary:
    """Map a GoldenEntry ORM row to a GoldenEntrySummary.

    ``latest_review`` is the most-recent review across ALL
    reviewers; the listing dashboard shows the same headline
    grade to every viewer, regardless of who is logged in.
    """
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
        latest_review_status=(
            latest_review.status if latest_review else None
        ),
        latest_review_star=(
            latest_review.star_rating if latest_review else None
        ),
        latest_reviewer_username=latest_reviewer_username,
        latest_review_at=(
            latest_review.updated_at.isoformat()
            if latest_review else None
        ),
        review_count=review_count,
        # HARNESS-20: lock-state flag.  ``getattr`` keeps the
        # mapper resilient if a test passes a bare object that
        # predates the column.
        is_locked=bool(getattr(e, "is_locked", False)),
        # HARNESS-21 [2b/4]: lane discriminator.  ``getattr`` for
        # the same resilience reason.
        lane=str(getattr(e, "lane", "manual") or "manual"),
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
        figs = item.get("figure_image_paths") or []
        if not isinstance(figs, list):
            figs = []
        out.append(
            GoldenCitationOut(
                manual_id=str(item.get("manual_id", "")),
                slug=str(item.get("slug", "")),
                quote=str(item.get("quote", "")),
                figure_image_paths=[
                    str(p) for p in figs if isinstance(p, str)
                ],
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
    lane: str = Query(
        default="manual",
        description=(
            "Lane discriminator: 'manual' (default — manual-eval "
            "goldens, 5 buckets: lookup, procedural, "
            "cross-section, image-required, adversarial) or 'obd' "
            "(OBD-eval goldens, 6 buckets: signal_statistics, "
            "event_finding, dtc_enumeration, dtc_decode, "
            "compound_obd, adversarial_obd).  HARNESS-21 [2b/4]: "
            "default 'manual' preserves back-compat with pre-[2b/4] "
            "callers that don't pass the param."
        ),
        pattern="^(manual|obd)$",
    ),
    bucket: Optional[str] = Query(
        default=None,
        description=(
            "Filter by question_type bucket.  Manual lane buckets: "
            "lookup, procedural, cross-section, image-required, "
            "adversarial.  OBD lane buckets: signal_statistics, "
            "event_finding, dtc_enumeration, dtc_decode, "
            "compound_obd, adversarial_obd."
        ),
    ),
    difficulty: Optional[str] = Query(default=None),
    has_reviews: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    db: Session = Depends(get_db),
) -> GoldenListResponse:
    """List golden entries with team-wide latest-review status.

    Every authenticated user sees the same dashboard: each entry
    carries the most-recent review across ALL reviewers as its
    headline grade, plus the reviewer's username and submit time.
    Per-reviewer history (with all individual grades) lives on
    the detail page's ``/reviews`` endpoint.

    Args:
        lane: Required lane filter — 'manual' (default) or 'obd'.
            HARNESS-21 [2b/4] split the dashboard into per-lane
            routes; the API correspondingly returns only the
            chosen lane's entries.  No "both lanes" mode — the
            UI never wants that, and it would muddy the bucket
            filter.
        bucket: Optional ``question_type`` filter (lane-specific).
        difficulty: Optional ``difficulty`` filter.
        has_reviews: If True, only entries that have at least
            one review (any reviewer).  If False, only entries
            no-one has reviewed yet.  None = no filter.
        limit: Max items per page.
        offset: Pagination offset.
        current_user: Authenticated user.  Identity is not used
            in the response — every team member sees the same
            headline status.
        db: Database session.

    Returns:
        Paginated list of entry summaries with the team's latest
        review attached.
    """
    query = db.query(GoldenEntry).filter(GoldenEntry.lane == lane)
    if bucket:
        query = query.filter(
            GoldenEntry.question_type == bucket,
        )
    if difficulty:
        query = query.filter(
            GoldenEntry.difficulty == difficulty,
        )

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

    # Pull every review for the page in one query, ordered most-
    # recent first.  N is small (≤500) so picking the first per
    # entry_id in Python is fine.  Joining users gives us the
    # reviewer username without a second round trip.
    review_rows = (
        db.query(GoldenReview, User.username)
        .join(User, GoldenReview.reviewer_id == User.id)
        .filter(GoldenReview.golden_entry_id.in_(entry_ids))
        .order_by(GoldenReview.updated_at.desc())
        .all()
    )

    latest_by_entry: Dict[str, tuple] = {}
    count_by_entry: Dict[str, int] = {}
    for review, username in review_rows:
        count_by_entry[review.golden_entry_id] = (
            count_by_entry.get(review.golden_entry_id, 0) + 1
        )
        # `review_rows` is ordered DESC; first hit wins.
        latest_by_entry.setdefault(
            review.golden_entry_id, (review, username),
        )

    items: List[GoldenEntrySummary] = []
    for e in rows:
        latest = latest_by_entry.get(e.id)
        has_any = latest is not None
        if has_reviews is True and not has_any:
            continue
        if has_reviews is False and has_any:
            continue
        latest_review, latest_username = (
            (latest[0], latest[1]) if latest else (None, None)
        )
        items.append(
            _to_summary(
                e,
                latest_review=latest_review,
                latest_reviewer_username=latest_username,
                review_count=count_by_entry.get(e.id, 0),
            ),
        )

    if has_reviews is not None:
        total = len(items)

    return GoldenListResponse(items=items, total=total)


@router.get(
    "/{entry_id}",
    response_model=GoldenEntryDetail,
    summary="Get one golden entry",
)
async def get_golden(
    entry_id: str,
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    db: Session = Depends(get_db),
) -> GoldenEntryDetail:
    """Return the full entry payload.

    The team feedback history (with every reviewer's grades)
    lives at ``GET /v2/goldens/{id}/reviews``; the submit form
    on the dashboard always starts blank, so the detail
    endpoint no longer needs to return a per-caller review.

    Args:
        entry_id: Stable entry identifier (matches JSONL ``id``).
        current_user: Authenticated user (required, identity
            not used in the response).
        db: Database session.

    Returns:
        Full ``GoldenEntryDetail`` payload.

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

    # Resolve manual.md_file_path so the frontend can compute
    # the imageBaseUrl for inline figure rendering.  Best-
    # effort: tolerate non-UUID manual_ids (e.g. the
    # adversarial-entry "(none)" sentinel) by returning None.
    md_file_path: Optional[str] = None
    try:
        manual_uuid = uuid.UUID(entry.manual_id)
    except (ValueError, AttributeError):
        manual_uuid = None
    if manual_uuid is not None:
        manual = (
            db.query(Manual.md_file_path)
            .filter(Manual.id == manual_uuid)
            .first()
        )
        if manual is not None:
            md_file_path = manual[0]

    # HARNESS-21 [2b/4]: pass-through of the OBD-side JSONB
    # columns.  Manual entries get the empty defaults.  The
    # ``getattr`` calls keep the mapper resilient against
    # pre-[2b/4] rows that predate the columns (the migration
    # back-fills defaults, so this is a belt-and-braces).
    pitfall_raw = getattr(entry, "pitfall_directives", None) or []
    pitfall_list = (
        [str(p) for p in pitfall_raw]
        if isinstance(pitfall_raw, list) else []
    )
    sig_cites = getattr(
        entry, "expected_signal_citations", None,
    ) or []
    dtc_cites = getattr(entry, "expected_dtcs", None) or []

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
        md_file_path=md_file_path,
        # HARNESS-20: lock-state flag.
        is_locked=bool(getattr(entry, "is_locked", False)),
        # HARNESS-21 [2b/4]: lane + OBD-specific fields.
        lane=str(getattr(entry, "lane", "manual") or "manual"),
        expected_signal_citations=(
            sig_cites if isinstance(sig_cites, list) else []
        ),
        expected_dtcs=(
            dtc_cites if isinstance(dtc_cites, list) else []
        ),
        expected_no_evidence=bool(
            getattr(entry, "expected_no_evidence", False),
        ),
        pitfall_directives=pitfall_list,
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
    status_code=status.HTTP_201_CREATED,
    summary="Append a new review of a golden entry",
)
async def submit_review(
    entry_id: str,
    payload: GoldenReviewSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GoldenReviewOut:
    """Append a new review row for one golden entry.

    Reviews are append-only: submitting twice creates two rows.
    The same reviewer can post multiple grades over time; the
    listing dashboard surfaces the team-wide most-recent grade
    as the entry's headline status.  Reviewers can delete their
    own rows via ``DELETE /v2/goldens/reviews/{review_id}``.

    Audio handling: each submit may attach its own audio via
    ``audio_token``; the staged file is moved into permanent
    storage keyed by the new row's UUID, so audio attachments
    do not collide across the same reviewer's multiple rows.

    Args:
        entry_id: Golden entry ID (matches JSONL ``id``).
        payload: Review fields.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        The newly-created ``GoldenReviewOut``.

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

    review = GoldenReview(
        id=uuid.uuid4(),
        golden_entry_id=entry_id,
        reviewer_id=current_user.id,
        star_rating=payload.star_rating,
        question_realism_score=payload.question_realism_score,
        answer_correctness_score=payload.answer_correctness_score,
        citation_faithfulness_score=(
            payload.citation_faithfulness_score
        ),
        status=payload.status,
        notes=payload.notes,
        # Freeze the Q+A snapshot at submit time so the review
        # remains reproducible even after the live entry gets
        # edited (Phase 3 feature).
        snapshot_question_en=entry.question_en,
        snapshot_question_zh=entry.question_zh,
        snapshot_summary_en=entry.golden_summary_en,
        snapshot_summary_zh=entry.golden_summary_zh,
        snapshot_citations=entry.golden_citations,
    )
    db.add(review)

    # Flush so review.id is populated for audio path-building.
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
        "golden_review_appended",
        entry_id=entry_id,
        review_id=str(review.id),
        reviewer_id=str(current_user.id),
        status=review.status,
        star=review.star_rating,
        has_audio=bool(review.audio_file_path),
    )

    return _to_review_out(review)


# ── Team feedback (cross-user) ───────────────────────────────


@router.get(
    "/{entry_id}/reviews",
    response_model=TeamReviewListResponse,
    summary="List ALL team reviews for one golden entry",
)
async def list_team_reviews(
    entry_id: str,
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    db: Session = Depends(get_db),
) -> TeamReviewListResponse:
    """Return every reviewer's grade for a golden entry.

    Any authenticated user can read every team member's review
    on every entry — full transparency for the workshop-expert
    workflow (HARNESS-17 Phase 2 design decision: option A,
    confirmed by user).  Each entry carries its reviewer's
    username plus the Q+A snapshot frozen at submit time so
    the feedback is self-contained.

    Args:
        entry_id: Golden entry ID.
        current_user: Authenticated user (required, identity
            not used — we expose all reviews to any logged-in
            user).
        db: Database session.

    Returns:
        List of ``TeamReviewItem`` with reviewer + snapshot
        data + grade fields, ordered by most-recent-first.
    """
    # Confirm entry exists so we 404 cleanly (vs returning an
    # empty list for a nonexistent ID).
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

    # Join reviews against users so we can include reviewer
    # username in one round-trip.  Most-recent submit first.
    rows = (
        db.query(GoldenReview, User.username)
        .join(User, GoldenReview.reviewer_id == User.id)
        .filter(GoldenReview.golden_entry_id == entry_id)
        .order_by(GoldenReview.updated_at.desc())
        .all()
    )

    items: List[TeamReviewItem] = []
    for review, username in rows:
        items.append(
            TeamReviewItem(
                review_id=str(review.id),
                reviewer_id=str(review.reviewer_id),
                reviewer_username=username,
                star_rating=review.star_rating,
                question_realism_score=(
                    review.question_realism_score
                ),
                answer_correctness_score=(
                    review.answer_correctness_score
                ),
                citation_faithfulness_score=(
                    review.citation_faithfulness_score
                ),
                status=review.status,
                notes=review.notes,
                has_audio=bool(review.audio_file_path),
                audio_duration_seconds=(
                    review.audio_duration_seconds
                ),
                snapshot_question_en=review.snapshot_question_en,
                snapshot_question_zh=review.snapshot_question_zh,
                snapshot_summary_en=review.snapshot_summary_en,
                snapshot_summary_zh=review.snapshot_summary_zh,
                snapshot_citations=_coerce_citations(
                    review.snapshot_citations,
                ) if review.snapshot_citations else None,
                created_at=review.created_at.isoformat(),
                updated_at=review.updated_at.isoformat(),
            ),
        )

    return TeamReviewListResponse(items=items, total=len(items))


@router.get(
    "/reviews/{review_id}/audio",
    summary="Stream any review's audio attachment (cross-user)",
)
async def get_any_review_audio(
    review_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream the audio attached to ANY review by its UUID.

    Any authenticated user can play any review's audio — full-
    transparency Phase 2 setting (workshop-expert workflow,
    option A).  Defence-in-depth path validation still applies
    to prevent traversal out of the audio storage root.

    Args:
        review_id: GoldenReview UUID.
        current_user: Authenticated user (required).
        db: Database session.

    Returns:
        FileResponse streaming the audio file.

    Raises:
        HTTPException: 404 if review or audio not found.
    """
    review = (
        db.query(GoldenReview)
        .filter(GoldenReview.id == review_id)
        .first()
    )
    if not review or not review.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="No audio attached to that review.",
        )

    abs_path = os.path.join(
        settings.audio_storage_path, review.audio_file_path,
    )
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


# Intentionally NO delete endpoint for golden reviews.
#
# Reviews are append-only and immutable: the team-shared dashboard
# uses last-write-wins semantics for the headline status, and prior
# rows stay as audit history so the reasoning behind a re-grade
# remains traceable.  If a reviewer needs to revise an earlier
# grade, they post a new review (POST /v2/goldens/{id}/review) —
# the most-recent submit becomes the headline, the older rows
# remain visible in the team-feedback panel.
#
# DB-level cleanup (e.g. anonymisation, GDPR-style erasure) must be
# performed by a server administrator via direct SQL, deliberately
# leaving an out-of-band audit trail.


# ── HARNESS-21 [2b/4]: OBD reference-stats endpoint ──────────


_OBD_REFERENCE_STATS_PATH = Path(
    "/app/tests/harness/evals/golden/v1/"
    "yamaha_road_test_reference.json"
)
"""Absolute path inside the container where the precomputed
Yamaha reference stats JSON lives.

Generated offline by ``scripts/compute_yamaha_reference.py
--json …`` and committed in PR [2a/4].  The Dockerfile's
``COPY ... yamaha_road_test_reference.json`` ensures this path
resolves at runtime.

The sidecar carries per-signal min/p50/p95/max/mean/std and
event-window time-ranges; the ``/goldens/obd`` detail-page
sparkline renderer reads it client-side.
"""


_obd_reference_cache: Optional[Dict[str, Any]] = None
"""Module-level cache so repeated calls don't re-read the disk."""


def _load_obd_reference() -> Dict[str, Any]:
    """Load + cache the Yamaha reference-stats sidecar JSON.

    Cache is in-process and never invalidated — the sidecar is
    immutable post-PR-[2a/4] and pinned by SHA-256 to the
    fixture file.  Re-deploy refreshes the container, which
    repopulates the cache from disk.
    """
    global _obd_reference_cache
    if _obd_reference_cache is None:
        if not _OBD_REFERENCE_STATS_PATH.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "OBD reference stats sidecar is missing "
                    "from this image — verify the Dockerfile "
                    "copies yamaha_road_test_reference.json."
                ),
            )
        _obd_reference_cache = _json.loads(
            _OBD_REFERENCE_STATS_PATH.read_text(
                encoding="utf-8",
            ),
        )
    return _obd_reference_cache


@router.get(
    "/obd/reference-stats",
    summary="OBD reference statistics (Yamaha fixture)",
)
async def get_obd_reference_stats(
    current_user: User = Depends(get_current_user),  # noqa: ARG001
) -> JSONResponse:
    """Serve the precomputed Yamaha-fixture stats sidecar.

    The ``/goldens/obd/[id]`` detail page calls this to render
    sparklines + value previews next to each
    ``expected_signal_citation`` on a golden entry.  Auth-gated
    like every other ``/v2/goldens/...`` route.

    Returns:
        The full sidecar JSON: ``{schema_version, fixture,
        signal_stats, event_windows, metadata_dtcs}``.

    Raises:
        HTTPException: 404 if the sidecar isn't in the image
            (deploy / Dockerfile bug).
    """
    return JSONResponse(content=_load_obd_reference())

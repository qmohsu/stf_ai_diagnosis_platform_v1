"""Manual library services (PROD-06): list / toc / search / upload / delete.

The library is PUBLIC (design doc §1.8): every workshop member can read
every manual; only managers upload or delete.  Manuals copied from V2
have no uploader ("seed") and cannot be deleted through the API (FM-4).

Upload = validate PDF → store under ``uploads/`` → insert row → defer the
``knowledge.ingest_manual`` job on the ``gpu`` queue.  The queue library
uses its own connection, so the row is committed first and the defer
failure path deletes row + file again (FM-5): the caller never sees a
half-registered manual.

This module must NOT import the heavy pipeline package (FM-29); reading
uses ``manual_fs`` / ``manual_index`` only.

Author: Xiangzhu Yan
"""

import hashlib
import io
import re
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import structlog
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.errors import ApiError, forbidden, not_found
from stf_v3.knowledge import manual_fs, manual_index
from stf_v3.knowledge.models import Manual
from stf_v3.knowledge.schemas import ManualOut, SearchHit
from stf_v3.settings import settings
from stf_v3.workshops.models import Membership

log = structlog.get_logger("stf_v3.knowledge")

STAGES = ("converting", "building", "indexing", "summarizing", "gates", "installing")
_HIT_PREVIEW_CHARS = 160


# ── paths ────────────────────────────────────────────────────────────


def storage_root() -> Path:
    """Absolute manual storage root (read per call so tests can redirect)."""
    return Path(settings.manual_storage_path).resolve()


def pdf_path(manual_id: uuid.UUID) -> Path:
    """Where the source PDF lives."""
    return storage_root() / "uploads" / f"{manual_id}.pdf"


def manual_dir(manual_id: uuid.UUID) -> Path:
    """Directory holding a V3-ingested manual's artefacts."""
    return storage_root() / str(manual_id)


def has_index_track(manual_id: uuid.UUID) -> bool:
    """True when an index sidecar exists for the manual."""
    return manual_index.load_runtime_index(str(manual_id)) is not None


def to_out(row: Manual) -> ManualOut:
    """Serialises a row (adds canonical name, seed flag, index flag)."""
    canonical = f"{row.manufacturer} {row.vehicle_model}".strip()
    return ManualOut(
        id=row.id, filename=row.filename, manufacturer=row.manufacturer,
        vehicle_model=row.vehicle_model, factory_code=row.factory_code,
        canonical_name=canonical, status=row.status,
        file_size_bytes=row.file_size_bytes, page_count=row.page_count,
        section_count=row.section_count, language=row.language,
        converter=row.converter, error_message=row.error_message,
        pages_processed=row.pages_processed, pages_total=row.pages_total,
        pages_phase=row.pages_phase, warnings=row.warnings, job_id=row.job_id,
        index_track=(row.status == "ingested" and has_index_track(row.id)),
        seed=row.uploaded_by is None, uploaded_by=row.uploaded_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


# ── authorisation ────────────────────────────────────────────────────


async def require_member(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Any workshop membership grants read access to the public library."""
    stmt = select(Membership.id).where(Membership.user_id == user_id).limit(1)
    if (await session.execute(stmt)).first() is None:
        raise forbidden("membership_required", "Join a workshop first")


async def require_manager(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Upload / delete need a manager role in at least one workshop."""
    stmt = (
        select(Membership.id)
        .where(Membership.user_id == user_id, Membership.role == "manager")
        .limit(1)
    )
    if (await session.execute(stmt)).first() is None:
        raise forbidden("role_required", "Requires manager role")


# ── queries ──────────────────────────────────────────────────────────


async def list_manuals(session: AsyncSession, q: Optional[str] = None) -> List[Manual]:
    """All manuals, newest first; ``q`` matches manufacturer/model/code."""
    stmt = select(Manual).order_by(Manual.created_at.desc())
    rows = list((await session.execute(stmt)).scalars())
    if q:
        needle = q.strip().lower()
        rows = [
            r for r in rows
            if needle in f"{r.manufacturer} {r.vehicle_model} {r.factory_code or ''} {r.filename}".lower()
        ]
    return rows


async def get_manual(session: AsyncSession, manual_id: uuid.UUID) -> Manual:
    """Returns a manual row or 404."""
    row = await session.get(Manual, manual_id)
    if row is None:
        raise not_found("manual_not_found", "Manual not found")
    return row


def _legacy_text(row: Manual) -> Optional[str]:
    if not row.md_file_path:
        return None
    path = storage_root() / row.md_file_path
    if not path.is_file():
        return None
    return manual_fs._clean_md(  # noqa: SLF001 - same package family
        manual_fs.promote_unheaded_titles(path.read_text(encoding="utf-8"))
    )


def toc_text(row: Manual, max_depth: int = 3) -> Tuple[str, bool]:
    """Indented heading tree; index track when a sidecar exists.

    Mirrors the V2 ``get_manual_toc`` tool so the future agent tool and
    this endpoint agree.  Large trees auto-shrink to stay under 60k chars.
    """
    rt = manual_index.load_runtime_index(str(row.id))
    if rt is not None:
        depth = max_depth
        toc = rt.toc_text(max_depth=depth)
        while len(toc) > 60_000 and depth > 1:
            depth -= 1
            toc = rt.toc_text(max_depth=depth)
        return toc, True
    text = _legacy_text(row)
    if text is None:
        raise ApiError(409, "manual_not_readable", "Manual has no readable content yet")
    tree = manual_fs.parse_heading_tree(text)
    return _format_tree(tree, max_depth=max_depth), False


def _format_tree(nodes, indent: int = 0, max_depth: Optional[int] = None) -> str:  # type: ignore[no-untyped-def]
    lines: List[str] = []
    prefix = "  " * indent
    for node in nodes:
        lines.append(f"{prefix}- {node.title}  [{node.slug}]")
        if node.children and (max_depth is None or indent + 1 < max_depth):
            lines.append(_format_tree(node.children, indent + 1, max_depth))
    return "\n".join(lines)


def _low_value(line: str) -> bool:
    # Dotted-leader TOC rows and cross-references match every title.
    return bool(re.search(r"\.{3,}", line)) or "參閱" in line


def search_text(row: Manual, query: str, max_hits: int = 20) -> Tuple[List[SearchHit], int, bool]:
    """Literal, case-insensitive line search (the agent's absence check).

    Returns:
        (hits shown, total hit count, index_track).
    """
    needle = query.casefold()
    rt = manual_index.load_runtime_index(str(row.id))
    if rt is not None:
        raw = [(i, l.strip()) for i, l in enumerate(rt.content_lines) if needle in l.casefold()]
        ordered = [h for h in raw if not _low_value(h[1])] + [h for h in raw if _low_value(h[1])]
        hits = [
            SearchHit(section=rt.enclosing_node_id(i), line=t[:_HIT_PREVIEW_CHARS])
            for i, t in ordered[:max_hits]
        ]
        return hits, len(raw), True
    text = _legacy_text(row)
    if text is None:
        raise ApiError(409, "manual_not_readable", "Manual has no readable content yet")
    lines = text.split("\n")
    raw = [(i, l.strip()) for i, l in enumerate(lines) if needle in l.casefold()]
    flat = manual_fs._flatten_tree(manual_fs.parse_heading_tree(text))  # noqa: SLF001
    starts = [(n.line_start, n.slug) for n in flat]

    def enclosing(idx: int) -> str:
        best = "(before first section)"
        for start, slug in starts:
            if start <= idx:
                best = slug
            else:
                break
        return best

    hits = [SearchHit(section=enclosing(i), line=t[:_HIT_PREVIEW_CHARS]) for i, t in raw[:max_hits]]
    return hits, len(raw), False


# ── upload ───────────────────────────────────────────────────────────


def validate_pdf(data: bytes) -> int:
    """Cheap upload-time checks (FM-23, FM-26): returns the page count.

    Raises:
        ApiError: 413 too large, 422 not a PDF / encrypted / unreadable /
            too many pages.
    """
    if len(data) > settings.manual_max_upload_bytes:
        raise ApiError(413, "file_too_large", f"PDF exceeds {settings.manual_max_upload_bytes} bytes")
    if not data.startswith(b"%PDF"):
        raise ApiError(422, "not_a_pdf", "Only PDF files are accepted")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ApiError(422, "pdf_encrypted", "Encrypted PDFs are not accepted")
        pages = len(reader.pages)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure = bad file
        raise ApiError(422, "pdf_unreadable", f"PDF could not be parsed: {exc}") from exc
    if pages == 0:
        raise ApiError(422, "pdf_unreadable", "PDF has no pages")
    if pages > settings.manual_max_pages:
        raise ApiError(
            422, "pdf_too_many_pages",
            f"PDF has {pages} pages; limit is {settings.manual_max_pages}. Split it.",
        )
    return pages


async def upload_manual(
    session: AsyncSession,
    data: bytes,
    filename: str,
    manufacturer: str,
    vehicle_model: str,
    factory_code: Optional[str],
    uploaded_by: uuid.UUID,
) -> Manual:
    """Registers a PDF and queues its ingest job.

    Order (FM-5): validate → write PDF → insert+commit row → defer job →
    store job_id.  Any failure after the file exists removes the file
    (and the row) so nothing half-registered survives.

    Raises:
        ApiError: 409 ``manual_exists`` on the same content hash.
    """
    from stf_v3.knowledge import tasks  # local: keeps the queue app lazy

    pages = validate_pdf(data)
    file_hash = hashlib.sha256(data).hexdigest()
    existing = (await session.execute(select(Manual).where(Manual.file_hash == file_hash))).scalar_one_or_none()
    if existing is not None:
        raise ApiError(409, "manual_exists", f"Same PDF already registered as {existing.id}")

    manual_id = uuid.uuid4()
    target = pdf_path(manual_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".pdf.part")
    tmp.write_bytes(data)
    tmp.replace(target)

    row = Manual(
        id=manual_id, uploaded_by=uploaded_by, filename=filename[:500],
        file_hash=file_hash, manufacturer=manufacturer.strip(),
        vehicle_model=vehicle_model.strip(),
        factory_code=(factory_code or "").strip() or None,
        status="queued", file_size_bytes=len(data), page_count=pages,
        pdf_file_path=str(target.relative_to(storage_root())),
        pages_processed=0, pages_total=len(STAGES), pages_phase="queued",
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        target.unlink(missing_ok=True)
        raise ApiError(409, "manual_exists", "Same PDF already registered")
    try:
        job_id = await tasks.defer_ingest(manual_id)
        row.job_id = job_id
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - undo registration, surface error
        log.error("manual.defer_failed", manual_id=str(manual_id), error=str(exc))
        await session.delete(row)
        await session.commit()
        target.unlink(missing_ok=True)
        raise ApiError(503, "queue_unavailable", "Could not queue the ingest job") from exc
    await session.refresh(row)
    log.info("manual.queued", manual_id=str(manual_id), job_id=row.job_id, pages=pages)
    return row


# ── delete ───────────────────────────────────────────────────────────


async def delete_manual(session: AsyncSession, row: Manual, by: uuid.UUID) -> None:
    """Deletes a manual: cancels/aborts its job, removes row and files.

    Seed manuals (no uploader) are protected (FM-4).  A running job sees
    the abort flag between stages and cleans up its own work dir
    (FM-10 / FM-34); its final install is skipped because the row is gone.
    """
    from stf_v3.knowledge import tasks

    if row.uploaded_by is None:
        raise forbidden("seed_protected", "Seed manuals can only be removed by an operator script")
    if row.job_id and row.status in ("queued", "converting"):
        await tasks.cancel_ingest(row.job_id)
    manual_id = row.id
    await session.delete(row)
    await session.commit()
    pdf_path(manual_id).unlink(missing_ok=True)
    shutil.rmtree(manual_dir(manual_id), ignore_errors=True)
    log.warning(
        "manual.deleted", manual_id=str(manual_id), deleted_by=str(by),
        filename=row.filename, status_before=row.status,
    )

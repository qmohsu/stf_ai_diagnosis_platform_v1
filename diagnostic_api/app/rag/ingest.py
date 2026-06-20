"""Markdown ingestion for the RAG pipeline.

Reads a structured markdown file (produced by marker-pdf, see
``scripts/marker_convert.py``), parses it into sections by heading,
chunks the sections, generates embeddings, and inserts the resulting
``RagChunk`` rows into PostgreSQL (pgvector).

This module is the single entry point for populating ``rag_chunks``.
The previous PyMuPDF-based PDF parser, OCR, vision, and translation
helpers were removed when the upload pipeline standardised on
marker-pdf.  The CLI was removed at the same time; ingestion is now
driven exclusively by the upload background task in
``app.services.manual_pipeline``.

Two phases are exposed separately so the pipeline can mark distinct
status transitions (``chunking`` -> ``embedding``):

* :func:`parse_and_chunk_md` — read the ``.md``, parse sections,
  chunk.  CPU-only, fast.
* :func:`embed_and_insert_chunks` — embed every chunk via the Ollama
  embedding service and insert into pgvector.  Network-bound, slow.

Idempotency: chunk checksums (SHA-256 of doc_id + section_title +
text) are pre-fetched in one query.  Re-running the same file is
a no-op for unchanged chunks.  Reingestion (delete-then-insert
within a single transaction) is handled by the caller, not this
module — see ``manual_pipeline.run_reingestion``.
"""

import hashlib
import re
from pathlib import Path
from typing import List, Set
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from app.models_db import Manual, RagChunk
from app.rag.chunker import ChunkedSection, Chunker
from app.rag.embedding import embedding_service
from app.rag.parser import parse_document

logger = structlog.get_logger(__name__)


# Patterns flagged by ``_audit_chunks`` after embedding.  These are
# things we already strip in ``parser.py``; the audit is a
# regression guard — if any of these appear in a fresh batch of
# chunks, something upstream is leaking pollution and we want to
# know in the logs without crashing the ingest.
_POLLUTION_PATTERNS: List[tuple] = [
    (
        "empty_html_tag",
        re.compile(
            r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>\s*</\1\s*>",
            re.IGNORECASE,
        ),
    ),
    ("html_entity", re.compile(r"&(amp|nbsp|lt|gt|quot);")),
]


def _audit_chunks(
    chunks: List[ChunkedSection],
    log: structlog.BoundLogger,
) -> None:
    """Scan chunks for known pollution patterns and log warnings.

    This catches regressions where new marker-pdf output formats
    introduce artifacts the parser hasn't been taught to strip.
    Non-fatal: emits a structured warning per pattern with the
    chunk count and a sample message.
    """
    for tag, pattern in _POLLUTION_PATTERNS:
        offenders = [
            c.chunk_index for c in chunks
            if pattern.search(c.text)
            or pattern.search(c.section_title or "")
        ]
        if offenders:
            log.warning(
                "ingest.pollution_detected",
                pattern=tag,
                count=len(offenders),
                first_chunk_index=offenders[0],
            )


def _checksum(
    doc_id: str, section_title: str, chunk_text: str,
) -> str:
    """Compute a stable SHA-256 checksum for a chunk.

    The checksum is derived from the document id, section title,
    and chunk text so it remains stable across re-runs (unlike
    index-based hashing).

    Args:
        doc_id: Document identifier (manual filename stem).
        section_title: Section heading text.
        chunk_text: Chunk body text.

    Returns:
        SHA-256 hex digest as a string.
    """
    payload = f"{doc_id}:{section_title}:{chunk_text}"
    return hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()


def _existing_checksums(
    db: Session, manual_id: UUID,
) -> Set[str]:
    """Fetch all existing checksums for a manual.

    Returns the full set in one query, eliminating the N+1
    pattern of per-chunk lookups.

    Args:
        db: SQLAlchemy session.
        manual_id: Manual UUID to filter by.

    Returns:
        Set of checksum hex strings already in the database.
    """
    rows = (
        db.query(RagChunk.checksum)
        .filter(RagChunk.manual_id == manual_id)
        .all()
    )
    return {r[0] for r in rows}


def parse_and_chunk_md(
    md_path: Path,
    chunker: Chunker,
) -> List[ChunkedSection]:
    """Parse a structured markdown file and split into chunks.

    CPU-only.  Does not touch the database.

    Args:
        md_path: Path to the structured ``.md`` file.
        chunker: Chunker instance configured with the desired
            chunk_size and overlap.

    Returns:
        List of ``Chunk`` objects ready for embedding.

    Raises:
        FileNotFoundError: If ``md_path`` does not exist.
        ValueError: If the markdown file is empty.
    """
    log = logger.bind(file=md_path.name)

    if not md_path.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {md_path}",
        )

    raw_text = md_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(
            f"Markdown file is empty: {md_path}",
        )

    sections = parse_document(raw_text, md_path.name)
    log.info(
        "ingest.parsed", section_count=len(sections),
    )

    chunks = chunker.chunk_sections(sections)
    log.info("ingest.chunked", chunk_count=len(chunks))

    return chunks


async def embed_and_insert_chunks(
    chunks: List[ChunkedSection],
    manual_id: UUID,
    doc_id: str,
    db: Session,
) -> dict:
    """Embed chunks and insert as ``RagChunk`` rows.

    All inserts for a single call are committed as one
    transaction.  On failure the entire batch is rolled back —
    the caller's manual row keeps its previous chunk_count.

    Args:
        chunks: Chunks produced by :func:`parse_and_chunk_md`.
        manual_id: UUID of the parent ``Manual`` row.  Used as
            the FK on every inserted ``RagChunk``.
        doc_id: Document identifier (typically the manual's
            ``.md`` filename stem).
        db: SQLAlchemy database session.

    Returns:
        Dict with ``inserted`` and ``skipped`` counts.
    """
    log = logger.bind(
        manual_id=str(manual_id), doc_id=doc_id,
    )
    log.info("ingest.embedding_start", count=len(chunks))

    # Pollution audit: flag any artifacts that should have been
    # stripped at parse time but aren't.  Non-fatal.
    _audit_chunks(chunks, log)

    existing = _existing_checksums(db, manual_id)

    # APP-59: the parent Manual row is the authoritative source of
    # vehicle identity (the user supplied it at upload).  Stamp its
    # manufacturer + model onto every chunk so retrieval can filter
    # by make + model reliably, rather than trusting the per-section
    # fuzzy extraction in the parser.
    manual = db.query(Manual).get(manual_id)
    manual_manufacturer = manual.manufacturer if manual else None
    manual_vehicle_model = manual.vehicle_model if manual else None

    inserted = 0
    skipped = 0

    for chunk in chunks:
        cs = _checksum(
            doc_id, chunk.section_title, chunk.text,
        )

        # Idempotency: skip if already ingested.
        if cs in existing:
            skipped += 1
            continue

        vector = await embedding_service.get_embedding(
            chunk.text,
        )
        if not vector:
            log.warning(
                "ingest.embedding_failed",
                chunk_index=chunk.chunk_index,
            )
            continue

        meta = {
            "dtc_codes": chunk.dtc_codes,
            "has_image": chunk.has_image,
        }

        row = RagChunk(
            manual_id=manual_id,
            text=chunk.text,
            doc_id=doc_id,
            source_type="manual",
            section_title=chunk.section_title,
            vehicle_model=(
                manual_vehicle_model or chunk.vehicle_model
            ),
            manufacturer=manual_manufacturer,
            chunk_index=chunk.chunk_index,
            checksum=cs,
            metadata_json=meta,
            embedding=vector,
        )
        db.add(row)
        existing.add(cs)
        inserted += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error(
            "ingest.commit_error", error=str(exc),
        )
        raise

    log.info(
        "ingest.done",
        inserted=inserted,
        skipped=skipped,
    )
    return {"inserted": inserted, "skipped": skipped}

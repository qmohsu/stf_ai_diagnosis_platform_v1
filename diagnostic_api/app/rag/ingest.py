"""Ingestion CLI script.

Reads documents from a directory, parses them into sections, chunks the
sections, generates embeddings, and upserts into Weaviate with idempotency
(SHA-256 checksum deduplication).

Usage:
    python -m app.rag.ingest --dir /app/data
    python -m app.rag.ingest --dir /app/data --force-recreate
    python -m app.rag.ingest --dir /app/data --chunk-size 600 --overlap 80
    python -m app.rag.ingest --dir /app/data --enable-ocr --enable-page-render
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import structlog
import weaviate.classes.query as wq

from app.rag.client import get_client
from app.rag.schema import init_schema
from app.rag.parser import parse_document
from app.rag.chunker import Chunker
from app.rag.embedding import embedding_service

logger = structlog.get_logger(__name__)


def _checksum(doc_id: str, section_title: str, chunk_text: str) -> str:
    """Compute a stable SHA-256 checksum for a chunk.

    The checksum is derived from the document id, section title, and chunk
    text so it remains stable across re-runs (unlike index-based hashing).
    """
    payload = f"{doc_id}:{section_title}:{chunk_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chunk_exists(collection, checksum: str) -> bool:
    """Check whether a chunk with the given checksum already exists.

    TODO(14): Batch deduplication -- fetch all checksums for a doc_id in one
    query instead of one roundtrip per chunk (N+1 query pattern).
    """
    try:
        result = collection.query.fetch_objects(
            filters=wq.Filter.by_property("checksum").equal(checksum),
            limit=1,
        )
        return len(result.objects) > 0
    except Exception as e:
        logger.warning("idempotency_check.error", error=str(e))
        return False


async def _preflight_vision_check() -> bool:
    """Verify the Ollama vision model is available.

    Returns:
        ``True`` if the vision model is ready, ``False`` otherwise.
        When ``False``, image description should be disabled to
        avoid failed API calls during ingestion.
    """
    try:
        from app.rag.vision import get_vision_service
        vision_svc = get_vision_service()
        ready = await vision_svc.check_model_ready()
        if not ready:
            logger.warning(
                "ingest.vision_model_not_ready",
                msg="Falling back to describe_images=False",
            )
        return ready
    except Exception as exc:
        logger.warning(
            "ingest.vision_preflight_error",
            error=str(exc),
        )
        return False


async def process_file(
    file_path: Path,
    client,
    chunker: Chunker,
    *,
    describe_images: bool = True,
    enable_ocr: bool = False,
    enable_page_render: bool = False,
) -> dict:
    """Process a single file: parse -> chunk -> embed -> insert.

    Args:
        file_path: Path to the file to ingest.
        client: Weaviate client.
        chunker: Chunker instance.
        describe_images: If True, use vision model to describe PDF images.
        enable_ocr: If True, run OCR on PDF images to extract text.
        enable_page_render: If True, render full PDF pages as images.

    Returns:
        Dict with inserted and skipped counts.
    """
    log = logger.bind(file=file_path.name)
    log.info("ingest.processing_file")

    doc_id = file_path.stem

    # Determine source type
    name_lower = file_path.name.lower()
    if "manual" in name_lower:
        source_type = "manual"
    elif "log" in name_lower:
        source_type = "log"
    else:
        source_type = "other"

    # Parse into sections — PDFs use font-based structured extraction;
    # text/markdown files use the original markdown heading parser.
    if file_path.suffix.lower() == ".pdf":
        from .pdf_parser import extract_pdf_sections_async
        sections = await extract_pdf_sections_async(
            file_path,
            filename=file_path.name,
            describe_images=describe_images,
            enable_ocr=enable_ocr,
            enable_page_render=enable_page_render,
        )
    else:
        text = file_path.read_text(encoding="utf-8")
        sections = parse_document(text, file_path.name)
    log.info("ingest.parsed", section_count=len(sections))

    # Chunk sections
    chunks = chunker.chunk_sections(sections)
    log.info("ingest.chunked", chunk_count=len(chunks))

    collection = client.collections.get("KnowledgeChunk")

    inserted = 0
    skipped = 0

    for chunk in chunks:
        cs = _checksum(doc_id, chunk.section_title, chunk.text)

        # Idempotency: skip if already ingested
        if _chunk_exists(collection, cs):
            skipped += 1
            continue

        # Generate embedding
        vector = await embedding_service.get_embedding(chunk.text)
        if not vector:
            log.warning(
                "ingest.embedding_failed",
                chunk_index=chunk.chunk_index,
            )
            continue

        # Build metadata JSON for extra flexibility
        meta = {
            "dtc_codes": chunk.dtc_codes,
            "has_image": chunk.has_image,
        }

        try:
            collection.data.insert(
                properties={
                    "text": chunk.text,
                    "doc_id": doc_id,
                    "source_type": source_type,
                    "section_title": chunk.section_title,
                    "vehicle_model": chunk.vehicle_model,
                    "chunk_index": chunk.chunk_index,
                    "checksum": cs,
                    "metadata_json": json.dumps(meta),
                },
                vector=vector,
            )
            inserted += 1
        except Exception as e:
            log.error(
                "ingest.insert_error",
                chunk_index=chunk.chunk_index,
                error=str(e),
            )

    log.info("ingest.file_done", inserted=inserted, skipped=skipped)
    return {"inserted": inserted, "skipped": skipped}


async def main():
    parser = argparse.ArgumentParser(
        description="Ingest documents into Weaviate."
    )
    parser.add_argument(
        "--dir", type=str, required=True, help="Directory containing documents."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Maximum chunk size in characters (default: 500).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=50,
        help="Overlap size in characters (default: 50).",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Delete and recreate Weaviate collection before ingestion.",
    )
    parser.add_argument(
        "--no-describe-images",
        action="store_true",
        help="Disable vision model image description for PDFs.",
    )
    parser.add_argument(
        "--enable-ocr",
        action="store_true",
        help="Run OCR on PDF images to extract text (part numbers, torque, etc.).",
    )
    parser.add_argument(
        "--enable-page-render",
        action="store_true",
        help="Render full PDF pages as images for OCR/vision processing.",
    )
    args = parser.parse_args()

    data_dir = Path(args.dir)
    if not data_dir.exists():
        logger.error("ingest.dir_not_found", dir=str(data_dir))
        return

    # Init client and schema
    # TODO(16): Wrap client usage in try/finally to guarantee client.close()
    # on unhandled exceptions from process_file.
    try:
        client = get_client()
        init_schema(client, force_recreate=args.force_recreate)
        logger.info("ingest.schema_ready")
    except Exception as e:
        logger.error("ingest.connection_failed", error=str(e))
        return

    chunker = Chunker(chunk_size=args.chunk_size, overlap=args.overlap)

    # Discover files (txt, md, and pdf)
    files = (
        sorted(data_dir.glob("*.txt")) +
        sorted(data_dir.glob("*.md")) +
        sorted(data_dir.glob("*.pdf"))
    )
    logger.info("ingest.files_found", count=len(files))

    total_inserted = 0
    total_skipped = 0

    describe_images = not args.no_describe_images

    # Pre-flight: verify vision model is available before processing
    if describe_images:
        vision_ready = await _preflight_vision_check()
        if not vision_ready:
            describe_images = False
            logger.warning(
                "ingest.vision_disabled",
                msg="Vision model unavailable, proceeding without "
                    "image descriptions.",
            )

    # TODO(15): Process files concurrently with bounded parallelism
    # (asyncio.Semaphore) instead of sequentially.
    for file_path in files:
        stats = await process_file(
            file_path,
            client,
            chunker,
            describe_images=describe_images,
            enable_ocr=args.enable_ocr,
            enable_page_render=args.enable_page_render,
        )
        total_inserted += stats["inserted"]
        total_skipped += stats["skipped"]

    client.close()
    logger.info(
        "ingest.complete",
        total_inserted=total_inserted,
        total_skipped=total_skipped,
    )


if __name__ == "__main__":
    asyncio.run(main())

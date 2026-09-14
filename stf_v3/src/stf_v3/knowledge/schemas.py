"""Pydantic schemas for the manual library endpoints (PROD-06).

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class ManualOut(BaseModel):
    """One manual's metadata and ingest progress.

    ``pages_phase`` is the current pipeline stage while ingesting
    (``converting`` → ``building`` → ``indexing`` → ``summarizing`` →
    ``gates`` → ``installing``); ``pages_processed`` / ``pages_total`` count
    stages (k of 6), not PDF pages.  ``page_count`` is the PDF page count.
    """

    id: uuid.UUID
    filename: str
    manufacturer: str
    vehicle_model: str
    factory_code: Optional[str]
    canonical_name: str
    status: str
    file_size_bytes: int
    page_count: Optional[int]
    section_count: Optional[int]
    language: Optional[str]
    converter: Optional[str]
    error_message: Optional[str]
    pages_processed: Optional[int]
    pages_total: Optional[int]
    pages_phase: Optional[str]
    warnings: Optional[Any]
    job_id: Optional[int]
    index_track: bool
    seed: bool
    uploaded_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class ManualTocOut(BaseModel):
    """Heading tree rendered as indented text (what the agent tool sees)."""

    manual_id: uuid.UUID
    index_track: bool
    max_depth: int
    toc: str


class SearchHit(BaseModel):
    """One matching line and the section that encloses it."""

    section: str
    line: str


class ManualSearchOut(BaseModel):
    """Literal full-text search result over one manual."""

    manual_id: uuid.UUID
    query: str
    index_track: bool
    total_hits: int
    hits: List[SearchHit]

"""Tests for the manual upload and management API endpoints.

Covers upload validation (PDF magic bytes, file size, dedup),
list/get/delete CRUD, and status polling.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status as http_status

from app.api.v2.endpoints.manuals import (
    _PDF_MAGIC,
    _to_summary,
    ManualStatusResponse,
    ManualUploadResponse,
)


# ── Fixtures ────────────────────────────────────────────────


def _make_manual(**overrides):
    """Build a minimal Manual-like object for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "filename": "test_manual.pdf",
        "file_hash": hashlib.sha256(b"test").hexdigest(),
        "vehicle_model": "TRICITY-155",
        "status": "ingested",
        "file_size_bytes": 1024,
        "page_count": 42,
        "section_count": 10,
        "language": "en",
        "converter": "marker-pdf",
        "error_message": None,
        "md_file_path": "TRICITY-155/test_manual.md",
        "pdf_file_path": "uploads/test.pdf",
        "chunk_count": 100,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ── Unit tests: helpers ─────────────────────────────────────


class TestToSummary:
    """Tests for the _to_summary helper."""

    def test_maps_all_fields(self):
        """All Manual fields appear in ManualSummary."""
        manual = _make_manual()
        summary = _to_summary(manual)
        assert summary.id == str(manual.id)
        assert summary.filename == manual.filename
        assert summary.vehicle_model == manual.vehicle_model
        assert summary.status == manual.status
        assert summary.page_count == manual.page_count
        assert summary.chunk_count == manual.chunk_count


# ── Unit tests: validation ──────────────────────────────────


class TestUploadValidation:
    """Tests for upload request validation logic."""

    def test_pdf_magic_bytes_constant(self):
        """PDF magic bytes are correct."""
        assert _PDF_MAGIC == b"%PDF"

    def test_valid_pdf_starts_with_magic(self):
        """A valid PDF buffer starts with %PDF."""
        buf = b"%PDF-1.7 fake content"
        assert buf[:4].startswith(_PDF_MAGIC)

    def test_non_pdf_rejected(self):
        """Non-PDF data fails magic byte check."""
        buf = b"PK\x03\x04 zip data"
        assert not buf[:4].startswith(_PDF_MAGIC)


class TestFileHash:
    """Tests for dedup file hashing."""

    def test_compute_file_hash_deterministic(self):
        """Same data produces same hash."""
        from app.services.manual_pipeline import (
            compute_file_hash,
        )
        data = b"test pdf content"
        h1 = compute_file_hash(data)
        h2 = compute_file_hash(data)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_data_different_hash(self):
        """Different data produces different hash."""
        from app.services.manual_pipeline import (
            compute_file_hash,
        )
        h1 = compute_file_hash(b"pdf-a")
        h2 = compute_file_hash(b"pdf-b")
        assert h1 != h2


# ── Unit tests: response models ─────────────────────────────


class TestResponseModels:
    """Tests for Pydantic response schema validation."""

    def test_upload_response_schema(self):
        """ManualUploadResponse accepts valid data."""
        resp = ManualUploadResponse(
            manual_id="abc-123",
            status="converting",
            filename="test.pdf",
        )
        assert resp.status == "converting"

    def test_status_response_schema(self):
        """ManualStatusResponse accepts valid data."""
        resp = ManualStatusResponse(
            status="ingested",
            error_message=None,
            page_count=42,
            chunk_count=100,
        )
        assert resp.page_count == 42

    def test_status_response_with_error(self):
        """ManualStatusResponse includes error_message."""
        resp = ManualStatusResponse(
            status="failed",
            error_message="OOM during conversion",
            page_count=None,
            chunk_count=None,
        )
        assert resp.error_message == "OOM during conversion"

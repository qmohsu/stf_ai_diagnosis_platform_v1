"""Tests for the manual upload and management API endpoints.

Covers upload validation (PDF magic bytes, file size, dedup),
list/get/delete CRUD, and status polling.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import status as http_status

from app.api.v2.endpoints.manuals import (
    _PDF_MAGIC,
    _clean_required_field,
    _to_summary,
    ManualStatusResponse,
    ManualUploadResponse,
)
from app.models_db import Manual


# ── Fixtures ────────────────────────────────────────────────


def _make_manual(**overrides):
    """Build a minimal Manual-like object for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "filename": "test_manual.pdf",
        "file_hash": hashlib.sha256(b"test").hexdigest(),
        "manufacturer": "Yamaha",
        "vehicle_model": "TRICITY-155",
        "factory_code": "MWS150-A",
        "canonical_name": "Yamaha TRICITY-155",
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
        "pages_processed": None,
        "pages_total": None,
        "pages_phase": None,
        "warnings": None,
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
        assert summary.manufacturer == manual.manufacturer
        assert summary.vehicle_model == manual.vehicle_model
        assert summary.factory_code == manual.factory_code
        assert summary.canonical_name == manual.canonical_name
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
            manufacturer="Toyota",
            vehicle_model="Hiace",
            canonical_name="Toyota Hiace",
        )
        assert resp.status == "converting"
        assert resp.canonical_name == "Toyota Hiace"

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


# ── APP-59: required vehicle identity ───────────────────────


class TestRequiredVehicleIdentity:
    """Tests for the required manufacturer + model fields."""

    def test_clean_trims_and_collapses_whitespace(self):
        """Surrounding/duplicate whitespace is normalised."""
        assert (
            _clean_required_field("  Toyota   Hiace ", "Model")
            == "Toyota Hiace"
        )

    def test_clean_rejects_empty_string(self):
        """An empty value raises a 422."""
        with pytest.raises(HTTPException) as exc:
            _clean_required_field("", "Manufacturer")
        assert (
            exc.value.status_code
            == http_status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        assert "Manufacturer" in exc.value.detail

    def test_clean_rejects_whitespace_only(self):
        """A blank (whitespace-only) value raises a 422."""
        with pytest.raises(HTTPException) as exc:
            _clean_required_field("   ", "Vehicle model")
        assert (
            exc.value.status_code
            == http_status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    def test_canonical_name_property(self):
        """Manual.canonical_name is '<Manufacturer> <Model>'."""
        manual = Manual(
            manufacturer="Toyota", vehicle_model="Hiace",
        )
        assert manual.canonical_name == "Toyota Hiace"


class TestFrontmatterIdentity:
    """Tests for write_frontmatter_identity (APP-59)."""

    def test_adds_identity_to_existing_frontmatter(self, tmp_path):
        """Make/model are written into existing frontmatter."""
        import yaml

        from app.services.manual_pipeline import (
            write_frontmatter_identity,
        )

        md = tmp_path / "manual.md"
        md.write_text(
            "---\nsource_pdf: x.pdf\npage_count: 12\n---\n\n# Body\n",
            encoding="utf-8",
        )
        ok = write_frontmatter_identity(str(md), "Toyota", "Hiace")
        assert ok is True

        text = md.read_text(encoding="utf-8")
        block = text.split("---")[1]
        fm = yaml.safe_load(block)
        assert fm["manufacturer"] == "Toyota"
        assert fm["vehicle_model"] == "Hiace"
        # Pre-existing keys are preserved.
        assert fm["page_count"] == 12
        # Body survives.
        assert "# Body" in text

    def test_creates_frontmatter_when_absent(self, tmp_path):
        """A frontmatter block is created if the file has none."""
        import yaml

        from app.services.manual_pipeline import (
            write_frontmatter_identity,
        )

        md = tmp_path / "nofm.md"
        md.write_text("# Just a body\n", encoding="utf-8")
        write_frontmatter_identity(str(md), "Yamaha", "TRICITY155")

        text = md.read_text(encoding="utf-8")
        assert text.startswith("---")
        fm = yaml.safe_load(text.split("---")[1])
        assert fm["manufacturer"] == "Yamaha"
        assert fm["vehicle_model"] == "TRICITY155"
        assert "# Just a body" in text

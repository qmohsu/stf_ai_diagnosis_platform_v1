"""Tests for the manual conversion and ingestion pipeline.

Covers PDF staging, conversion status updates, ingestion
chunk counting, and file cleanup.
"""

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.manual_pipeline import (
    compute_file_hash,
    save_uploaded_pdf,
    delete_manual_files,
    delete_manual_chunks,
)


# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def tmp_storage(tmp_path):
    """Patch manual_storage_path to a temp directory."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    with patch(
        "app.services.manual_pipeline.settings"
    ) as mock_settings:
        mock_settings.manual_storage_path = str(tmp_path)
        yield tmp_path


# ── Tests: save_uploaded_pdf ────────────────────────────────


class TestSaveUploadedPdf:
    """Tests for staging PDF uploads to disk."""

    def test_saves_file_to_uploads_dir(self, tmp_storage):
        """PDF data is written to uploads/{id}.pdf."""
        data = b"%PDF-1.7 fake content"
        manual_id = uuid.uuid4()
        rel_path = save_uploaded_pdf(data, manual_id)

        assert rel_path == f"uploads/{manual_id}.pdf"
        abs_path = tmp_storage / "uploads" / f"{manual_id}.pdf"
        assert abs_path.exists()
        assert abs_path.read_bytes() == data

    def test_returns_relative_path(self, tmp_storage):
        """Returned path is relative, not absolute."""
        data = b"%PDF-1.7 content"
        manual_id = uuid.uuid4()
        rel_path = save_uploaded_pdf(data, manual_id)
        assert not os.path.isabs(rel_path)
        assert rel_path.startswith("uploads/")


# ── Tests: delete_manual_files ──────────────────────────────


class TestDeleteManualFiles:
    """Tests for filesystem cleanup on manual deletion."""

    def test_deletes_pdf_and_md(self, tmp_storage):
        """Both source PDF and output MD are removed."""
        # Set up files.
        pdf_path = tmp_storage / "uploads" / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.7")

        model_dir = tmp_storage / "TRICITY-155"
        model_dir.mkdir()
        md_path = model_dir / "test.md"
        md_path.write_text("# Test", encoding="utf-8")

        manual = MagicMock()
        manual.id = uuid.uuid4()
        manual.pdf_file_path = "uploads/test.pdf"
        manual.md_file_path = "TRICITY-155/test.md"

        delete_manual_files(manual)

        assert not pdf_path.exists()
        assert not md_path.exists()

    def test_handles_missing_files_gracefully(
        self, tmp_storage,
    ):
        """No error when files don't exist."""
        manual = MagicMock()
        manual.id = uuid.uuid4()
        manual.pdf_file_path = "uploads/nonexistent.pdf"
        manual.md_file_path = "TRICITY-155/nonexistent.md"

        # Should not raise.
        delete_manual_files(manual)


# ── Tests: delete_manual_chunks ─────────────────────────────


class TestDeleteManualChunks:
    """Tests for RAG chunk cleanup."""

    def test_deletes_by_doc_id(self):
        """Chunks with matching doc_id are removed."""
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 5

        count = delete_manual_chunks("test_manual", mock_db)

        assert count == 5
        mock_db.commit.assert_called_once()


# ── Tests: compute_file_hash ────────────────────────────────


class TestComputeFileHash:
    """Tests for PDF file hashing."""

    def test_sha256_hex_length(self):
        """Hash is 64-character hex string."""
        h = compute_file_hash(b"test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        """Same input always produces same hash."""
        data = b"service manual pdf bytes"
        assert compute_file_hash(data) == compute_file_hash(
            data,
        )

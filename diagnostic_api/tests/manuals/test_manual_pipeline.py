"""Tests for the manual conversion and ingestion pipeline.

Covers PDF staging, conversion status updates, ingestion
chunk counting, and file cleanup.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.manual_pipeline import (
    _aware_utc,
    cleanup_orphan_files,
    compute_file_hash,
    delete_manual_chunks,
    delete_manual_files,
    save_uploaded_pdf,
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

    def test_deletes_by_manual_id(self):
        """Chunks with matching manual_id are removed."""
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 5

        manual_id = uuid.uuid4()
        count = delete_manual_chunks(manual_id, mock_db)

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


# ── Tests: _aware_utc ───────────────────────────────────────


class TestAwareUtc:
    """Tests for the _aware_utc timezone-normalisation helper."""

    def test_naive_datetime_becomes_utc_aware(self):
        """Naive datetime is tagged as UTC without value change."""
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = _aware_utc(naive)
        assert result.tzinfo is not None
        assert result == datetime(
            2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc,
        )

    def test_already_aware_utc_unchanged(self):
        """UTC-aware datetime passes through unmodified."""
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _aware_utc(aware)
        assert result == aware
        assert result.tzinfo == timezone.utc

    def test_does_not_raise_on_naive(self):
        """Subtracting from now() after normalisation never raises."""
        now = datetime.now(timezone.utc)
        naive = datetime.utcnow()
        # Must not raise TypeError.
        age = (now - _aware_utc(naive)).total_seconds()
        assert abs(age) < 2  # Same instant, within 2 seconds.


# ── Tests: cleanup_orphan_files (tz-safety) ─────────────────


def _make_mock_manual(
    *,
    status: str = "failed",
    updated_at: datetime,
    manual_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a minimal Manual-like mock for cleanup tests.

    Args:
        status: Manual status string.
        updated_at: The ``updated_at`` value to use (naive or aware).
        manual_id: Optional UUID; auto-generated if omitted.

    Returns:
        A MagicMock that satisfies cleanup_orphan_files attribute
        access.
    """
    m = MagicMock()
    m.id = manual_id or uuid.uuid4()
    m.status = status
    m.updated_at = updated_at
    m.pdf_file_path = None
    m.md_file_path = None
    return m


class TestCleanupOrphanFilesTimezone:
    """Tests that cleanup_orphan_files handles tz-naive updated_at."""

    def _run(self, tmp_path: object, manuals: list) -> dict:
        """Execute cleanup_orphan_files with a mocked DB session.

        Args:
            tmp_path: Temporary directory used as storage root.
            manuals: List of Manual-like mocks returned by the query.

        Returns:
            The dict returned by cleanup_orphan_files.
        """
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = manuals

        with (
            patch(
                "app.services.manual_pipeline.settings"
            ) as mock_settings,
            patch(
                "app.services.manual_pipeline.SessionLocal",
                return_value=mock_db,
            ),
            patch(
                "app.services.manual_pipeline.delete_manual_files",
            ),
        ):
            mock_settings.manual_storage_path = str(tmp_path)
            return cleanup_orphan_files(grace_seconds=3600)

    def test_no_type_error_with_naive_updated_at_in_grace(
        self, tmp_path
    ):
        """Naive updated_at within grace period raises no TypeError."""
        recent_naive = datetime.utcnow() - timedelta(seconds=60)
        m = _make_mock_manual(
            status="failed", updated_at=recent_naive,
        )
        # Must not raise TypeError.
        result = self._run(tmp_path, [m])
        assert isinstance(result, dict)

    def test_no_type_error_with_aware_updated_at_in_grace(
        self, tmp_path
    ):
        """Tz-aware updated_at within grace period raises no TypeError."""
        recent_aware = datetime.now(timezone.utc) - timedelta(
            seconds=60,
        )
        m = _make_mock_manual(
            status="failed", updated_at=recent_aware,
        )
        result = self._run(tmp_path, [m])
        assert isinstance(result, dict)

    def test_stale_naive_failed_row_is_removed(self, tmp_path):
        """Failed row with naive timestamp older than grace is deleted."""
        old_naive = datetime.utcnow() - timedelta(seconds=7200)
        m = _make_mock_manual(status="failed", updated_at=old_naive)
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [m]

        with (
            patch(
                "app.services.manual_pipeline.settings"
            ) as mock_settings,
            patch(
                "app.services.manual_pipeline.SessionLocal",
                return_value=mock_db,
            ),
            patch(
                "app.services.manual_pipeline.delete_manual_files",
            ),
        ):
            mock_settings.manual_storage_path = str(tmp_path)
            cleanup_orphan_files(grace_seconds=3600)

        mock_db.delete.assert_called_once_with(m)
        mock_db.commit.assert_called()

    def test_fresh_naive_failed_row_is_kept(self, tmp_path):
        """Failed row with naive timestamp within grace is NOT deleted."""
        recent_naive = datetime.utcnow() - timedelta(seconds=60)
        m = _make_mock_manual(
            status="failed", updated_at=recent_naive,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [m]

        with (
            patch(
                "app.services.manual_pipeline.settings"
            ) as mock_settings,
            patch(
                "app.services.manual_pipeline.SessionLocal",
                return_value=mock_db,
            ),
            patch(
                "app.services.manual_pipeline.delete_manual_files",
            ),
        ):
            mock_settings.manual_storage_path = str(tmp_path)
            cleanup_orphan_files(grace_seconds=3600)

        mock_db.delete.assert_not_called()


# ── Tests: _run_marker_convert (API key not on disk) ────────


class TestMarkerConvertApiKeyNotOnDisk:
    """Tests that the queue request JSON never contains the API key."""

    def test_request_json_omits_api_key(self, tmp_path):
        """Queue JSON written to disk must not contain the API key.

        _run_marker_convert writes a request JSON to .queue/ before
        polling for the result.  This test confirms the plaintext
        API key is absent from that file (CWE-312 regression guard).
        """
        from app.services.manual_pipeline import _run_marker_convert
        import structlog

        queue_dir = tmp_path / ".queue"
        queue_dir.mkdir()
        manual_id = uuid.uuid4()
        req_path = queue_dir / f"{manual_id}.request.json"
        res_path = queue_dir / f"{manual_id}.result.json"
        SECRET = "sk-openrouter-super-secret-99999"
        captured: dict = {}

        async def fake_sleep(_seconds: float) -> None:
            """Capture the request file then signal completion."""
            if req_path.exists() and not captured:
                captured.update(
                    json.loads(req_path.read_text(encoding="utf-8"))
                )
            res_path.write_text(
                json.dumps(
                    {"status": "ok", "vehicle_model": "TEST"}
                ),
                encoding="utf-8",
            )

        with (
            patch(
                "app.services.manual_pipeline.settings"
            ) as mock_settings,
            patch(
                "app.services.manual_pipeline._sync_progress_to_db",
                return_value=None,
            ),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            mock_settings.manual_storage_path = str(tmp_path)
            mock_settings.premium_llm_api_key = SECRET
            mock_settings.premium_llm_base_url = (
                "https://openrouter.ai/api/v1"
            )
            mock_settings.manual_llm_model = "gpt-4o"
            mock_settings.marker_poll_interval_seconds = 0

            log = structlog.get_logger("test")
            asyncio.run(
                _run_marker_convert(
                    "uploads/test.pdf",
                    manual_id,
                    log,
                )
            )

        assert captured, "Request JSON was never captured"
        assert "openai_api_key" not in captured, (
            "openai_api_key key must not appear in queue JSON"
        )
        assert SECRET not in json.dumps(captured), (
            "API key value must not appear in queue JSON"
        )

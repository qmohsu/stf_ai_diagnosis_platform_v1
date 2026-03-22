"""Tests for ingestion idempotency.

The checksum tests are pure unit tests.  The mocked integration test
verifies the skip-if-exists logic without a live PostgreSQL instance.

    pytest infra/test_ingest_idempotency.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "diagnostic_api"),
)

from app.rag.ingest import _checksum, process_file
from app.rag.chunker import Chunker


# ---------------------------------------------------------------------------
# Checksum stability
# ---------------------------------------------------------------------------

class TestChecksum:
    """Verify SHA-256 checksum determinism."""

    def test_deterministic(self):
        """Same inputs must always produce the same checksum."""
        c1 = _checksum("doc1", "Section A", "hello world")
        c2 = _checksum("doc1", "Section A", "hello world")
        assert c1 == c2

    def test_different_text_different_checksum(self):
        """Different chunk text must produce different checksums."""
        c1 = _checksum("doc1", "Section A", "text one")
        c2 = _checksum("doc1", "Section A", "text two")
        assert c1 != c2

    def test_different_section_different_checksum(self):
        """Different section titles must produce different checksums."""
        c1 = _checksum("doc1", "Section A", "same text")
        c2 = _checksum("doc1", "Section B", "same text")
        assert c1 != c2

    def test_sha256_length(self):
        """Checksum must be a 64-character SHA-256 hex digest."""
        c = _checksum("d", "s", "t")
        assert len(c) == 64  # SHA-256 hex digest


# ---------------------------------------------------------------------------
# Idempotency integration test (mocked PostgreSQL)
# ---------------------------------------------------------------------------

class TestIdempotencyMocked:
    """Test idempotency logic without a real database."""

    @pytest.fixture
    def tmp_doc(self, tmp_path):
        """Create a small temporary document."""
        p = tmp_path / "sample_manual.txt"
        p.write_text(
            "# STF-850 Manual\n\n"
            "## Engine\n\n"
            "P0171 lean condition info.\n",
            encoding="utf-8",
        )
        return p

    def test_second_run_skips_all(self, tmp_doc):
        """Second ingestion of the same file should skip all chunks."""
        inserted_checksums: set = set()

        # --- Build mock DB session ---
        mock_db = MagicMock()

        # First run: no existing checksums -> all inserts
        mock_db.query.return_value.filter.return_value \
            .all.return_value = []

        def fake_add(row):
            inserted_checksums.add(row.checksum)

        mock_db.add.side_effect = fake_add
        mock_db.commit.return_value = None

        dummy_vector = [0.1] * 768
        chunker = Chunker(chunk_size=500, overlap=50)

        # Patch embedding_service.get_embedding
        async def fake_embedding(text):
            return dummy_vector

        with patch(
            "app.rag.ingest.embedding_service.get_embedding",
            side_effect=fake_embedding,
        ):
            # --- First run: should insert ---
            stats1 = asyncio.run(
                process_file(tmp_doc, mock_db, chunker)
            )
            assert stats1["inserted"] >= 1, (
                f"Expected inserts, got {stats1}"
            )

            # --- Second run: existing checksums found ---
            existing_rows = [
                (cs,) for cs in inserted_checksums
            ]
            mock_db.query.return_value.filter.return_value \
                .all.return_value = existing_rows
            mock_db.add.reset_mock()

            stats2 = asyncio.run(
                process_file(tmp_doc, mock_db, chunker)
            )
            assert stats2["skipped"] >= 1, (
                f"Expected skips, got {stats2}"
            )
            assert stats2["inserted"] == 0, (
                f"Expected 0 inserts, got {stats2}"
            )
            assert mock_db.add.call_count == 0, (
                "Expected no db.add() calls on second run"
            )

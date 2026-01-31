"""Tests for ingestion idempotency.

The checksum tests are pure unit tests.  The mocked integration test
verifies the skip-if-exists logic without a live Weaviate instance.

    pytest infra/test_ingest_idempotency.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "diagnostic_api"))

from app.rag.ingest import _checksum, _chunk_exists, process_file
from app.rag.chunker import Chunker


# ---------------------------------------------------------------------------
# Checksum stability
# ---------------------------------------------------------------------------

class TestChecksum:
    def test_deterministic(self):
        """Same inputs must always produce the same checksum."""
        c1 = _checksum("doc1", "Section A", "hello world")
        c2 = _checksum("doc1", "Section A", "hello world")
        assert c1 == c2

    def test_different_text_different_checksum(self):
        c1 = _checksum("doc1", "Section A", "text one")
        c2 = _checksum("doc1", "Section A", "text two")
        assert c1 != c2

    def test_different_section_different_checksum(self):
        c1 = _checksum("doc1", "Section A", "same text")
        c2 = _checksum("doc1", "Section B", "same text")
        assert c1 != c2

    def test_sha256_length(self):
        c = _checksum("d", "s", "t")
        assert len(c) == 64  # SHA-256 hex digest is 64 chars


# ---------------------------------------------------------------------------
# Idempotency integration test (mocked Weaviate)
# ---------------------------------------------------------------------------

class _FakeQueryResult:
    """Mimics Weaviate query result with an objects list."""
    def __init__(self, objects=None):
        self.objects = objects or []


class TestIdempotencyMocked:
    """Test idempotency logic without a real Weaviate instance."""

    @pytest.fixture
    def tmp_doc(self, tmp_path):
        """Create a small temporary document."""
        p = tmp_path / "sample_manual.txt"
        p.write_text(
            "# STF-850 Manual\n\n## Engine\n\nP0171 lean condition info.\n",
            encoding="utf-8",
        )
        return p

    def test_second_run_skips_all(self, tmp_doc):
        """Second ingestion of the same file should skip all chunks."""
        inserted_checksums: set = set()

        # --- Build mock collection (sync methods) ---
        mock_query = MagicMock()
        mock_data = MagicMock()
        mock_collection = MagicMock()
        mock_collection.query = mock_query
        mock_collection.data = mock_data

        # First run: fetch_objects returns empty (not found) -> inserts
        mock_query.fetch_objects.return_value = _FakeQueryResult([])

        def fake_insert(properties, vector):
            inserted_checksums.add(properties["checksum"])

        mock_data.insert.side_effect = fake_insert

        # --- Build mock client (sync) ---
        mock_client = MagicMock()
        mock_client.collections.get.return_value = mock_collection

        dummy_vector = [0.1] * 768
        chunker = Chunker(chunk_size=500, overlap=50)

        # Patch embedding_service.get_embedding as a coroutine
        async def fake_embedding(text):
            return dummy_vector

        with patch(
            "app.rag.ingest.embedding_service.get_embedding",
            side_effect=fake_embedding,
        ):
            # --- First run: should insert ---
            stats1 = asyncio.run(
                process_file(tmp_doc, mock_client, chunker)
            )
            assert stats1["inserted"] >= 1, f"Expected inserts, got {stats1}"

            # --- Second run: make fetch_objects return "found" ---
            mock_query.fetch_objects.return_value = _FakeQueryResult([{"x": 1}])
            mock_data.insert.reset_mock()

            stats2 = asyncio.run(
                process_file(tmp_doc, mock_client, chunker)
            )
            assert stats2["skipped"] >= 1, f"Expected skips, got {stats2}"
            assert stats2["inserted"] == 0, f"Expected 0 inserts, got {stats2}"

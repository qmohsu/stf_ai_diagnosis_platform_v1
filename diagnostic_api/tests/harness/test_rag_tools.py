"""Tests for the redesigned search_manual RAG tool."""

from unittest.mock import AsyncMock, patch

import pytest


# ------------------------------------------------------------------
# Fake RetrievalResult objects
# ------------------------------------------------------------------


class _FakeResult:
    """Minimal stand-in for ``RetrievalResult``."""

    def __init__(
        self, text, score, doc_id, section_title,
    ):
        self.text = text
        self.score = score
        self.doc_id = doc_id
        self.section_title = section_title


FAKE_RESULTS = [
    _FakeResult(
        text="Check fuel pressure regulator for leaks.",
        score=0.87,
        doc_id="MWS150-A",
        section_title="Fuel System Inspection",
    ),
    _FakeResult(
        text="Inspect ignition coil resistance.",
        score=0.72,
        doc_id="MWS150-A",
        section_title="Ignition System",
    ),
    _FakeResult(
        text="Verify catalytic converter operation.",
        score=0.65,
        doc_id="MWS150-B",
        section_title="Exhaust System",
    ),
]


# ------------------------------------------------------------------
# Tests: search_manual
# ------------------------------------------------------------------


class TestSearchManual:
    """Tests for the search_manual tool handler."""

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self):
        """Output lists results with score and doc_id."""
        from app.harness_tools.rag_tools import search_manual

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=FAKE_RESULTS,
        ):
            result = await search_manual(
                {"query": "fuel pressure", "top_k": 5},
            )

        assert isinstance(result, str)
        assert "[0.87]" in result
        assert "MWS150-A#Fuel System Inspection" in result
        assert "Check fuel pressure" in result

    @pytest.mark.asyncio
    async def test_empty_results_generic(self):
        """No matches without vehicle_model returns guidance."""
        from app.harness_tools.rag_tools import search_manual

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await search_manual(
                {"query": "nonexistent"},
            )

        assert "No manual sections found" in result
        assert "Try different keywords" in result

    @pytest.mark.asyncio
    async def test_empty_results_with_model(self):
        """No matches with vehicle_model mentions the model."""
        from app.harness_tools.rag_tools import search_manual

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await search_manual({
                "query": "fuel system",
                "vehicle_model": "STF-850",
            })

        assert "STF-850" in result
        assert "No service manual sections found" in result

    @pytest.mark.asyncio
    async def test_default_top_k(self):
        """Default top_k is 5 when not specified."""
        from app.harness_tools.rag_tools import search_manual

        mock_retrieve = AsyncMock(return_value=[])
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            mock_retrieve,
        ):
            await search_manual({"query": "test"})

        mock_retrieve.assert_awaited_once_with(
            "test",
            top_k=5,
            vehicle_model=None,
            exclude_chunk_ids=None,
        )

    @pytest.mark.asyncio
    async def test_vehicle_model_passed_through(self):
        """vehicle_model is forwarded to retrieve_context."""
        from app.harness_tools.rag_tools import search_manual

        mock_retrieve = AsyncMock(return_value=[])
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            mock_retrieve,
        ):
            await search_manual({
                "query": "fuel",
                "vehicle_model": "MWS-150-A",
            })

        mock_retrieve.assert_awaited_once_with(
            "fuel",
            top_k=5,
            vehicle_model="MWS-150-A",
            exclude_chunk_ids=None,
        )

    @pytest.mark.asyncio
    async def test_exclude_chunk_ids_passed(self):
        """exclude_chunk_ids is forwarded to retrieve_context."""
        from app.harness_tools.rag_tools import search_manual

        mock_retrieve = AsyncMock(return_value=[])
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            mock_retrieve,
        ):
            await search_manual({
                "query": "fuel",
                "exclude_chunk_ids": [10, 20, 30],
            })

        mock_retrieve.assert_awaited_once_with(
            "fuel",
            top_k=5,
            vehicle_model=None,
            exclude_chunk_ids=[10, 20, 30],
        )

    @pytest.mark.asyncio
    async def test_long_text_truncated(self):
        """Chunk text longer than _MAX_TEXT_LEN is truncated."""
        from app.harness_tools.rag_tools import (
            _MAX_TEXT_LEN,
            search_manual,
        )

        long_result = _FakeResult(
            text="x" * 1000,
            score=0.90,
            doc_id="DOC",
            section_title="Section",
        )
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=[long_result],
        ):
            result = await search_manual(
                {"query": "test"},
            )

        assert "..." in result
        assert "x" * _MAX_TEXT_LEN in result

"""Tests for RAG tool wrappers (search_manual, refine_search)."""

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
                {"query": "fuel pressure", "top_k": 3},
            )

        assert isinstance(result, str)
        assert "[0.87]" in result
        assert "MWS150-A#Fuel System Inspection" in result
        assert "Check fuel pressure" in result

    @pytest.mark.asyncio
    async def test_empty_results(self):
        """No matches returns descriptive message."""
        from app.harness_tools.rag_tools import search_manual

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await search_manual(
                {"query": "nonexistent"},
            )

        assert isinstance(result, str)
        assert "No matching" in result

    @pytest.mark.asyncio
    async def test_default_top_k(self):
        """Default top_k is 3 when not specified."""
        from app.harness_tools.rag_tools import search_manual

        mock_retrieve = AsyncMock(return_value=[])
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            mock_retrieve,
        ):
            await search_manual({"query": "test"})

        mock_retrieve.assert_awaited_once_with(
            "test", top_k=3,
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
        # The truncated text should be _MAX_TEXT_LEN chars
        # plus the "..." suffix.
        assert "x" * _MAX_TEXT_LEN in result


# ------------------------------------------------------------------
# Tests: refine_search
# ------------------------------------------------------------------


class TestRefineSearch:
    """Tests for the refine_search tool handler."""

    @pytest.mark.asyncio
    async def test_excludes_doc_ids(self):
        """Results with excluded doc_ids are filtered out."""
        from app.harness_tools.rag_tools import refine_search

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=FAKE_RESULTS,
        ):
            result = await refine_search({
                "query": "fuel system",
                "top_k": 3,
                "exclude_doc_ids": ["MWS150-A"],
            })

        assert isinstance(result, str)
        # MWS150-A results should be excluded.
        assert "MWS150-A" not in result
        # MWS150-B should remain.
        assert "MWS150-B" in result

    @pytest.mark.asyncio
    async def test_over_fetches(self):
        """retrieve_context is called with top_k + len(exclude)."""
        from app.harness_tools.rag_tools import refine_search

        mock_retrieve = AsyncMock(return_value=[])
        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            mock_retrieve,
        ):
            await refine_search({
                "query": "test",
                "top_k": 3,
                "exclude_doc_ids": ["A", "B"],
            })

        mock_retrieve.assert_awaited_once_with(
            "test", top_k=5,  # 3 + 2 excluded
        )

    @pytest.mark.asyncio
    async def test_no_exclude(self):
        """Without exclude_doc_ids, behaves like search_manual."""
        from app.harness_tools.rag_tools import refine_search

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=FAKE_RESULTS,
        ):
            result = await refine_search(
                {"query": "fuel", "top_k": 3},
            )

        assert isinstance(result, str)
        assert "MWS150-A" in result
        assert "MWS150-B" in result

    @pytest.mark.asyncio
    async def test_all_excluded_returns_message(self):
        """All results excluded returns 'No matching' message."""
        from app.harness_tools.rag_tools import refine_search

        with patch(
            "app.harness_tools.rag_tools.retrieve_context",
            new_callable=AsyncMock,
            return_value=FAKE_RESULTS,
        ):
            result = await refine_search({
                "query": "fuel",
                "top_k": 3,
                "exclude_doc_ids": [
                    "MWS150-A", "MWS150-B",
                ],
            })

        assert "No matching" in result

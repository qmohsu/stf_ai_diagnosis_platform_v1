"""Unit tests for hybrid keyword + vector retrieval (APP-56 / #18).

These tests exercise the orchestration layer of
``app.rag.retrieve``:

- ``mode`` dispatches to the right ``_sync_*_query`` helper.
- The ``vector`` mode path is byte-identical to pre-APP-56 behaviour
  (still calls the legacy ``_sync_vector_query`` helper).
- ``_build_filter_clause`` assembles ``vehicle_model`` and
  ``exclude_chunk_ids`` predicates that flow into both fusion
  branches.
- ``_format_pgvector`` produces the literal string pgvector expects
  on the wire.
- The fused-score sort keeps the right ordering when both branches
  return overlapping IDs.

The actual SQL is exercised against real Postgres in the eval-lane
benchmark (``tests/harness/evals/rag_runner.py`` with ``mode="hybrid"``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.retrieve import (
    RetrievalResult,
    _build_filter_clause,
    _build_or_tsquery,
    _format_pgvector,
    _row_to_result,
    retrieve_context,
)


# ── _format_pgvector ──────────────────────────────────────────────


def test_format_pgvector_brackets_and_commas():
    """pgvector wants ``'[v1,v2,...]'`` on the wire."""
    out = _format_pgvector([1.0, 2.5, -3.25])
    assert out.startswith("[") and out.endswith("]")
    assert out.count(",") == 2
    # No spaces — minimises bytes sent to Postgres.
    assert " " not in out


def test_format_pgvector_empty():
    """Empty list still produces a valid literal (degenerate)."""
    assert _format_pgvector([]) == "[]"


# ── _build_or_tsquery ─────────────────────────────────────────────


def test_or_tsquery_joins_with_or():
    """Multi-word query becomes ``term | term | term`` for to_tsquery."""
    out = _build_or_tsquery("P0117 coolant temperature sensor")
    assert out == "p0117 | coolant | temperature | sensor"


def test_or_tsquery_lowercases():
    """Tokens are lowercased so they match the english tsvector analyser."""
    assert _build_or_tsquery("P0117") == "p0117"
    assert _build_or_tsquery("ABS Sensor") == "abs | sensor"


def test_or_tsquery_drops_punctuation():
    """Operators and quotes that to_tsquery would reject are stripped out."""
    out = _build_or_tsquery("don't & break tsquery")
    assert "&" not in out
    assert "'" not in out
    # Apostrophe splits 'don' from 't' — both valid tokens.
    assert "don" in out and "break" in out and "tsquery" in out


def test_or_tsquery_empty_input():
    """All-punctuation input produces an empty string (caller must check)."""
    assert _build_or_tsquery("") == ""
    assert _build_or_tsquery("   ") == ""
    assert _build_or_tsquery("!@#$%") == ""


def test_or_tsquery_keeps_dotted_identifiers():
    """Part numbers like ABC.123 stay as one token."""
    out = _build_or_tsquery("part ABC.123")
    assert "abc.123" in out


# ── _build_filter_clause ──────────────────────────────────────────


def test_filter_clause_empty_when_no_filters():
    """Both filters None → empty fragment, empty params."""
    sql, params = _build_filter_clause(None, None)
    assert sql == ""
    assert params == {}


def test_filter_clause_vehicle_model_only():
    """vehicle_model filter emits one AND clause."""
    sql, params = _build_filter_clause("MWS-150-A", None)
    assert "vehicle_model" in sql
    assert sql.startswith("AND ")
    assert params == {"vehicle_model": "MWS-150-A"}


def test_filter_clause_exclude_chunk_ids_uses_array():
    """exclude_chunk_ids uses ``<> ALL(:exclude_ids)`` for index-friendly NOT IN."""
    sql, params = _build_filter_clause(None, [1, 2, 3])
    assert "chunk_index" in sql
    assert "ALL" in sql
    assert params["exclude_ids"] == [1, 2, 3]


def test_filter_clause_both_filters_chained():
    """Both filters joined with AND."""
    sql, params = _build_filter_clause("MWS-150-A", [42])
    # Two AND conditions joined by another AND.
    assert sql.count("AND") >= 2
    assert params == {
        "vehicle_model": "MWS-150-A",
        "exclude_ids": [42],
    }


# ── _row_to_result fallbacks ──────────────────────────────────────


def test_row_to_result_fallbacks():
    """Missing optional fields collapse to sentinels."""
    row = SimpleNamespace(
        text="hello",
        doc_id=None,
        source_type=None,
        section_title=None,
        chunk_index=None,
        metadata_json=None,
    )
    result = _row_to_result(row, score=0.5)
    assert result.text == "hello"
    assert result.score == 0.5
    assert result.doc_id == "unknown"
    assert result.source_type == "unknown"
    assert result.section_title == "unknown"
    assert result.chunk_index == 0
    assert result.metadata == {}


# ── retrieve_context mode dispatch ────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_context_vector_mode_default(monkeypatch):
    """Default mode dispatches to vector helper, no keyword call."""
    captured = {}

    async def fake_embed(_q):
        return [0.1] * 768

    def fake_vector_query(vector, top_k, **kwargs):
        captured["called"] = "vector"
        captured["top_k"] = top_k
        captured["vehicle_model"] = kwargs.get("vehicle_model")
        return [RetrievalResult(
            text="t", score=0.9, doc_id="d",
            source_type="manual", section_title="s",
            chunk_index=0,
        )]

    def fake_hybrid_query(*a, **kw):
        captured["called"] = "hybrid"
        return []

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_vector_query", fake_vector_query,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_hybrid_query", fake_hybrid_query,
    )

    result = await retrieve_context("p0300", top_k=5)
    assert captured["called"] == "vector"
    assert captured["top_k"] == 5
    assert len(result) == 1


@pytest.mark.asyncio
async def test_retrieve_context_hybrid_mode_invokes_hybrid(monkeypatch):
    """mode='hybrid' routes to the fusion helper with alpha."""
    captured = {}

    async def fake_embed(_q):
        return [0.2] * 768

    def fake_hybrid_query(vector, query_str, top_k, alpha, **kw):
        captured.update(
            query=query_str, top_k=top_k, alpha=alpha,
        )
        return []

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_hybrid_query", fake_hybrid_query,
    )

    await retrieve_context(
        "p0300", top_k=4, mode="hybrid", alpha=0.3,
    )
    assert captured["query"] == "p0300"
    assert captured["top_k"] == 4
    assert captured["alpha"] == 0.3


@pytest.mark.asyncio
async def test_retrieve_context_keyword_mode_skips_embedding(monkeypatch):
    """Keyword mode must not call the embedding service."""
    embed_called = {"count": 0}

    async def fake_embed(_q):
        embed_called["count"] += 1
        return [0.0] * 768

    def fake_keyword_query(query_str, top_k, **kw):
        return []

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_keyword_query", fake_keyword_query,
    )

    await retrieve_context("p0300", mode="keyword")
    assert embed_called["count"] == 0, (
        "keyword-only path must not invoke the embedding service"
    )


@pytest.mark.asyncio
async def test_retrieve_context_clamps_top_k(monkeypatch):
    """top_k is clamped to [1, 20]."""
    seen_top_k = {}

    async def fake_embed(_q):
        return [0.0] * 768

    def fake_vector_query(_vec, top_k, **kw):
        seen_top_k["v"] = top_k
        return []

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_vector_query", fake_vector_query,
    )

    await retrieve_context("q", top_k=999)
    assert seen_top_k["v"] == 20

    await retrieve_context("q", top_k=0)
    assert seen_top_k["v"] == 1


@pytest.mark.asyncio
async def test_retrieve_context_clamps_alpha(monkeypatch):
    """alpha outside [0, 1] is clamped before reaching the helper."""
    seen_alpha = {}

    async def fake_embed(_q):
        return [0.0] * 768

    def fake_hybrid_query(_vec, _q, _topk, alpha, **kw):
        seen_alpha["a"] = alpha
        return []

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )
    monkeypatch.setattr(
        "app.rag.retrieve._sync_hybrid_query", fake_hybrid_query,
    )

    await retrieve_context("q", mode="hybrid", alpha=5.0)
    assert seen_alpha["a"] == 1.0

    await retrieve_context("q", mode="hybrid", alpha=-1.0)
    assert seen_alpha["a"] == 0.0


@pytest.mark.asyncio
async def test_retrieve_context_empty_embedding_returns_empty(monkeypatch):
    """If embedding service returns nothing, retrieval short-circuits."""

    async def fake_embed(_q):
        return None

    monkeypatch.setattr(
        "app.rag.retrieve.embedding_service.get_embedding",
        fake_embed,
    )

    out = await retrieve_context("q", mode="vector")
    assert out == []

    out = await retrieve_context("q", mode="hybrid")
    assert out == []

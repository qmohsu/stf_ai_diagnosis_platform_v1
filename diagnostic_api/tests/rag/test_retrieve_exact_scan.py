"""Regression tests for filtered-retrieval exact-scan fallback.

APP-62 / Issue #156: with >=2 manuals sharing the HNSW index, a hard
``vehicle_model`` filter starved production retrieval to 0 rows —
HNSW selects approximate nearest neighbours FIRST and filters AFTER,
so the larger manual crowds the filtered one out of the candidate
pool entirely (proven at the pgvector 0.7.4 maximum
``hnsw.ef_search=1000``).  The fix forces an exact sequential scan
(``SET LOCAL enable_indexscan = off``) for the transaction whenever
the filter is set, while unfiltered queries keep the HNSW path.

No live Postgres in the unit lane, so these tests mock the session
and assert on the emitted planner overrides:

- filtered vector / hybrid queries emit the ``SET LOCAL`` overrides
  BEFORE the retrieval statement;
- unfiltered queries emit no overrides (HNSW path preserved);
- the path helper labels the choice for structured logging;
- filtered rows still map to ``RetrievalResult`` (regression shape
  for "filtered retrieval returns the in-vehicle manual's rows").

The live proof (TRICITY155 filter returns >0 rows with the Corolla
E11 manual co-ingested) runs on the server post-merge — the eval
adapter ``rag_runner._sync_exact_vector_query`` already validated
the identical mechanism against real Postgres (HARNESS-23).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock, patch

from app.rag.retrieve import (
    _force_exact_scan_for_filter,
    _sync_hybrid_query,
    _sync_vector_query,
)


def _mock_session(rows: List[Any] | None = None) -> MagicMock:
    """Build a chainable mock ``Session`` for both query styles.

    Args:
        rows: Rows returned by both the ORM ``.all()`` (vector path)
            and raw-SQL ``.fetchall()`` (hybrid path).

    Returns:
        MagicMock standing in for ``SessionLocal()``.
    """
    rows = rows if rows is not None else []
    db = MagicMock()
    orm_query = MagicMock()
    for chained in ("filter", "order_by", "limit"):
        getattr(orm_query, chained).return_value = orm_query
    orm_query.all.return_value = rows
    db.query.return_value = orm_query
    db.execute.return_value.fetchall.return_value = rows
    return db


def _set_local_statements(db: MagicMock) -> List[str]:
    """Extract ``SET LOCAL`` statements emitted on the mock session."""
    return [
        str(call.args[0])
        for call in db.execute.call_args_list
        if "SET LOCAL" in str(call.args[0])
    ]


def _fake_row(distance: float = 0.2) -> SimpleNamespace:
    """A row shaped like the vector-path SELECT output."""
    return SimpleNamespace(
        text="rear brake torque spec",
        doc_id="tricity155-manual",
        source_type="manual",
        section_title="Rear Brake",
        chunk_index=7,
        metadata_json={},
        distance=distance,
    )


# ── _force_exact_scan_for_filter ──────────────────────────────────


def test_force_exact_scan_disables_index_scans_when_filtered():
    """A hard vehicle_model filter must switch the planner to exact."""
    db = MagicMock()

    path = _force_exact_scan_for_filter(db, "TRICITY155")

    assert path == "exact"
    stmts = _set_local_statements(db)
    assert any("enable_indexscan = off" in s for s in stmts)
    assert any("enable_bitmapscan = off" in s for s in stmts)


def test_force_exact_scan_noop_without_filter():
    """No filter → HNSW path untouched, no planner overrides."""
    db = MagicMock()

    path = _force_exact_scan_for_filter(db, None)

    assert path == "hnsw"
    db.execute.assert_not_called()


def test_force_exact_scan_noop_on_empty_string():
    """Empty-string filter is falsy → treated as unfiltered."""
    db = MagicMock()

    path = _force_exact_scan_for_filter(db, "")

    assert path == "hnsw"
    db.execute.assert_not_called()


# ── _sync_vector_query scan-path selection ────────────────────────


def test_vector_query_filtered_forces_exact_scan():
    """vehicle_model filter emits SET LOCAL overrides on the txn."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_vector_query(
            [0.1] * 768, 5, vehicle_model="TRICITY155",
        )

    stmts = _set_local_statements(db)
    assert any("enable_indexscan = off" in s for s in stmts)
    assert any("enable_bitmapscan = off" in s for s in stmts)


def test_vector_query_unfiltered_keeps_hnsw():
    """No filter → no planner overrides (HNSW path preserved)."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_vector_query([0.1] * 768, 5)

    assert _set_local_statements(db) == []


def test_vector_query_exact_scan_set_before_select():
    """Overrides must land before the retrieval statement runs."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_vector_query(
            [0.1] * 768, 5, vehicle_model="TRICITY155",
        )

    call_names = [name for name, _args, _kw in db.method_calls]
    assert "execute" in call_names and "query" in call_names
    assert call_names.index("execute") < call_names.index("query"), (
        "SET LOCAL must run before the ORM SELECT in the same txn"
    )


def test_vector_query_filtered_returns_rows():
    """Regression shape: the filtered query maps rows to results.

    The 0-rows bug meant a filtered query returned nothing at all;
    with the exact scan the filtered manual's rows come back with
    cosine similarity scores.
    """
    db = _mock_session(rows=[_fake_row(distance=0.2)])
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        results = _sync_vector_query(
            [0.1] * 768, 5, vehicle_model="TRICITY155",
        )

    assert len(results) == 1
    assert results[0].doc_id == "tricity155-manual"
    assert abs(results[0].score - 0.8) < 1e-9


def test_vector_query_logs_scan_path(caplog):
    """Structured log records which scan path served the query."""
    db = _mock_session()
    with caplog.at_level(logging.INFO, logger="app.rag.retrieve"):
        with patch(
            "app.rag.retrieve.SessionLocal", return_value=db,
        ):
            _sync_vector_query(
                [0.1] * 768, 5, vehicle_model="TRICITY155",
            )
            _sync_vector_query([0.1] * 768, 5)

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("scan_path=exact" in m for m in messages)
    assert any("scan_path=hnsw" in m for m in messages)


# ── _sync_hybrid_query scan-path selection ────────────────────────


def test_hybrid_query_filtered_forces_exact_scan():
    """Hybrid's semantic CTE has the same starvation → same fix."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_hybrid_query(
            [0.1] * 768, "brake torque", 5, 0.5,
            vehicle_model="TRICITY155",
        )

    stmts = _set_local_statements(db)
    assert any("enable_indexscan = off" in s for s in stmts)
    assert any("enable_bitmapscan = off" in s for s in stmts)


def test_hybrid_query_unfiltered_keeps_hnsw():
    """Unfiltered hybrid keeps the HNSW path (no overrides)."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_hybrid_query([0.1] * 768, "brake torque", 5, 0.5)

    assert _set_local_statements(db) == []


def test_hybrid_query_exact_scan_set_before_select():
    """Overrides precede the fused SELECT within the transaction."""
    db = _mock_session()
    with patch("app.rag.retrieve.SessionLocal", return_value=db):
        _sync_hybrid_query(
            [0.1] * 768, "brake torque", 5, 0.5,
            vehicle_model="TRICITY155",
        )

    executed = [str(c.args[0]) for c in db.execute.call_args_list]
    set_local_idx = [
        i for i, s in enumerate(executed) if "SET LOCAL" in s
    ]
    select_idx = [
        i for i, s in enumerate(executed) if "semantic" in s
    ]
    assert set_local_idx and select_idx
    assert max(set_local_idx) < min(select_idx), (
        "SET LOCAL must run before the hybrid SELECT in the same txn"
    )

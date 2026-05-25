"""Add tsvector column + GIN index to rag_chunks for hybrid search.

APP-56 / Issue #18.

Pure pgvector cosine similarity misses exact-match queries — a
technician searching for a specific DTC like ``P0300`` can have
semantically similar but wrong chunks rank above the exact match.
This migration adds the schema half of a hybrid keyword + vector
retrieval path; the retrieval-layer half lives in
``app/rag/retrieve.py``.

Schema change:

- ``rag_chunks.tsv`` — ``tsvector`` column, ``GENERATED ALWAYS AS
  (to_tsvector('english', text)) STORED``.  Populated synchronously
  by Postgres for every existing row during the ALTER, and on every
  subsequent insert/update of ``text``.  The application never writes
  to this column.
- ``ix_rag_chunks_tsv`` — GIN index over ``tsv`` for fast keyword
  lookup via ``tsv @@ plainto_tsquery(...)``.

Operational notes:

- At ~2.2K chunks the ALTER backfills sub-second and the GIN build is
  similarly fast.  If the corpus grows past ~100K rows, a future
  migration should use ``CREATE INDEX CONCURRENTLY`` and a controlled
  backfill window — neither helper is necessary at current scale.
- Disk overhead is negligible (~hundreds of bytes per chunk).
- The pre-existing HNSW index on ``embedding`` is unaffected; the two
  indexes coexist with no shared locks beyond the table itself.
- Downgrade is lossless because ``tsv`` is derived from ``text``.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-24 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add generated tsvector column + GIN index to rag_chunks."""
    op.execute(
        "ALTER TABLE rag_chunks "
        "ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS "
        "(to_tsvector('english', text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_rag_chunks_tsv "
        "ON rag_chunks USING GIN (tsv)"
    )


def downgrade() -> None:
    """Drop the GIN index and tsvector column."""
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_tsv")
    op.execute("ALTER TABLE rag_chunks DROP COLUMN IF EXISTS tsv")

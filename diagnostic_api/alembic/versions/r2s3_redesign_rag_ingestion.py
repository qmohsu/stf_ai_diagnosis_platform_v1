"""Redesign RAG ingestion: link rag_chunks to manuals, expand status enum.

Adds ``manual_id`` FK on ``rag_chunks`` so reingestion can cascade-delete
chunks for a single manual.  Locks ``source_type`` to ``'manual'`` for now
(future ingestion sources will require a follow-up migration that relaxes
the constraint).  Expands ``manuals.status`` enum to include the new
``chunking`` and ``embedding`` stages for finer UI observability.

Safe to make ``manual_id`` NOT NULL from the start because ``rag_chunks``
is empty in production at the time of this migration (verified
2026-05-02).  Any pre-existing rows would block the migration and require
a backfill step.

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add manual_id FK + source_type CHECK + expand status enum."""
    # 1. rag_chunks.manual_id (NOT NULL, FK with cascade).
    op.add_column(
        "rag_chunks",
        sa.Column(
            "manual_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "manuals.id", ondelete="CASCADE",
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_rag_chunks_manual_id",
        "rag_chunks",
        ["manual_id"],
    )

    # 2. rag_chunks.source_type CHECK (locked to 'manual').
    op.create_check_constraint(
        "ck_rag_chunk_source_type",
        "rag_chunks",
        "source_type = 'manual'",
    )

    # 3. manuals.status: drop old CHECK, add new one with 4 stages.
    op.drop_constraint(
        "ck_manual_status", "manuals", type_="check",
    )
    op.create_check_constraint(
        "ck_manual_status",
        "manuals",
        "status IN ("
        "'uploading', 'converting', 'chunking', "
        "'embedding', 'ingested', 'failed'"
        ")",
    )


def downgrade() -> None:
    """Reverse: shrink status enum, drop CHECK + manual_id."""
    # Revert status CHECK to original 4 values.
    op.drop_constraint(
        "ck_manual_status", "manuals", type_="check",
    )
    op.create_check_constraint(
        "ck_manual_status",
        "manuals",
        "status IN ("
        "'uploading', 'converting', 'ingested', 'failed'"
        ")",
    )

    # Drop source_type CHECK.
    op.drop_constraint(
        "ck_rag_chunk_source_type",
        "rag_chunks",
        type_="check",
    )

    # Drop manual_id FK + index + column.
    op.drop_index(
        "ix_rag_chunks_manual_id", table_name="rag_chunks",
    )
    op.drop_column("rag_chunks", "manual_id")

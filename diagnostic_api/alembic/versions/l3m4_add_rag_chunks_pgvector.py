"""Add rag_chunks table with pgvector embedding column.

Replaces the Weaviate KnowledgeChunk collection with a PostgreSQL
table using the pgvector extension for vector similarity search.

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-03-21 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "l3m4n5o6p7q8"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create pgvector extension and rag_chunks table."""
    # Enable pgvector extension (idempotent, requires superuser
    # privileges — the init script also creates it as a safety net).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "rag_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "doc_id", sa.String(255), nullable=False,
            index=True,
        ),
        sa.Column(
            "source_type", sa.String(50), nullable=False,
        ),
        sa.Column(
            "section_title", sa.String(500), nullable=True,
        ),
        sa.Column(
            "vehicle_model", sa.String(100), nullable=True,
            index=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column(
            "checksum", sa.String(64), nullable=False,
            unique=True, index=True,
        ),
        sa.Column(
            "metadata_json",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "embedding", Vector(768), nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
        ),
    )

    # HNSW index for cosine distance — incrementally buildable,
    # works on empty tables (unlike IVFFlat which needs training).
    op.execute(
        "CREATE INDEX ix_rag_chunks_embedding "
        "ON rag_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """Drop rag_chunks table."""
    op.drop_table("rag_chunks")

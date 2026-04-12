"""Add manuals table for PDF upload lifecycle.

Creates the ``manuals`` table for tracking uploaded service
manuals through the conversion and RAG ingestion pipeline
(Issue #70 / HARNESS-10).

Revision ID: q1r2s3t4u5v6
Revises: p9q0r1s2t3u4
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "q1r2s3t4u5v6"
down_revision = "p9q0r1s2t3u4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create manuals table with indexes and CHECK."""
    op.create_table(
        "manuals",
        sa.Column(
            "id", UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "filename", sa.String(500), nullable=False,
        ),
        sa.Column(
            "file_hash", sa.String(64),
            nullable=False, unique=True,
        ),
        sa.Column(
            "vehicle_model", sa.String(100),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(20),
            nullable=False,
            server_default="uploading",
        ),
        sa.Column(
            "file_size_bytes", sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "page_count", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "section_count", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "language", sa.String(20), nullable=True,
        ),
        sa.Column(
            "converter", sa.String(100), nullable=True,
        ),
        sa.Column(
            "error_message", sa.Text(), nullable=True,
        ),
        sa.Column(
            "md_file_path", sa.String(500),
            nullable=True,
        ),
        sa.Column(
            "pdf_file_path", sa.String(500),
            nullable=True,
        ),
        sa.Column(
            "chunk_count", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'uploading', 'converting', "
            "'ingested', 'failed')",
            name="ck_manual_status",
        ),
    )

    op.create_index(
        "ix_manuals_user_id", "manuals", ["user_id"],
    )
    op.create_index(
        "ix_manuals_file_hash", "manuals", ["file_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Drop manuals table and indexes."""
    op.drop_index(
        "ix_manuals_file_hash", table_name="manuals",
    )
    op.drop_index(
        "ix_manuals_user_id", table_name="manuals",
    )
    op.drop_table("manuals")

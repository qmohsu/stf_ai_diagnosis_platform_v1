"""Add per-page progress columns to manuals.

Adds ``pages_processed`` and ``pages_total`` to ``manuals`` so the UI
can display per-page progress while marker-pdf is converting a large
PDF.  Both columns are nullable so existing rows are unaffected and
older converters that don't emit progress files continue to work.

The existing ``page_count`` column remains the authoritative final
count from marker's ``result.json``; ``pages_total`` is the running
estimate during conversion.  At successful completion the two
should agree.

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pages_processed and pages_total columns."""
    op.add_column(
        "manuals",
        sa.Column(
            "pages_processed", sa.Integer(), nullable=True,
        ),
    )
    op.add_column(
        "manuals",
        sa.Column(
            "pages_total", sa.Integer(), nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the per-page progress columns."""
    op.drop_column("manuals", "pages_total")
    op.drop_column("manuals", "pages_processed")

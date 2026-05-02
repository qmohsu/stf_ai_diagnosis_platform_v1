"""Add Manual.warnings JSONB for ingestion-quality observability.

When marker-pdf's LLM-assisted processors hit a malformed-JSON
response (or any other recoverable LLM error), marker silently
falls back to non-LLM extraction for that page.  Without
visibility, the user sees ``status='ingested'`` and assumes high
quality; in reality, page N's table may have been extracted by
the layout model alone.

This migration adds a nullable ``manuals.warnings`` JSONB column.
The marker-worker captures `LLM did not return a valid response`
log lines and writes a structured warnings JSON to the queue
directory, which the API then persists to this column.

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the warnings JSONB column."""
    op.add_column(
        "manuals",
        sa.Column(
            "warnings", JSONB(), nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the warnings column."""
    op.drop_column("manuals", "warnings")

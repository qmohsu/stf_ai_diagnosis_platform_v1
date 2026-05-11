"""Add Q+A snapshot columns to golden_reviews for HARNESS-17 Phase 2.

Phase 2 of HARNESS-17 (GitHub Issue #82) needs reviews to be
self-contained: each review carries a frozen copy of the question
and answer that was graded.  Without this, a future edit to a
golden entry (deferred Phase 3 feature) would silently rewrite
the context that historical reviews refer to — reviewer X gave 5
stars to "version A" but the displayed text is now "version B",
which is a data-integrity bug waiting to happen.

Columns:

- ``snapshot_question_en``     — English question text at submit time
- ``snapshot_question_zh``     — Chinese question text at submit time
- ``snapshot_summary_en``      — English proposed answer at submit time
- ``snapshot_summary_zh``      — Chinese proposed answer at submit time
- ``snapshot_citations``       — JSONB list of {manual_id, slug, quote}

All nullable to keep the migration trivially backwards-compatible
with the one pre-existing review row (which has no snapshot
because it was submitted before snapshots existed).

The submit-review endpoint will populate these at write time on
all new reviews going forward.  We do NOT backfill the existing
row in this migration — the dashboard will treat NULL snapshot
columns as "snapshot not available, falling back to live entry"
which gives the right behaviour for that one legacy row.

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add four nullable snapshot columns to golden_reviews."""
    op.add_column(
        "golden_reviews",
        sa.Column(
            "snapshot_question_en", sa.Text(), nullable=True,
        ),
    )
    op.add_column(
        "golden_reviews",
        sa.Column(
            "snapshot_question_zh", sa.Text(), nullable=True,
        ),
    )
    op.add_column(
        "golden_reviews",
        sa.Column(
            "snapshot_summary_en", sa.Text(), nullable=True,
        ),
    )
    op.add_column(
        "golden_reviews",
        sa.Column(
            "snapshot_summary_zh", sa.Text(), nullable=True,
        ),
    )
    op.add_column(
        "golden_reviews",
        sa.Column(
            "snapshot_citations", JSONB(), nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the snapshot columns."""
    op.drop_column("golden_reviews", "snapshot_citations")
    op.drop_column("golden_reviews", "snapshot_summary_zh")
    op.drop_column("golden_reviews", "snapshot_summary_en")
    op.drop_column("golden_reviews", "snapshot_question_zh")
    op.drop_column("golden_reviews", "snapshot_question_en")

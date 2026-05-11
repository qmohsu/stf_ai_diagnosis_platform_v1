"""Drop golden_reviews unique-per-(entry, reviewer) constraint.

HARNESS-17 Phase 2 follow-up (GitHub Issue #82): the review
model shifts from "one review per (entry, reviewer)" upsert to
append-only.  Each submit creates a new row; reviewers may post
multiple grades on the same entry over time; the dashboard
overview surfaces the most-recent grade across all reviewers.

The unique constraint that enforced the old behaviour
(``uq_golden_review_entry_reviewer``) is dropped here so the
``submit_review`` endpoint can INSERT freely.  No data changes:
existing rows remain valid under the new schema.

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-05-10
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the unique (golden_entry_id, reviewer_id) constraint."""
    op.drop_constraint(
        "uq_golden_review_entry_reviewer",
        "golden_reviews",
        type_="unique",
    )


def downgrade() -> None:
    """Re-create the unique constraint.

    NOTE: downgrade will fail if any (entry, reviewer) pair has
    more than one row.  Operators must dedupe first if rolling
    back is required.
    """
    op.create_unique_constraint(
        "uq_golden_review_entry_reviewer",
        "golden_reviews",
        ["golden_entry_id", "reviewer_id"],
    )

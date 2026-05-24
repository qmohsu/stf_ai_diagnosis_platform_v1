"""Add ``tier`` column to ``golden_entries`` for HARNESS-20.

HARNESS-20 (GitHub Issue #90) introduces a two-tier corpus for
golden Q&A entries.  The existing
``tests/harness/evals/golden/v2/mws150a.jsonl`` becomes the
**candidate** tier — mutable, dashboard-graded, still being
shaped.  A new ``tests/harness/evals/golden/v2/locked/`` tier
holds entries that an expert reviewer has accepted (≥4★,
``status='accept'``) and that have been explicitly promoted by
``scripts/promote_golden.py``.  The eval harness reads only the
locked tier, so a typo fix to a candidate's ``must_contain``
can never retroactively re-score an entry the expert has
already graded.

This migration adds the ``tier`` column so ``golden_sync.py``
can tag each row with the file-system tier it came from, and the
dashboard listing API can surface the tier to the UI (the UI
itself stays unchanged in this revision; lockability comes from
``promote_golden.py``, not from a UI button).

Schema:

- ``tier`` ``VARCHAR(20)`` ``NOT NULL`` ``DEFAULT 'candidate'``
- ``CHECK (tier IN ('candidate', 'locked'))``
- Indexed for the listing filter

Existing rows are backfilled with the default ``'candidate'``,
which is correct because no entries have been promoted yet at
the time this migration runs.

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the ``tier`` column with default + check + index."""
    op.add_column(
        "golden_entries",
        sa.Column(
            "tier",
            sa.String(20),
            nullable=False,
            server_default="candidate",
        ),
    )
    op.create_check_constraint(
        "ck_golden_entry_tier",
        "golden_entries",
        "tier IN ('candidate', 'locked')",
    )
    op.create_index(
        "ix_golden_entries_tier",
        "golden_entries",
        ["tier"],
    )


def downgrade() -> None:
    """Drop the index, check, and column in reverse order."""
    op.drop_index(
        "ix_golden_entries_tier",
        table_name="golden_entries",
    )
    op.drop_constraint(
        "ck_golden_entry_tier",
        "golden_entries",
        type_="check",
    )
    op.drop_column("golden_entries", "tier")

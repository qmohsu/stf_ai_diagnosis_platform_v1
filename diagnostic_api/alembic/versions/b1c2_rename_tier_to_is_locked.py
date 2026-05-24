"""Replace ``tier`` column with ``is_locked`` boolean on
``golden_entries`` (HARNESS-20 follow-up).

The `tier` column added in `z0a1b2c3d4e5` was conceptually wrong:
it tried to record "which file this row came from" but
`GoldenEntry.id` is a single primary key shared between the
candidate and locked tiers (the locked file is a verbatim copy
of the candidate line — that's the whole point of the
promotion mechanism).  So the two-tier sync walk produced two
upserts on the same `id`, and whichever ran last overwrote the
other's `tier` value.  Post-deploy verification showed all 30
rows ended up with `tier='candidate'` regardless of actual
lock state.

The corrected semantics: each `golden_entries` row holds the
**candidate** content (mutable; the dashboard reflects this),
and a new `is_locked` flag means "this entry id also exists in
the locked tier" — i.e. "has been promoted, eval-locked".  The
locked tier's *content* stays on the filesystem as the eval
source of truth; the DB doesn't try to mirror it.

Migration:

- Add ``is_locked BOOLEAN NOT NULL DEFAULT FALSE`` (all
  existing rows get FALSE; the next `golden_sync` run on
  startup re-populates correctly from the filesystem two-pass
  walk introduced in the same commit as this migration).
- Drop the CHECK constraint `ck_golden_entry_tier`.
- Drop the index `ix_golden_entries_tier`.
- Drop the `tier` column.

No data is preserved across the rename because the prior
column was uniformly wrong — the next sync run rebuilds the
correct state from the filesystem.

Revision ID: b1c2d3e4f5a6
Revises: z0a1b2c3d4e5
Create Date: 2026-05-24

Note: an earlier draft of this migration mistakenly used revision id
``a1b2c3d4e5f6`` which collided with the pre-HARNESS-20 migration of
the same id (``a1b2c3d4e5f6_add_raw_and_parsed_columns.py``).
Alembic refused to resolve the chain, so the migration silently
never ran and ``is_locked`` was missing on production until the
collision was fixed by re-issuing this migration as
``b1c2d3e4f5a6``.  See the hotfix that landed for the trace.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ``is_locked`` boolean; drop the broken ``tier``."""
    op.add_column(
        "golden_entries",
        sa.Column(
            "is_locked",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_golden_entries_is_locked",
        "golden_entries",
        ["is_locked"],
    )
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


def downgrade() -> None:
    """Restore ``tier`` column (defaulted to ``candidate``);
    drop ``is_locked``.

    The restored ``tier`` will hold ``'candidate'`` for every
    row — same wrong state the buggy column reached on its
    own.  Restoring useful tier data is not possible without
    re-running the (now-removed) buggy sync pathway, so
    downgrades are deliberately lossy.  Use only to roll back
    schema, not state.
    """
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
    op.drop_index(
        "ix_golden_entries_is_locked",
        table_name="golden_entries",
    )
    op.drop_column("golden_entries", "is_locked")

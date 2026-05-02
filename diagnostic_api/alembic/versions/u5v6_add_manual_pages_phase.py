"""Add Manual.pages_phase for stage-aware progress display.

Marker-pdf runs multiple sequential pipeline stages (layout,
OCR, recognition, LLM section header, LLM page correction,
table rewrite), each with its own ``tqdm`` bar over its own
unit count.  Without a stage label the UI shows ``283/434`` and
then jumps to ``526/555`` as marker moves between stages, which
looks like a regression to the user.

This migration adds ``manuals.pages_phase`` so the worker can
expose the current stage's ``tqdm.desc`` string and the UI can
render ``OCR 283/434`` instead of the bare counter.

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the pages_phase varchar column."""
    op.add_column(
        "manuals",
        sa.Column(
            "pages_phase", sa.String(length=50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the pages_phase column."""
    op.drop_column("manuals", "pages_phase")

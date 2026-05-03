"""Widen rag_chunks.section_title from VARCHAR(500) to TEXT.

Marker-pdf emits markdown headings that embed HTML page-anchor
spans (``<span id="page-281-1"></span><span id="page-281-0"></span>
恆溫器``) for the manual viewer.  On long Chinese service manuals
with many anchors per heading the resulting ``section_title``
can exceed 500 characters, blowing up the INSERT batch with
``StringDataRightTruncation``.

We accept long titles (TEXT has no practical cap) rather than
truncating them — long titles carry useful retrieval signal and
the chunker still applies a defensive cap of 2000 characters at
write time as a belt-and-braces guard against pathological
inputs.

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Convert section_title to TEXT."""
    op.alter_column(
        "rag_chunks",
        "section_title",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Revert section_title to VARCHAR(500).

    Will fail if any row has a title longer than 500 chars; you
    must clean the data before downgrading.
    """
    op.alter_column(
        "rag_chunks",
        "section_title",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=True,
    )

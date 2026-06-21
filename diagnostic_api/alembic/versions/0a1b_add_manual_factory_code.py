"""Add factory_code alias to manuals.

APP-61. Some service manuals are referred to by a factory / manual
code (e.g. Yamaha's ``MWS150-A`` for the Tricity 155) rather than the
marketing model name stored in ``vehicle_model`` (``TRICITY155``).
Storing the code as an optional alias lets the harness match a question
phrased by either identifier to the same manual, instead of the honest
agent refusing because "TRICITY155" != "MWS-150-A".

Nullable — most manuals have no distinct factory code. Backfills the
one known case (the Yamaha Tricity 155 manual) so the existing corpus
resolves both names without a re-upload.

Revision ID: 0a1b2c3d4e5f
Revises: a7b8c9d0e1f2
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0a1b2c3d4e5f"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manuals",
        sa.Column("factory_code", sa.String(100), nullable=True),
    )
    # Backfill the one known manual whose cover/factory code differs
    # from its marketing model name.  Guarded so it is a no-op on any
    # environment that lacks the row or already set the value.
    op.execute(
        "UPDATE manuals SET factory_code = 'MWS150-A' "
        "WHERE manufacturer = 'Yamaha' "
        "AND vehicle_model = 'TRICITY155' "
        "AND factory_code IS NULL"
    )


def downgrade() -> None:
    op.drop_column("manuals", "factory_code")

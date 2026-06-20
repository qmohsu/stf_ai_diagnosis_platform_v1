"""Add manufacturer + vehicle_model to obd_analysis_sessions.

APP-60 (follow-up to the P00AF Hiace finding, #135). A vehicle model
cannot be derived from an OBD log, so the uploader must state it; these
columns hold the make/model supplied at upload so the agent can ground
on the real vehicle and match the right service manual.

Required at the API layer (422 on blank); nullable in the DB so the
(potentially many) historical sessions stay valid — no backfill, no
NOT NULL.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "obd_analysis_sessions",
        sa.Column("manufacturer", sa.String(100), nullable=True),
    )
    op.add_column(
        "obd_analysis_sessions",
        sa.Column("vehicle_model", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("obd_analysis_sessions", "vehicle_model")
    op.drop_column("obd_analysis_sessions", "manufacturer")

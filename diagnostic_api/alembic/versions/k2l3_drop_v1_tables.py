"""Drop unused V1 tables (vehicles, diagnostic_sessions,
diagnostic_feedback).

These tables were part of the original V1 API layer which has been
fully removed.  The corresponding SQLAlchemy models were deleted from
models_db.py in the V1 cleanup refactor.

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-03-09 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, None] = "j1k2l3m4n5o6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop V1 tables in FK-safe order."""
    op.drop_table("diagnostic_feedback")
    op.drop_table("diagnostic_sessions")
    op.drop_table("vehicles")


def downgrade() -> None:
    """Recreate V1 tables (empty, for rollback only)."""
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vehicle_id", sa.String(50), unique=True, nullable=False,
        ),
        sa.Column("make", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "diagnostic_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vehicle_id",
            sa.String(50),
            sa.ForeignKey("vehicles.vehicle_id"),
            nullable=False,
        ),
        sa.Column("session_type", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "diagnostic_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_sessions.id"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

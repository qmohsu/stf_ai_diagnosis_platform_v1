"""Add authentication: modify users table, add user_id to
obd_analysis_sessions with unique constraint.

Clean-slate migration: truncates all session, feedback, and
history data before applying schema changes.

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-03-08 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, None] = "i0j1k2l3m4n5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply auth schema changes with clean-slate data wipe."""
    # 1. Truncate all child tables in FK-safe order
    op.execute("TRUNCATE TABLE diagnosis_history CASCADE")
    op.execute("TRUNCATE TABLE obd_summary_feedback CASCADE")
    op.execute("TRUNCATE TABLE obd_detailed_feedback CASCADE")
    op.execute("TRUNCATE TABLE obd_rag_feedback CASCADE")
    op.execute("TRUNCATE TABLE obd_ai_diagnosis_feedback CASCADE")
    op.execute(
        "TRUNCATE TABLE obd_premium_diagnosis_feedback CASCADE"
    )
    op.execute("TRUNCATE TABLE obd_analysis_sessions CASCADE")
    op.execute("TRUNCATE TABLE users CASCADE")

    # 2. Modify users table: drop email, add hashed_password
    op.drop_index("ix_users_email", table_name="users")
    op.drop_column("users", "email")
    op.add_column(
        "users",
        sa.Column(
            "hashed_password",
            sa.String(255),
            nullable=False,
            server_default="PLACEHOLDER",
        ),
    )
    op.alter_column(
        "users", "hashed_password", server_default=None,
    )

    # 3. Add user_id FK to obd_analysis_sessions
    op.add_column(
        "obd_analysis_sessions",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_obd_analysis_sessions_user_id",
        "obd_analysis_sessions",
        ["user_id"],
    )

    # 4. Add unique constraint (user_id, input_text_hash)
    op.create_unique_constraint(
        "uq_user_input_hash",
        "obd_analysis_sessions",
        ["user_id", "input_text_hash"],
    )


def downgrade() -> None:
    """Reverse auth schema changes."""
    # 1. Drop unique constraint and user_id column
    op.drop_constraint(
        "uq_user_input_hash",
        "obd_analysis_sessions",
        type_="unique",
    )
    op.drop_index(
        "ix_obd_analysis_sessions_user_id",
        table_name="obd_analysis_sessions",
    )
    op.drop_column("obd_analysis_sessions", "user_id")

    # 2. Reverse users table changes
    op.drop_column("users", "hashed_password")
    op.add_column(
        "users",
        sa.Column(
            "email",
            sa.String(255),
            nullable=False,
            unique=True,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])

"""Add harness_event_log table and update provider CHECK.

Creates the append-only ``harness_event_log`` table for agent
session event persistence (HARNESS-03) and extends the
``DiagnosisHistory.provider`` CHECK constraint to accept
``'agent'`` alongside ``'local'`` and ``'premium'``.

Revision ID: p9q0r1s2t3u4
Revises: o7p8q9r0s1t2
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = "p9q0r1s2t3u4"
down_revision = "o7p8q9r0s1t2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create harness_event_log table; widen provider CHECK."""
    # 1. Create harness_event_log table
    op.create_table(
        "harness_event_log",
        sa.Column(
            "id", UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id", UUID(as_uuid=True),
            sa.ForeignKey(
                "obd_analysis_sessions.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "event_type", sa.String(50), nullable=False,
        ),
        sa.Column(
            "iteration", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # 2. Indexes
    op.create_index(
        "ix_harness_event_log_session_id",
        "harness_event_log",
        ["session_id"],
    )
    op.create_index(
        "ix_harness_event_log_event_type",
        "harness_event_log",
        ["event_type"],
    )
    op.create_index(
        "ix_harness_event_session_time",
        "harness_event_log",
        ["session_id", "created_at"],
    )

    # 3. Update DiagnosisHistory provider CHECK constraint
    op.drop_constraint(
        "ck_diagnosis_history_provider",
        "diagnosis_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_diagnosis_history_provider",
        "diagnosis_history",
        "provider IN ('local', 'premium', 'agent')",
    )


def downgrade() -> None:
    """Drop harness_event_log table; restore provider CHECK."""
    # 1. Restore original provider CHECK constraint
    op.drop_constraint(
        "ck_diagnosis_history_provider",
        "diagnosis_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_diagnosis_history_provider",
        "diagnosis_history",
        "provider IN ('local', 'premium')",
    )

    # 2. Drop indexes and table
    op.drop_index(
        "ix_harness_event_session_time",
        table_name="harness_event_log",
    )
    op.drop_index(
        "ix_harness_event_log_event_type",
        table_name="harness_event_log",
    )
    op.drop_index(
        "ix_harness_event_log_session_id",
        table_name="harness_event_log",
    )
    op.drop_table("harness_event_log")

"""Add obd_agent_diagnosis_feedback table.

Adds a dedicated feedback table for the Agent AI diagnosis view,
mirroring obd_premium_diagnosis_feedback (mixin columns +
diagnosis_text snapshot + diagnosis_history_id FK + audio columns).
Keeping agent feedback in its own table — consistent with the
existing per-view feedback tables — lets agent-rated generations
stay separable from local/premium feedback for training-data
collection.  HARNESS-24, GitHub issue #127.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "obd_agent_diagnosis_feedback",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("obd_analysis_sessions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("is_helpful", sa.Boolean(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("diagnosis_text", sa.Text(), nullable=True),
        sa.Column(
            "diagnosis_history_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diagnosis_history.id"),
            nullable=True,
        ),
        sa.Column(
            "audio_file_path", sa.String(500), nullable=True,
        ),
        sa.Column(
            "audio_duration_seconds", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "audio_size_bytes", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_obd_agent_diagnosis_feedback_diagnosis_history_id",
        "obd_agent_diagnosis_feedback",
        ["diagnosis_history_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_obd_agent_diagnosis_feedback_diagnosis_history_id",
        table_name="obd_agent_diagnosis_feedback",
    )
    op.drop_table("obd_agent_diagnosis_feedback")

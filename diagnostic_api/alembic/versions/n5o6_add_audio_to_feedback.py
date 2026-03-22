"""Add audio recording columns to all feedback tables.

Adds audio_file_path, audio_duration_seconds, and audio_size_bytes
to each of the five OBD feedback tables via the shared mixin.

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "n5o6p7q8r9s0"
down_revision = "m4n5o6p7q8r9"
branch_labels = None
depends_on = None

_FEEDBACK_TABLES = [
    "obd_summary_feedback",
    "obd_detailed_feedback",
    "obd_rag_feedback",
    "obd_ai_diagnosis_feedback",
    "obd_premium_diagnosis_feedback",
]


def upgrade() -> None:
    """Add audio columns to all five feedback tables."""
    for table in _FEEDBACK_TABLES:
        op.add_column(
            table,
            sa.Column(
                "audio_file_path",
                sa.String(500),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "audio_duration_seconds",
                sa.Integer(),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "audio_size_bytes",
                sa.Integer(),
                nullable=True,
            ),
        )


def downgrade() -> None:
    """Remove audio columns from all five feedback tables."""
    for table in _FEEDBACK_TABLES:
        op.drop_column(table, "audio_size_bytes")
        op.drop_column(table, "audio_duration_seconds")
        op.drop_column(table, "audio_file_path")

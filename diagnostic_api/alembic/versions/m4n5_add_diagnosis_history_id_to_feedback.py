"""Add diagnosis_history_id FK to AI and premium feedback tables.

Links feedback rows to the specific diagnosis generation they refer
to, making feedback actionable across multiple regenerations.

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "m4n5o6p7q8r9"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- obd_ai_diagnosis_feedback ---
    op.add_column(
        "obd_ai_diagnosis_feedback",
        sa.Column(
            "diagnosis_history_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_ai_feedback_diagnosis_history",
        "obd_ai_diagnosis_feedback",
        "diagnosis_history",
        ["diagnosis_history_id"],
        ["id"],
    )
    op.create_index(
        "ix_obd_ai_diagnosis_feedback_diagnosis_history_id",
        "obd_ai_diagnosis_feedback",
        ["diagnosis_history_id"],
    )

    # --- obd_premium_diagnosis_feedback ---
    op.add_column(
        "obd_premium_diagnosis_feedback",
        sa.Column(
            "diagnosis_history_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_premium_feedback_diagnosis_history",
        "obd_premium_diagnosis_feedback",
        "diagnosis_history",
        ["diagnosis_history_id"],
        ["id"],
    )
    op.create_index(
        "ix_obd_premium_diagnosis_feedback_diagnosis_history_id",
        "obd_premium_diagnosis_feedback",
        ["diagnosis_history_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_obd_premium_diagnosis_feedback_diagnosis_history_id",
        table_name="obd_premium_diagnosis_feedback",
    )
    op.drop_constraint(
        "fk_premium_feedback_diagnosis_history",
        "obd_premium_diagnosis_feedback",
        type_="foreignkey",
    )
    op.drop_column(
        "obd_premium_diagnosis_feedback",
        "diagnosis_history_id",
    )

    op.drop_index(
        "ix_obd_ai_diagnosis_feedback_diagnosis_history_id",
        table_name="obd_ai_diagnosis_feedback",
    )
    op.drop_constraint(
        "fk_ai_feedback_diagnosis_history",
        "obd_ai_diagnosis_feedback",
        type_="foreignkey",
    )
    op.drop_column(
        "obd_ai_diagnosis_feedback",
        "diagnosis_history_id",
    )

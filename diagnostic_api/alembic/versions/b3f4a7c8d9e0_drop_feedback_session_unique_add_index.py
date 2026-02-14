"""drop unique constraint on feedback session_id, add index

Revision ID: b3f4a7c8d9e0
Revises: a1b2c3d4e5f6
Create Date: 2026-02-14 16:15:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3f4a7c8d9e0'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "obd_analysis_feedback_session_id_key",
        "obd_analysis_feedback",
        type_="unique",
    )
    op.create_index(
        "ix_obd_analysis_feedback_session_id",
        "obd_analysis_feedback",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_obd_analysis_feedback_session_id",
        table_name="obd_analysis_feedback",
    )
    op.create_unique_constraint(
        "obd_analysis_feedback_session_id_key",
        "obd_analysis_feedback",
        ["session_id"],
    )

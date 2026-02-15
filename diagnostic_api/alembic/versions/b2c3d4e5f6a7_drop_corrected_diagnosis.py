"""drop corrected_diagnosis from all feedback tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-15 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = [
    'obd_summary_feedback',
    'obd_detailed_feedback',
    'obd_rag_feedback',
    'obd_ai_diagnosis_feedback',
]


def upgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, 'corrected_diagnosis')


def downgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column('corrected_diagnosis', sa.Text(), nullable=True))

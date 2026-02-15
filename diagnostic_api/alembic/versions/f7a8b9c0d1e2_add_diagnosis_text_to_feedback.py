"""add diagnosis_text column to obd_ai_diagnosis_feedback

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-02-15 19:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'obd_ai_diagnosis_feedback',
        sa.Column('diagnosis_text', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('obd_ai_diagnosis_feedback', 'diagnosis_text')

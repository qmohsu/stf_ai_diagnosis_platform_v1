"""add retrieved_text column to obd_rag_feedback

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-02-15 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'obd_rag_feedback',
        sa.Column('retrieved_text', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('obd_rag_feedback', 'retrieved_text')

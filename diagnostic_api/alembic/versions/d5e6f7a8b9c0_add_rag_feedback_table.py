"""add obd_rag_feedback table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-02-15 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'obd_rag_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('obd_analysis_sessions.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_obd_rag_feedback_session_id',
                    'obd_rag_feedback', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_obd_rag_feedback_session_id', table_name='obd_rag_feedback')
    op.drop_table('obd_rag_feedback')

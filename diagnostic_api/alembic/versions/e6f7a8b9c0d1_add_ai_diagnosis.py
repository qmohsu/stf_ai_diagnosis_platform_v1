"""add diagnosis_text column and obd_ai_diagnosis_feedback table

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-02-15 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add diagnosis_text column to obd_analysis_sessions
    op.add_column(
        'obd_analysis_sessions',
        sa.Column('diagnosis_text', sa.Text(), nullable=True),
    )

    # Create obd_ai_diagnosis_feedback table
    op.create_table(
        'obd_ai_diagnosis_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('obd_analysis_sessions.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_obd_ai_diagnosis_feedback_session_id',
                    'obd_ai_diagnosis_feedback', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_obd_ai_diagnosis_feedback_session_id',
                  table_name='obd_ai_diagnosis_feedback')
    op.drop_table('obd_ai_diagnosis_feedback')
    op.drop_column('obd_analysis_sessions', 'diagnosis_text')

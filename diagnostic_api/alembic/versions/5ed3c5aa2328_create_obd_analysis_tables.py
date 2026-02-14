"""create_obd_analysis_tables

Revision ID: 5ed3c5aa2328
Revises: 68e7defefd58
Create Date: 2026-02-14 06:27:12.783964

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5ed3c5aa2328'
down_revision: Union[str, None] = '68e7defefd58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('obd_analysis_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('vehicle_id', sa.String(length=50), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('input_text_hash', sa.String(length=64), nullable=False),
    sa.Column('input_size_bytes', sa.Integer(), nullable=False),
    sa.Column('result_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_obd_analysis_sessions_input_text_hash'), 'obd_analysis_sessions', ['input_text_hash'], unique=False)
    op.create_index(op.f('ix_obd_analysis_sessions_status'), 'obd_analysis_sessions', ['status'], unique=False)
    op.create_index(op.f('ix_obd_analysis_sessions_vehicle_id'), 'obd_analysis_sessions', ['vehicle_id'], unique=False)

    op.create_table('obd_analysis_feedback',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('is_helpful', sa.Boolean(), nullable=False),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['obd_analysis_sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id')
    )


def downgrade() -> None:
    op.drop_table('obd_analysis_feedback')
    op.drop_index(op.f('ix_obd_analysis_sessions_vehicle_id'), table_name='obd_analysis_sessions')
    op.drop_index(op.f('ix_obd_analysis_sessions_status'), table_name='obd_analysis_sessions')
    op.drop_index(op.f('ix_obd_analysis_sessions_input_text_hash'), table_name='obd_analysis_sessions')
    op.drop_table('obd_analysis_sessions')

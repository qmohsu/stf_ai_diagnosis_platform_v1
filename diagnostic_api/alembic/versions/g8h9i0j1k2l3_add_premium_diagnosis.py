"""add premium_diagnosis_text column and premium feedback table

Revision ID: g8h9i0j1k2l3
Revises: f7a8b9c0d1e2
Create Date: 2026-02-28 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'g8h9i0j1k2l3'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add premium_diagnosis_text column to sessions table
    op.add_column(
        'obd_analysis_sessions',
        sa.Column('premium_diagnosis_text', sa.Text(), nullable=True),
    )

    # Create premium diagnosis feedback table
    op.create_table(
        'obd_premium_diagnosis_feedback',
        sa.Column(
            'id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            'session_id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey('obd_analysis_sessions.id'),
            nullable=False,
            index=True,
        ),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('diagnosis_text', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table('obd_premium_diagnosis_feedback')
    op.drop_column('obd_analysis_sessions', 'premium_diagnosis_text')

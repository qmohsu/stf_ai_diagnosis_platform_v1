"""add premium_diagnosis_model column and diagnosis_history table

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-03-03 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'h9i0j1k2l3m4'
down_revision: Union[str, None] = 'g8h9i0j1k2l3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add premium_diagnosis_model to sessions table
    op.add_column(
        'obd_analysis_sessions',
        sa.Column(
            'premium_diagnosis_model',
            sa.String(200),
            nullable=True,
        ),
    )

    # Create diagnosis_history table
    op.create_table(
        'diagnosis_history',
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
        sa.Column(
            'provider',
            sa.String(20),
            nullable=False,
        ),
        sa.Column(
            'model_name',
            sa.String(200),
            nullable=False,
        ),
        sa.Column(
            'diagnosis_text',
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table('diagnosis_history')
    op.drop_column(
        'obd_analysis_sessions',
        'premium_diagnosis_model',
    )

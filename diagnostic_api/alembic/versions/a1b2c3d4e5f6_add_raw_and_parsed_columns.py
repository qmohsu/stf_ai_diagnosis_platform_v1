"""add raw_input_text and parsed_summary_payload to obd_analysis_sessions

Revision ID: a1b2c3d4e5f6
Revises: 5ed3c5aa2328
Create Date: 2026-02-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5ed3c5aa2328'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'obd_analysis_sessions',
        sa.Column('raw_input_text', sa.Text(), nullable=True),
    )
    op.add_column(
        'obd_analysis_sessions',
        sa.Column(
            'parsed_summary_payload',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('obd_analysis_sessions', 'parsed_summary_payload')
    op.drop_column('obd_analysis_sessions', 'raw_input_text')

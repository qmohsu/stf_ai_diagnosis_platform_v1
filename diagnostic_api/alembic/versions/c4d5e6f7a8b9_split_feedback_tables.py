"""split obd_analysis_feedback into summary and detailed tables

Revision ID: c4d5e6f7a8b9
Revises: b3f4a7c8d9e0
Create Date: 2026-02-14 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3f4a7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create obd_summary_feedback table
    op.create_table(
        'obd_summary_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('obd_analysis_sessions.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_obd_summary_feedback_session_id',
                    'obd_summary_feedback', ['session_id'])

    # Create obd_detailed_feedback table
    op.create_table(
        'obd_detailed_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('obd_analysis_sessions.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_obd_detailed_feedback_session_id',
                    'obd_detailed_feedback', ['session_id'])

    # Migrate existing rows into obd_summary_feedback (preserve data)
    op.execute("""
        INSERT INTO obd_summary_feedback (id, session_id, rating, is_helpful,
                                          comments, corrected_diagnosis, created_at)
        SELECT id, session_id, rating, is_helpful,
               comments, corrected_diagnosis, created_at
        FROM obd_analysis_feedback
    """)

    # Drop old table
    op.drop_table('obd_analysis_feedback')


def downgrade() -> None:
    # Recreate old table
    op.create_table(
        'obd_analysis_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('obd_analysis_sessions.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('corrected_diagnosis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_obd_analysis_feedback_session_id',
                    'obd_analysis_feedback', ['session_id'])

    # Merge both tables back into old table
    op.execute("""
        INSERT INTO obd_analysis_feedback (id, session_id, rating, is_helpful,
                                           comments, corrected_diagnosis, created_at)
        SELECT id, session_id, rating, is_helpful,
               comments, corrected_diagnosis, created_at
        FROM obd_summary_feedback
    """)
    op.execute("""
        INSERT INTO obd_analysis_feedback (id, session_id, rating, is_helpful,
                                           comments, corrected_diagnosis, created_at)
        SELECT id, session_id, rating, is_helpful,
               comments, corrected_diagnosis, created_at
        FROM obd_detailed_feedback
    """)

    op.drop_table('obd_detailed_feedback')
    op.drop_table('obd_summary_feedback')

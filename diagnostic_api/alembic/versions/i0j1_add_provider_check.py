"""add CHECK constraint on diagnosis_history.provider

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-03-03 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'i0j1k2l3m4n5'
down_revision: Union[str, None] = 'h9i0j1k2l3m4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        'ck_diagnosis_history_provider',
        'diagnosis_history',
        "provider IN ('local', 'premium')",
    )


def downgrade() -> None:
    op.drop_constraint(
        'ck_diagnosis_history_provider',
        'diagnosis_history',
        type_='check',
    )

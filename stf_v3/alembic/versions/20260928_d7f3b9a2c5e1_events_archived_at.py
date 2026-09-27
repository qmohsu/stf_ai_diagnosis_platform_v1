"""Archive marker on conversations (PROD-15A FM-42).

``diagnosis_conversations.events_archived_at``: set by the host maintenance
service when a finished conversation's process events (``audit_events``)
older than 180 days were exported to the archive and deleted.  The API shows
it as ``events_archived`` and the replay answers 410 instead of an empty list.

Revision ID: d7f3b9a2c5e1
Revises: c4e8a1f2b7d3
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3b9a2c5e1"
down_revision: Union[str, None] = "c4e8a1f2b7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("diagnosis_conversations",
                  sa.Column("events_archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("diagnosis_conversations", "events_archived_at")

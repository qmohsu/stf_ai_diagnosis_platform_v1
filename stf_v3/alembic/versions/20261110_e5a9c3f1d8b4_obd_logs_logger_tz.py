"""Move stored log recording times onto Hong Kong wall clock (issue #257).

Loggers write wall-clock time without a zone.  Until this revision ingest
read it as UTC, so every ``obd_logs.recorded_start`` / ``recorded_end``
stored so far is 8 hours later than the real instant (a trip starting at
15:37 HKT was stored as 15:37 UTC = 23:37 HKT).  Ingest now reads the wall
clock as UTC+8 (``stf_v3.ingest.parsers.base.LOGGER_TZ``); this moves the
rows already stored onto the same footing.  ``uploaded_at`` is the server
clock and was always right.

A log uploaded by the OLD code after this migration ran but before the new
containers start (the deploy window, about a minute) would stay 8 hours
late: after a deploy, compare ``uploaded_at`` of the newest rows with the
API container's start time and shift such a row by hand.

Revision ID: e5a9c3f1d8b4
Revises: d7f3b9a2c5e1
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5a9c3f1d8b4"
down_revision: Union[str, None] = "d7f3b9a2c5e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SHIFT = (
    "UPDATE obd_logs SET "
    "recorded_start = recorded_start {op} interval '8 hours', "
    "recorded_end = recorded_end {op} interval '8 hours' "
    "WHERE recorded_start IS NOT NULL OR recorded_end IS NOT NULL"
)


def upgrade() -> None:
    """Shifts stored recording times 8 hours earlier (UTC → HKT reading)."""
    op.execute(_SHIFT.format(op="-"))


def downgrade() -> None:
    """Restores the old UTC reading (8 hours later)."""
    op.execute(_SHIFT.format(op="+"))

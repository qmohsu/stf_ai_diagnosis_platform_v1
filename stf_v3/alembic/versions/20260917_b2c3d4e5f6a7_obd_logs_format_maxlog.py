"""obd_logs.format accepts 'maxlog' (OBD Maximum Data Log, PROD-07 D3).

The real Jetson logger writes "OBD Maximum Data Log" CSVs, not native
TSV; V3 gained a targeted parser for it and the CHECK constraint on
``obd_logs.format`` must admit the new value.  Downgrade restores the
two-value constraint and therefore fails while ``maxlog`` rows exist
(intended: never silently drop stored logs).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "ck_obd_logs_format_values"


def upgrade() -> None:
    op.drop_constraint(_NAME, "obd_logs", type_="check")
    op.create_check_constraint(
        _NAME, "obd_logs", "format IN ('tsv','yamaha','maxlog')"
    )


def downgrade() -> None:
    op.drop_constraint(_NAME, "obd_logs", type_="check")
    op.create_check_constraint(_NAME, "obd_logs", "format IN ('tsv','yamaha')")

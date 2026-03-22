"""Migrate raw_input_text from DB column to filesystem storage.

Adds raw_input_file_path column, writes existing raw_input_text
values to disk as .txt files, then drops the raw_input_text column.

Revision ID: o7p8q9r0s1t2
Revises: n5o6p7q8r9s0
Create Date: 2026-03-22
"""

import os

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "o7p8q9r0s1t2"
down_revision = "n5o6p7q8r9s0"
branch_labels = None
depends_on = None

_TABLE = "obd_analysis_sessions"
_STORAGE_PATH = os.getenv("OBD_LOG_STORAGE_PATH", "/app/data/obd_logs")


def upgrade() -> None:
    """Add raw_input_file_path, migrate data to files, drop raw_input_text."""
    # 1. Add new column
    op.add_column(
        _TABLE,
        sa.Column(
            "raw_input_file_path",
            sa.String(500),
            nullable=True,
        ),
    )

    # 2. Migrate existing data to filesystem
    os.makedirs(_STORAGE_PATH, exist_ok=True)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, raw_input_text "
            "FROM obd_analysis_sessions "
            "WHERE raw_input_text IS NOT NULL"
        ),
    ).fetchall()

    for row in rows:
        session_id = str(row[0])
        raw_text = row[1]
        file_rel = f"{session_id}.txt"
        file_abs = os.path.join(_STORAGE_PATH, file_rel)
        with open(file_abs, "w", encoding="utf-8") as f:
            f.write(raw_text)
        conn.execute(
            sa.text(
                "UPDATE obd_analysis_sessions "
                "SET raw_input_file_path = :path "
                "WHERE id = :id"
            ),
            {"path": file_rel, "id": row[0]},
        )

    # 3. Drop old column
    op.drop_column(_TABLE, "raw_input_text")


def downgrade() -> None:
    """Re-add raw_input_text, read files back, drop raw_input_file_path."""
    # 1. Add column back
    op.add_column(
        _TABLE,
        sa.Column("raw_input_text", sa.Text(), nullable=True),
    )

    # 2. Read files back into column
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, raw_input_file_path "
            "FROM obd_analysis_sessions "
            "WHERE raw_input_file_path IS NOT NULL"
        ),
    ).fetchall()

    for row in rows:
        file_rel = row[1]
        file_abs = os.path.join(_STORAGE_PATH, file_rel)
        if os.path.isfile(file_abs):
            with open(file_abs, "r", encoding="utf-8") as f:
                raw_text = f.read()
            conn.execute(
                sa.text(
                    "UPDATE obd_analysis_sessions "
                    "SET raw_input_text = :text "
                    "WHERE id = :id"
                ),
                {"text": raw_text, "id": row[0]},
            )

    # 3. Drop new column
    op.drop_column(_TABLE, "raw_input_file_path")

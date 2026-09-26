"""Diagnosis jobs (PROD-11): one unfinished conversation per vehicle,
messages as request / response, report run facts, model-service state.

* ``diagnosis_conversations``: ``error_code``, ``started_at``,
  ``finished_at`` + partial unique index on ``vehicle_id`` while the
  status is queued / running (D4 / FM-15).
* ``messages.role`` (system/user/assistant/tool) cannot hold a Pydantic
  AI message, which is a *request* or a *response* mixing several part
  kinds (FM-47): the column becomes ``kind`` with those two values.
* ``reports``: ``partial``, ``stopped_reason``, ``limitations``,
  ``requests``, ``tool_calls``, ``model_source`` (FM-48).
* ``model_service_state``: the single row the host controller keeps about
  the on-demand vLLM (D1); the runtime role may read / insert / update it.

Downgrade is lossy by design: message rows (request / response) have no
pre-migration form and are deleted before the old CHECK comes back.

Revision ID: c4e8a1f2b7d3
Revises: b2c3d4e5f6a7
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e8a1f2b7d3"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "stf_v3_app"
_CONV = "diagnosis_conversations"


def upgrade() -> None:
    # ---------- conversations ----------
    op.add_column(_CONV, sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column(_CONV, sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_CONV, sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ux_conversations_vehicle_active", _CONV, ["vehicle_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )

    # ---------- messages: role -> kind (request / response) ----------
    op.drop_constraint("ck_messages_role_values", "messages", type_="check")
    op.alter_column("messages", "role", new_column_name="kind")
    op.create_check_constraint(
        "ck_messages_kind_values", "messages", "kind IN ('request','response')"
    )

    # ---------- reports: run facts ----------
    op.add_column("reports", sa.Column(
        "partial", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("reports", sa.Column(
        "stopped_reason", sa.String(length=20), nullable=False, server_default="complete"))
    op.add_column("reports", sa.Column(
        "limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        server_default=sa.text("'[]'::jsonb")))
    op.add_column("reports", sa.Column("requests", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("tool_calls", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("model_source", sa.String(length=100), nullable=True))

    # ---------- model_service_state ----------
    op.create_table(
        "model_service_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False, server_default="stopped"),
        sa.Column("blocked_reason", sa.String(length=30), nullable=True),
        sa.Column("started_by_us", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("controller_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gpu_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_model_service_state"),
        sa.CheckConstraint("id = 1", name="ck_model_service_state_single_row"),
        sa.CheckConstraint(
            "state IN ('stopped','starting','ready','blocked','failed')",
            name="ck_model_service_state_state_values",
        ),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON model_service_state TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON model_service_state FROM {_APP_ROLE}")
    op.drop_table("model_service_state")

    for col in ("model_source", "tool_calls", "requests", "limitations", "stopped_reason", "partial"):
        op.drop_column("reports", col)

    op.drop_constraint("ck_messages_kind_values", "messages", type_="check")
    op.execute("DELETE FROM messages")          # lossy: no pre-migration form
    op.alter_column("messages", "kind", new_column_name="role")
    op.create_check_constraint(
        "ck_messages_role_values", "messages", "role IN ('system','user','assistant','tool')"
    )

    op.drop_index("ux_conversations_vehicle_active", table_name=_CONV)
    for col in ("finished_at", "started_at", "error_code"):
        op.drop_column(_CONV, col)

"""Initial V3 schema (PROD-02): 12 business tables + procrastinate + grants.

Mirrors ``docs/plans/2026-09-08-v3-code-design.md`` §4 with the 2026-09-11
decision to drop pgvector / rag_chunks from Stage 1.  Constraint and index
names are spelled out so that ``alembic check`` matches the models in
``stf_v3.metadata``.

Revision ID: a1b2c3d4e5f6
Revises: None
Create Date: 2026-09-11

"""
from importlib import resources
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "stf_v3_app"
_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")


def _procrastinate_schema_sql() -> str:
    """Returns procrastinate's bundled ``schema.sql``.

    Returns:
        The SQL text that creates procrastinate's tables, types, functions
        and triggers.

    Raises:
        RuntimeError: If the installed procrastinate ships no schema file.
    """
    try:
        return (
            resources.files("procrastinate.sql")
            .joinpath("schema.sql")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "procrastinate schema.sql not found in the installed package"
        ) from exc


def _require_app_role() -> None:
    """Fails loudly if the runtime role is missing.

    The role is cluster-level and needs CREATEROLE, so it is created by
    ``scripts/create_database.sh`` (run as the Postgres superuser), not
    here.

    Raises:
        RuntimeError: If ``stf_v3_app`` does not exist.
    """
    exists = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _APP_ROLE}
    ).scalar()
    if not exists:
        raise RuntimeError(
            f"role {_APP_ROLE} missing: run stf_v3/scripts/create_database.sh"
        )


def upgrade() -> None:
    """Creates the full Stage 1 schema."""
    _require_app_role()

    # ---------- auth ----------
    op.create_table(
        "users",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=False),
        sa.Column(
            "is_superuser", sa.Boolean, server_default="false", nullable=False
        ),
        sa.Column(
            "is_verified", sa.Boolean, server_default="true", nullable=False
        ),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # ---------- workshops ----------
    op.create_table(
        "workshops",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workshops"),
        sa.UniqueConstraint("name", name="uq_workshops_name"),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("workshop_id", _UUID, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "workshop_id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workshop_id"], ["workshops.id"],
            name="fk_memberships_workshop_id_workshops", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('manager','technician')",
            name="ck_memberships_role_values",
        ),
    )
    op.create_index("ix_memberships_workshop", "memberships", ["workshop_id"])

    op.create_table(
        "invite_codes",
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("workshop_id", _UUID, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("used_by", _UUID, nullable=True),
        sa.Column("used_at", _TS, nullable=True),
        sa.Column("expires_at", _TS, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_invite_codes"),
        sa.ForeignKeyConstraint(
            ["workshop_id"], ["workshops.id"],
            name="fk_invite_codes_workshop_id_workshops", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_invite_codes_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["used_by"], ["users.id"], name="fk_invite_codes_used_by_users"
        ),
        sa.CheckConstraint(
            "role IN ('manager','technician')",
            name="ck_invite_codes_role_values",
        ),
    )

    # ---------- vehicles ----------
    op.create_table(
        "vehicles",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("workshop_id", _UUID, nullable=False),
        sa.Column("vin", sa.String(17), nullable=False),
        sa.Column("plate", sa.String(20), nullable=True),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("nickname", sa.String(100), nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_vehicles"),
        sa.ForeignKeyConstraint(
            ["workshop_id"], ["workshops.id"],
            name="fk_vehicles_workshop_id_workshops",
        ),
        sa.CheckConstraint(
            "vin ~ '^[A-HJ-NPR-Z0-9]{17}$'", name="ck_vehicles_vin_format"
        ),
    )
    op.create_index(
        "ux_vehicles_workshop_vin", "vehicles", ["workshop_id", "vin"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_vehicles_workshop", "vehicles", ["workshop_id"])

    op.create_table(
        "vehicle_devices",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("vehicle_id", _UUID, nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("token_hash", postgresql.CHAR(64), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("last_seen_at", _TS, nullable=True),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_vehicle_devices"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"],
            name="fk_vehicle_devices_vehicle_id_vehicles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_vehicle_devices_created_by_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_vehicle_devices_token_hash"),
    )

    # ---------- ingest ----------
    op.create_table(
        "obd_logs",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("vehicle_id", _UUID, nullable=False),  # D8 invariant
        sa.Column("sha256", postgresql.CHAR(64), nullable=False),
        sa.Column("raw_path", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("device_id", _UUID, nullable=True),
        sa.Column("uploaded_by", _UUID, nullable=True),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("vin_from_log", sa.String(17), nullable=True),
        sa.Column(
            "vin_mismatch", sa.Boolean, server_default="false", nullable=False
        ),
        sa.Column("recorded_start", _TS, nullable=True),
        sa.Column("recorded_end", _TS, nullable=True),
        sa.Column("uploaded_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_obd_logs"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name="fk_obd_logs_vehicle_id_vehicles"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["vehicle_devices.id"],
            name="fk_obd_logs_device_id_vehicle_devices",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name="fk_obd_logs_uploaded_by_users"
        ),
        sa.UniqueConstraint("vehicle_id", "sha256", name="uq_obd_logs_vehicle_sha"),
        sa.CheckConstraint(
            "source IN ('web','device')", name="ck_obd_logs_source_values"
        ),
        sa.CheckConstraint(
            "format IN ('tsv','yamaha')", name="ck_obd_logs_format_values"
        ),
    )
    op.create_index(
        "ix_obd_logs_vehicle_time", "obd_logs", ["vehicle_id", "uploaded_at"],
        postgresql_ops={"uploaded_at": "DESC"},
    )

    # ---------- diagnosis ----------
    op.create_table(
        "diagnosis_conversations",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("vehicle_id", _UUID, nullable=False),
        sa.Column("obd_log_id", _UUID, nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        sa.Column(
            "status", sa.String(10), server_default="queued", nullable=False
        ),
        sa.Column("stage", sa.String(4), server_default="s1", nullable=False),
        sa.Column("job_id", sa.BigInteger, nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean, server_default="false",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_diagnosis_conversations"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"],
            name="fk_diagnosis_conversations_vehicle_id_vehicles",
        ),
        sa.ForeignKeyConstraint(
            ["obd_log_id"], ["obd_logs.id"],
            name="fk_diagnosis_conversations_obd_log_id_obd_logs",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_diagnosis_conversations_created_by_users",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','done','error','cancelled')",
            name="ck_diagnosis_conversations_status_values",
        ),
    )
    op.create_index(
        "ix_conversations_vehicle_time", "diagnosis_conversations",
        ["vehicle_id", "created_at"], postgresql_ops={"created_at": "DESC"},
    )

    op.create_table(
        "messages",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", postgresql.JSONB, nullable=False),
        sa.Column("token_usage", postgresql.JSONB, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["diagnosis_conversations.id"],
            name="fk_messages_conversation_id_diagnosis_conversations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_messages_conv_seq"),
        sa.CheckConstraint(
            "role IN ('system','user','assistant','tool')",
            name="ck_messages_role_values",
        ),
    )

    op.create_table(
        "reports",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column(
            "citations", postgresql.JSONB, server_default="[]", nullable=False
        ),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("elapsed_s", sa.Numeric(8, 2), nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["diagnosis_conversations.id"],
            name="fk_reports_conversation_id_diagnosis_conversations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("conversation_id", name="uq_reports_conversation_id"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["diagnosis_conversations.id"],
            name="fk_audit_events_conversation_id_diagnosis_conversations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_audit_conv_seq"),
    )

    # ---------- knowledge ----------
    op.create_table(
        "manuals",
        sa.Column("id", _UUID, server_default=_GEN_UUID, nullable=False),
        sa.Column("uploaded_by", _UUID, nullable=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=False),
        sa.Column("vehicle_model", sa.String(100), nullable=False),
        sa.Column("factory_code", sa.String(100), nullable=True),
        sa.Column(
            "status", sa.String(20), server_default="uploading", nullable=False
        ),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("section_count", sa.Integer, nullable=True),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column("converter", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("md_file_path", sa.String(500), nullable=True),
        sa.Column("pdf_file_path", sa.String(500), nullable=True),
        sa.Column("pages_processed", sa.Integer, nullable=True),
        sa.Column("pages_total", sa.Integer, nullable=True),
        sa.Column("pages_phase", sa.String(50), nullable=True),
        sa.Column("warnings", postgresql.JSONB, nullable=True),
        sa.Column("job_id", sa.BigInteger, nullable=True),
        sa.Column("created_at", _TS, server_default=_NOW, nullable=False),
        sa.Column("updated_at", _TS, server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_manuals"),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name="fk_manuals_uploaded_by_users"
        ),
        sa.UniqueConstraint("file_hash", name="uq_manuals_file_hash"),
        sa.CheckConstraint(
            "status IN ('uploading','queued','converting','ingested','failed')",
            name="ck_manuals_status_values",
        ),
    )

    # ---------- jobs (procrastinate) ----------
    op.execute(_procrastinate_schema_sql())

    # ---------- runtime role grants ----------
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {_APP_ROLE}"
    )
    op.execute(
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public "
        f"TO {_APP_ROLE}"
    )
    op.execute(
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {_APP_ROLE}"
    )
    # Append-only black box: the app may only insert and read.
    op.execute(
        f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {_APP_ROLE}"
    )


def downgrade() -> None:
    """Drops everything created by ``upgrade`` (schema only)."""
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE}")
    op.execute(
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {_APP_ROLE}"
    )
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT tablename FROM pg_tables
                   WHERE schemaname = 'public'
                     AND tablename LIKE 'procrastinate\\_%' LOOP
            EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
          END LOOP;
          FOR r IN SELECT p.oid::regprocedure AS sig
                   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'public'
                     AND p.proname LIKE 'procrastinate\\_%' LOOP
            EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
          END LOOP;
          FOR r IN SELECT t.typname
                   FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = 'public' AND t.typtype = 'e'
                     AND t.typname LIKE 'procrastinate\\_%' LOOP
            EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
          END LOOP;
        END $$;
        """
    )
    for table in (
        "manuals",
        "audit_events",
        "reports",
        "messages",
        "diagnosis_conversations",
        "obd_logs",
        "vehicle_devices",
        "vehicles",
        "invite_codes",
        "memberships",
        "workshops",
        "users",
    ):
        op.drop_table(table)

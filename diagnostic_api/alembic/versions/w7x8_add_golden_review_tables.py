"""Add golden_entries + golden_reviews tables for HARNESS-17.

Adds two new tables to support the workshop-expert review
dashboard (GitHub Issue #82):

- ``golden_entries``: a queryable mirror of the golden JSONL
  files under ``tests/harness/evals/golden/v2/*.jsonl``.  The
  JSONL files remain the canonical source of truth; this table
  exists so the API can serve / filter / aggregate without
  re-parsing the files on every request, and so reviews have
  a stable foreign-key target.
- ``golden_reviews``: one row per ``(golden_entry, reviewer)``
  pair.  Carries star rating (overall + per-dimension), status
  (draft / accept / needs_revision / reject), free-text notes,
  and optional audio attachment using the same path pattern
  as the existing OBD feedback tables.

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create golden_entries and golden_reviews tables."""
    # ── golden_entries ──────────────────────────────────────
    op.create_table(
        "golden_entries",
        sa.Column(
            "id", sa.String(255),
            primary_key=True,
        ),  # e.g. '<manual_uuid>-dtc-001'
        sa.Column(
            "manual_id", sa.String(255), nullable=False,
        ),
        sa.Column(
            "category", sa.String(50), nullable=False,
        ),
        sa.Column(
            "question_type", sa.String(50), nullable=False,
        ),
        sa.Column(
            "difficulty", sa.String(20), nullable=False,
        ),
        sa.Column(
            "question_en", sa.Text(), nullable=False,
        ),
        sa.Column(
            "question_zh", sa.Text(), nullable=True,
        ),
        sa.Column(
            "obd_context", sa.Text(), nullable=True,
        ),
        sa.Column(
            "golden_summary_en", sa.Text(), nullable=False,
        ),
        sa.Column(
            "golden_summary_zh", sa.Text(), nullable=True,
        ),
        sa.Column(
            "golden_citations", JSONB(), nullable=False,
        ),
        sa.Column(
            "expected_recall_slugs", JSONB(), nullable=True,
        ),
        sa.Column(
            "must_contain", JSONB(), nullable=True,
        ),
        sa.Column(
            "pitfall_directives", JSONB(), nullable=True,
        ),
        sa.Column(
            "requires_image", sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "notes", sa.Text(), nullable=True,
        ),
        sa.Column(
            "source_jsonl_path", sa.String(500), nullable=True,
        ),
        sa.Column(
            "source_jsonl_line", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ("
            "'dtc', 'symptom', 'component', "
            "'adversarial', 'image')",
            name="ck_golden_entry_category",
        ),
        sa.CheckConstraint(
            "question_type IN ("
            "'lookup', 'procedural', 'cross-section', "
            "'image-required', 'adversarial')",
            name="ck_golden_entry_question_type",
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_golden_entry_difficulty",
        ),
    )
    op.create_index(
        "ix_golden_entries_question_type",
        "golden_entries",
        ["question_type"],
    )
    op.create_index(
        "ix_golden_entries_category",
        "golden_entries",
        ["category"],
    )
    op.create_index(
        "ix_golden_entries_manual_id",
        "golden_entries",
        ["manual_id"],
    )

    # ── golden_reviews ──────────────────────────────────────
    op.create_table(
        "golden_reviews",
        sa.Column(
            "id", UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "golden_entry_id", sa.String(255),
            sa.ForeignKey(
                "golden_entries.id", ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "star_rating", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "question_realism_score",
            sa.Integer(), nullable=True,
        ),
        sa.Column(
            "answer_correctness_score",
            sa.Integer(), nullable=True,
        ),
        sa.Column(
            "citation_faithfulness_score",
            sa.Integer(), nullable=True,
        ),
        sa.Column(
            "status", sa.String(20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "notes", sa.Text(), nullable=True,
        ),
        sa.Column(
            "audio_file_path", sa.String(500), nullable=True,
        ),
        sa.Column(
            "audio_duration_seconds",
            sa.Integer(), nullable=True,
        ),
        sa.Column(
            "audio_size_bytes",
            sa.Integer(), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "golden_entry_id", "reviewer_id",
            name="uq_golden_review_entry_reviewer",
        ),
        sa.CheckConstraint(
            "star_rating IS NULL OR "
            "(star_rating BETWEEN 1 AND 5)",
            name="ck_golden_review_star_rating",
        ),
        sa.CheckConstraint(
            "question_realism_score IS NULL OR "
            "(question_realism_score BETWEEN 1 AND 5)",
            name="ck_golden_review_q_realism_score",
        ),
        sa.CheckConstraint(
            "answer_correctness_score IS NULL OR "
            "(answer_correctness_score BETWEEN 1 AND 5)",
            name="ck_golden_review_a_correctness_score",
        ),
        sa.CheckConstraint(
            "citation_faithfulness_score IS NULL OR "
            "(citation_faithfulness_score BETWEEN 1 AND 5)",
            name="ck_golden_review_c_faithfulness_score",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'draft', 'accept', 'needs_revision', 'reject')",
            name="ck_golden_review_status",
        ),
    )
    op.create_index(
        "ix_golden_reviews_entry_id",
        "golden_reviews",
        ["golden_entry_id"],
    )
    op.create_index(
        "ix_golden_reviews_reviewer_id",
        "golden_reviews",
        ["reviewer_id"],
    )


def downgrade() -> None:
    """Drop golden_reviews and golden_entries (in dependency order)."""
    op.drop_index(
        "ix_golden_reviews_reviewer_id",
        table_name="golden_reviews",
    )
    op.drop_index(
        "ix_golden_reviews_entry_id",
        table_name="golden_reviews",
    )
    op.drop_table("golden_reviews")

    op.drop_index(
        "ix_golden_entries_manual_id",
        table_name="golden_entries",
    )
    op.drop_index(
        "ix_golden_entries_category",
        table_name="golden_entries",
    )
    op.drop_index(
        "ix_golden_entries_question_type",
        table_name="golden_entries",
    )
    op.drop_table("golden_entries")

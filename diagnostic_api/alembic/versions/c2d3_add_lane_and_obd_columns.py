"""Add OBD lane support to golden_entries + golden_reviews
(HARNESS-21 [2b/4]).

PR [2a/4] (#108) landed 15 OBD-lane eval goldens at
``golden/v1/yamaha_road_test.jsonl``, but the production dashboard
at ``/goldens`` only surfaces ``golden_entries`` rows synced from
``golden/v2/*``.  This migration adds the columns the
HARNESS-21 [2b/4] sync + API layer need to surface those OBD
entries alongside the manual lane.

Migration:

Add ``lane VARCHAR(20) NOT NULL DEFAULT 'manual'`` to two tables:
- ``golden_entries`` — discriminates manual vs OBD rows.  Sync
  populates ``'obd'`` for rows extracted from
  ``yamaha_road_test.jsonl``; manual rows stay ``'manual'``.
  Indexed for ``?lane=`` filter on ``GET /v2/goldens``.
- ``golden_reviews`` — every review is tagged with the lane of
  the entry it grades.  Reviews against manual entries get
  ``'manual'``; reviews against OBD entries get ``'obd'``.  Lets
  per-lane reviewed-count progress render on the
  ``/goldens`` landing page without an entry-table join.

Add three OBD-specific JSONB / Boolean columns to
``golden_entries`` (all nullable / defaulted, so manual rows stay
unchanged):

- ``expected_signal_citations JSONB`` — golden reference for the
  agent's ``signal_citations``.  ``[]`` (default) for manual.
  Populated by sync as a JSON-serialised list of
  ``ExpectedSignalCitation`` dicts.
- ``expected_dtcs JSONB`` — golden reference for the agent's
  ``dtc_citations``.  ``[]`` for manual.
- ``expected_no_evidence BOOLEAN NOT NULL DEFAULT FALSE`` —
  polarity flip flag for adversarial-OBD entries.  Always FALSE
  for manual.

No data backfill needed: existing manual rows get the defaults
(lane='manual', signal/dtc citations [], no-evidence flag FALSE)
and ``golden_sync`` on the next container startup re-populates
OBD rows from the filesystem.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


_LANE_CHECK = "lane IN ('manual', 'obd')"


def upgrade() -> None:
    """Add ``lane`` to both tables; add OBD-specific columns to
    ``golden_entries``; widen the ``question_type`` CHECK to
    accept OBD-lane values."""
    # ── golden_entries ───────────────────────────────────────────
    # Widen question_type CHECK to accept the six OBD-lane values
    # (signal_statistics, event_finding, dtc_enumeration,
    # dtc_decode, compound_obd, adversarial_obd) alongside the
    # existing five manual-lane values.  PostgreSQL requires
    # drop + recreate; no data migration needed (existing rows
    # use the manual values and stay valid).
    op.drop_constraint(
        "ck_golden_entry_question_type",
        "golden_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_golden_entry_question_type",
        "golden_entries",
        "question_type IN ("
        "'lookup', 'procedural', 'cross-section', "
        "'image-required', 'adversarial', "
        "'signal_statistics', 'event_finding', "
        "'dtc_enumeration', 'dtc_decode', "
        "'compound_obd', 'adversarial_obd')",
    )

    op.add_column(
        "golden_entries",
        sa.Column(
            "lane",
            sa.String(20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_check_constraint(
        "ck_golden_entry_lane",
        "golden_entries",
        _LANE_CHECK,
    )
    op.create_index(
        "ix_golden_entries_lane",
        "golden_entries",
        ["lane"],
    )
    op.add_column(
        "golden_entries",
        sa.Column(
            "expected_signal_citations",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "golden_entries",
        sa.Column(
            "expected_dtcs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "golden_entries",
        sa.Column(
            "expected_no_evidence",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # ── golden_reviews ───────────────────────────────────────────
    op.add_column(
        "golden_reviews",
        sa.Column(
            "lane",
            sa.String(20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_check_constraint(
        "ck_golden_review_lane",
        "golden_reviews",
        _LANE_CHECK,
    )
    op.create_index(
        "ix_golden_reviews_lane",
        "golden_reviews",
        ["lane"],
    )


def downgrade() -> None:
    """Drop the OBD-lane additions.

    Downgrades are lossy: any OBD entries in ``golden_entries`` and
    any OBD-tagged reviews in ``golden_reviews`` will lose their
    lane discriminator.  The ``expected_signal_citations`` /
    ``expected_dtcs`` JSONB content is dropped permanently.  Use
    only when rolling back the entire HARNESS-21 [2b/4] surface.
    """
    op.drop_index(
        "ix_golden_reviews_lane", table_name="golden_reviews",
    )
    op.drop_constraint(
        "ck_golden_review_lane", "golden_reviews", type_="check",
    )
    op.drop_column("golden_reviews", "lane")

    op.drop_column("golden_entries", "expected_no_evidence")
    op.drop_column("golden_entries", "expected_dtcs")
    op.drop_column("golden_entries", "expected_signal_citations")
    op.drop_index(
        "ix_golden_entries_lane", table_name="golden_entries",
    )
    op.drop_constraint(
        "ck_golden_entry_lane", "golden_entries", type_="check",
    )
    op.drop_column("golden_entries", "lane")

    # Restore the narrower question_type CHECK constraint
    # (manual-lane values only).  Any OBD entries left in the
    # table at downgrade time will violate this constraint and
    # the drop_constraint + create_constraint sequence will
    # raise — operators must delete OBD rows manually before
    # downgrading, which is the desired behaviour (OBD entries
    # without the lane discriminator are meaningless).
    op.drop_constraint(
        "ck_golden_entry_question_type",
        "golden_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_golden_entry_question_type",
        "golden_entries",
        "question_type IN ("
        "'lookup', 'procedural', 'cross-section', "
        "'image-required', 'adversarial')",
    )

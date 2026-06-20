"""Add manufacturer to manuals + rag_chunks; require vehicle identity.

APP-59 (GitHub issue #136).  Gives every manual a structured,
required vehicle identity so the harness can match a manual to the
session's vehicle instead of confabulating (the P00AF Hiace run
mistook a Toyota Hiace for a Yamaha scooter — issue #135).

- ``manuals.manufacturer``: new, required (NOT NULL after backfill).
- ``manuals.vehicle_model``: promoted to NOT NULL.
- ``rag_chunks.manufacturer``: new, nullable, indexed — stamped from
  the parent manual so retrieval can filter by make + model.

Backfills the two manuals currently in the vault (Yamaha TRICITY155,
Toyota Corolla E11) and copies make/model onto their chunks.  A
catch-all sets any other untagged rows to 'Unknown' so the NOT NULL
constraints never fail on stray rows (test/dev DBs).

Revision ID: f6a7b8c9d0e1
Revises: e4f5a6b7c8d9
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add columns nullable so the backfill can run first.
    op.add_column(
        "manuals",
        sa.Column("manufacturer", sa.String(100), nullable=True),
    )
    op.add_column(
        "rag_chunks",
        sa.Column("manufacturer", sa.String(100), nullable=True),
    )

    # 2. Backfill the two manuals in the vault (match on stable
    #    filename / current model value, whichever is present).
    op.execute(
        """
        UPDATE manuals
           SET manufacturer = 'Yamaha', vehicle_model = 'TRICITY155'
         WHERE filename ILIKE '%MWS150%'
            OR filename ILIKE '%TRICITY%'
            OR vehicle_model = 'MWS-150-A'
        """
    )
    op.execute(
        """
        UPDATE manuals
           SET manufacturer = 'Toyota', vehicle_model = 'Corolla E11'
         WHERE filename ILIKE '%Corolla%'
            OR vehicle_model ILIKE '%Corolla%'
        """
    )

    # 3. Catch-all so NOT NULL cannot fail on any stray rows.
    op.execute(
        "UPDATE manuals SET manufacturer = 'Unknown' "
        "WHERE manufacturer IS NULL"
    )
    op.execute(
        "UPDATE manuals SET vehicle_model = 'Unknown' "
        "WHERE vehicle_model IS NULL OR vehicle_model = ''"
    )

    # 4. Copy canonical make/model onto each manual's chunks so
    #    filtered retrieval is consistent with the manual record.
    op.execute(
        """
        UPDATE rag_chunks rc
           SET manufacturer  = m.manufacturer,
               vehicle_model = m.vehicle_model
          FROM manuals m
         WHERE rc.manual_id = m.id
        """
    )

    # 5. Enforce the required vehicle identity.
    op.alter_column(
        "manuals", "manufacturer", nullable=False,
    )
    op.alter_column(
        "manuals", "vehicle_model", nullable=False,
    )

    # 6. Index the new chunk column for make+model filtering.
    op.create_index(
        "ix_rag_chunks_manufacturer",
        "rag_chunks",
        ["manufacturer"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rag_chunks_manufacturer", table_name="rag_chunks",
    )
    op.alter_column(
        "manuals", "vehicle_model", nullable=True,
    )
    op.drop_column("rag_chunks", "manufacturer")
    op.drop_column("manuals", "manufacturer")

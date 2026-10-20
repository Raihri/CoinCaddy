"""Keep internal lineage from a Funding Passport packet to selected evidence register rows.

Revision ID: 0021_passport_evidence_lineage
Revises: 0020_funding_passport
"""
from alembic import op

revision = "0021_passport_evidence_lineage"
down_revision = "0020_funding_passport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE funding_passport_packet ADD COLUMN source_document_ids uuid[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE funding_passport_packet DROP COLUMN source_document_ids")

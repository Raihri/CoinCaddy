"""Versioned, owner-selected Funding Passport packets.

Revision ID: 0020_funding_passport
Revises: 0019_conditional_funding_plan
"""
from alembic import op

revision = "0020_funding_passport"
down_revision = "0019_conditional_funding_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE action_proposal DROP CONSTRAINT action_proposal_kind_check")
    op.execute("ALTER TABLE action_proposal ADD CONSTRAINT action_proposal_kind_check CHECK (kind IN ('invoice_reminder', 'supplier_payment', 'financing_application', 'funding_packet_share'))")
    op.execute("""
        CREATE TABLE funding_passport_packet (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            packet_version text NOT NULL CHECK (packet_version = 'passport-v1'),
            recipient text NOT NULL CHECK (recipient IN ('sandbox_lender_a', 'sandbox_lender_b')),
            selected_fields jsonb NOT NULL CHECK (jsonb_typeof(selected_fields) = 'array' AND jsonb_array_length(selected_fields) > 0),
            packet jsonb NOT NULL CHECK (jsonb_typeof(packet) = 'object'),
            packet_sha256 char(64) NOT NULL CHECK (packet_sha256 ~ '^[0-9a-f]{64}$'),
            created_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            UNIQUE (business_id, id),
            CHECK (expires_at > created_at AND expires_at <= created_at + interval '30 days')
        )
    """)
    op.execute("CREATE INDEX funding_passport_packet_recent_idx ON funding_passport_packet (business_id, created_at DESC)")
    op.execute("""
        CREATE FUNCTION funding_passport_packet_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'funding_passport_packet is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege'; END
        $$
    """)
    op.execute("CREATE TRIGGER funding_passport_packet_insert_only BEFORE UPDATE OR DELETE ON funding_passport_packet FOR EACH ROW EXECUTE FUNCTION funding_passport_packet_insert_only()")
    op.execute("CREATE TRIGGER funding_passport_packet_no_truncate BEFORE TRUNCATE ON funding_passport_packet FOR EACH STATEMENT EXECUTE FUNCTION funding_passport_packet_insert_only()")


def downgrade() -> None:
    op.execute("DROP TABLE funding_passport_packet")
    op.execute("DROP FUNCTION funding_passport_packet_insert_only()")
    op.execute("ALTER TABLE action_proposal DROP CONSTRAINT action_proposal_kind_check")
    op.execute("ALTER TABLE action_proposal ADD CONSTRAINT action_proposal_kind_check CHECK (kind IN ('invoice_reminder', 'supplier_payment', 'financing_application'))")

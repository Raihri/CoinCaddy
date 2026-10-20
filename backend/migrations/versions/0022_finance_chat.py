"""Finance Chat: an append-only record of each question, the read-only tool calls it made, and the cited answer.

Revision ID: 0022_finance_chat
Revises: 0021_passport_evidence_lineage
"""
from alembic import op

revision = "0022_finance_chat"
down_revision = "0021_passport_evidence_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chat_exchange (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            user_id          uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            role             text NOT NULL CHECK (role IN ('owner', 'bookkeeper', 'staff')),
            question         text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 600),
            question_sha256  char(64) NOT NULL,
            tool             text CHECK (tool IS NULL OR tool IN ('financial_health', 'cash_outlook', 'payment_review', 'funding_matches')),
            status           text NOT NULL CHECK (status IN ('answered', 'missing_evidence', 'not_found', 'not_allowed', 'read_only', 'not_supported')),
            assistant        text NOT NULL CHECK (assistant IN ('template', 'model')),
            tool_calls       jsonb NOT NULL CHECK (jsonb_typeof(tool_calls) = 'array'),
            answer           jsonb NOT NULL CHECK (jsonb_typeof(answer) = 'object'),
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX chat_exchange_user_idx ON chat_exchange (business_id, user_id, created_at DESC)")
    op.execute("""
        CREATE FUNCTION chat_exchange_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'chat_exchange is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege'; END
        $$
    """)
    op.execute("CREATE TRIGGER chat_exchange_insert_only BEFORE UPDATE OR DELETE ON chat_exchange FOR EACH ROW EXECUTE FUNCTION chat_exchange_insert_only()")
    op.execute("CREATE TRIGGER chat_exchange_no_truncate BEFORE TRUNCATE ON chat_exchange FOR EACH STATEMENT EXECUTE FUNCTION chat_exchange_insert_only()")


def downgrade() -> None:
    op.execute("DROP TABLE chat_exchange")
    op.execute("DROP FUNCTION chat_exchange_insert_only()")

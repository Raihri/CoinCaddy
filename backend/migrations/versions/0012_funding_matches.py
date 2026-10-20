"""Funding Matches: saved, append-only match runs.

A run stores the owner's request, the frozen inputs it used (the Cash Outlook inputs plus the few business facts
the filters read) and their SHA-256, the exact catalogue version and its contents, and the result. It contains
no lender identifier, application, or approval; nothing is sent to anyone.

Revision ID: 0012_funding_matches
Revises: 0011_payment_review
"""
from alembic import op

revision = "0012_funding_matches"
down_revision = "0011_payment_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE funding_match_run (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id        uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            calc_version       text NOT NULL,
            catalogue_version  text NOT NULL,
            as_of              date NOT NULL,
            request            jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            inputs             jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256      char(64) NOT NULL,
            catalogue          jsonb NOT NULL CHECK (jsonb_typeof(catalogue) = 'object'),
            results            jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            created_by         uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX funding_match_run_latest_idx ON funding_match_run (business_id, created_at DESC)")
    op.execute(
        """
        CREATE FUNCTION funding_match_run_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'funding_match_run is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER funding_match_run_append_only BEFORE UPDATE OR DELETE ON funding_match_run FOR EACH ROW EXECUTE FUNCTION funding_match_run_append_only()")
    op.execute("CREATE TRIGGER funding_match_run_no_truncate BEFORE TRUNCATE ON funding_match_run FOR EACH STATEMENT EXECUTE FUNCTION funding_match_run_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE funding_match_run")
    op.execute("DROP FUNCTION funding_match_run_append_only()")

"""Payment Review: saved, append-only reports.

A report stores the request, the frozen Cash Outlook inputs it was computed from (and their SHA-256),
the calculation version, and the four-path result, so any figure can be recomputed later. A report
never initiates or authorizes a payment; there is no execution link in this table.

Revision ID: 0011_payment_review
Revises: 0010_alerts
"""
from alembic import op

revision = "0011_payment_review"
down_revision = "0010_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payment_review (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            calc_version   text NOT NULL,
            as_of          date NOT NULL,
            request        jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            inputs         jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256  char(64) NOT NULL,
            results        jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            decision       text NOT NULL CHECK (decision IN ('within_reserve', 'reserve_shortfall', 'more_information_needed')),
            created_by     uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX payment_review_latest_idx ON payment_review (business_id, created_at DESC)")
    op.execute(
        """
        CREATE FUNCTION payment_review_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'payment_review is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER payment_review_append_only BEFORE UPDATE OR DELETE ON payment_review FOR EACH ROW EXECUTE FUNCTION payment_review_append_only()")
    op.execute("CREATE TRIGGER payment_review_no_truncate BEFORE TRUNCATE ON payment_review FOR EACH STATEMENT EXECUTE FUNCTION payment_review_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE payment_review")
    op.execute("DROP FUNCTION payment_review_append_only()")

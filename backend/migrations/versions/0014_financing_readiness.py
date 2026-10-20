"""Financing Readiness: a business document register and saved, append-only readiness checks.

business_document records that the business holds a document a lender may ask for (owner ID, trade licence, tax
certificate, guarantor letter, collateral valuation), with a short reference to where it is kept and its dates.
It never stores the document itself or its number: a reference with 8 or more digits in a row is refused here and
in the API. Rows are insert-only; a wrong entry is voided, never edited or deleted (the trigger from revision 0006).

readiness_check stores, for one product of one saved Funding Match, the frozen inputs with their SHA-256 and the
per-condition result. Its state is constrained: it can only be 'ready' when no condition is missing or uncertain.
It contains no score, probability, application, or lender identifier, and it is append-only.

Revision ID: 0014_financing_readiness
Revises: 0013_rescue_plan
"""
from alembic import op

revision = "0014_financing_readiness"
down_revision = "0013_rescue_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE business_document (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id   uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            kind          text NOT NULL CHECK (kind IN ('id', 'trade_licence', 'tax_certificate', 'guarantor_letter', 'collateral_valuation')),
            reference     text NOT NULL CHECK (char_length(reference) BETWEEN 1 AND 80
                                               AND regexp_replace(reference, '[[:space:]-]', '', 'g') !~ '[0-9]{8}'),
            issued_on     date CHECK (issued_on IS NULL OR issued_on >= DATE '1950-01-01'),
            expires_on    date,
            created_by    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at    timestamptz NOT NULL DEFAULT now(),
            voided_at     timestamptz,
            voided_by     uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            void_reason   text CHECK (void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 200),
            CHECK (expires_on IS NULL OR issued_on IS NULL OR expires_on > issued_on),
            CHECK ((voided_at IS NULL) = (voided_by IS NULL) AND (voided_at IS NULL) = (void_reason IS NULL)),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX business_document_kind_idx ON business_document (business_id, kind)")
    op.execute("CREATE TRIGGER business_document_only_void BEFORE UPDATE OR DELETE ON business_document FOR EACH ROW EXECUTE FUNCTION only_void_updates()")
    op.execute(
        """
        CREATE TABLE readiness_check (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            run_id           uuid NOT NULL,
            product_id       text NOT NULL CHECK (char_length(product_id) BETWEEN 1 AND 40),
            calc_version     text NOT NULL,
            as_of            date NOT NULL,
            inputs           jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256    char(64) NOT NULL,
            results          jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            state            text NOT NULL CHECK (state IN ('ready', 'needs_verification', 'not_ready')),
            present_count    integer NOT NULL CHECK (present_count >= 0),
            missing_count    integer NOT NULL CHECK (missing_count >= 0),
            uncertain_count  integer NOT NULL CHECK (uncertain_count >= 0),
            created_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CHECK ((state = 'not_ready') = (missing_count > 0)),
            CHECK ((state = 'needs_verification') = (missing_count = 0 AND uncertain_count > 0)),
            CHECK ((state = 'ready') = (missing_count = 0 AND uncertain_count = 0)),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, run_id) REFERENCES funding_match_run (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX readiness_check_run_idx ON readiness_check (business_id, run_id, created_at DESC)")
    op.execute(
        """
        CREATE FUNCTION readiness_check_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'readiness_check is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER readiness_check_append_only BEFORE UPDATE OR DELETE ON readiness_check FOR EACH ROW EXECUTE FUNCTION readiness_check_append_only()")
    op.execute("CREATE TRIGGER readiness_check_no_truncate BEFORE TRUNCATE ON readiness_check FOR EACH STATEMENT EXECUTE FUNCTION readiness_check_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE readiness_check")
    op.execute("DROP FUNCTION readiness_check_append_only()")
    op.execute("DROP TABLE business_document")

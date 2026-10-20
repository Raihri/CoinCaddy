"""Cash Gap Rescue Plan: documented deferral permissions and saved rescue plans.

deferral_permission records that a supplier agreed in writing (with a reference) to be paid a discretionary bill
later. It changes no bill: a rescue option may only move a bill that has one. Rows are insert-only; a wrong
entry is voided, never edited or deleted (the trigger from revision 0006).

rescue_plan stores the adjustments, the frozen inputs (the review's forecast inputs plus the permissions and
funding terms used) with their SHA-256, and the compared proposals. It is append-only and contains no action.

Revision ID: 0013_rescue_plan
Revises: 0012_funding_matches
"""
from alembic import op

revision = "0013_rescue_plan"
down_revision = "0012_funding_matches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE deferral_permission (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id         uuid NOT NULL,
            obligation_id       uuid NOT NULL,
            agreed_due_date     date NOT NULL CHECK (agreed_due_date >= DATE '2000-01-01'),
            evidence_reference  text NOT NULL CHECK (char_length(evidence_reference) BETWEEN 1 AND 120),
            note                text CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
            created_by          uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at          timestamptz NOT NULL DEFAULT now(),
            voided_at           timestamptz,
            voided_by           uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            void_reason         text CHECK (void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 200),
            CHECK ((voided_at IS NULL) = (voided_by IS NULL) AND (voided_at IS NULL) = (void_reason IS NULL)),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, obligation_id) REFERENCES obligation (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX deferral_permission_obligation_idx ON deferral_permission (business_id, obligation_id)")
    op.execute("CREATE TRIGGER deferral_permission_only_void BEFORE UPDATE OR DELETE ON deferral_permission FOR EACH ROW EXECUTE FUNCTION only_void_updates()")
    op.execute(
        """
        CREATE TABLE rescue_plan (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            review_id      uuid NOT NULL,
            calc_version   text NOT NULL,
            as_of          date NOT NULL,
            adjustments    jsonb NOT NULL CHECK (jsonb_typeof(adjustments) = 'object'),
            inputs         jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256  char(64) NOT NULL,
            results        jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            recommendation text NOT NULL CHECK (recommendation IN ('recommended', 'none_safe')),
            created_by     uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, review_id) REFERENCES payment_review (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX rescue_plan_review_idx ON rescue_plan (business_id, review_id, created_at DESC)")
    op.execute(
        """
        CREATE FUNCTION rescue_plan_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'rescue_plan is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER rescue_plan_append_only BEFORE UPDATE OR DELETE ON rescue_plan FOR EACH ROW EXECUTE FUNCTION rescue_plan_append_only()")
    op.execute("CREATE TRIGGER rescue_plan_no_truncate BEFORE TRUNCATE ON rescue_plan FOR EACH STATEMENT EXECUTE FUNCTION rescue_plan_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE rescue_plan")
    op.execute("DROP FUNCTION rescue_plan_append_only()")
    op.execute("DROP TABLE deferral_permission")

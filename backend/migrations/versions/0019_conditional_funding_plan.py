"""Conditional Funding Plan: receipt triggers, recalculated cash paths, and backup reviews.

Revision ID: 0019_conditional_funding_plan
Revises: 0018_receivables_guardian
"""
from alembic import op

revision = "0019_conditional_funding_plan"
down_revision = "0018_receivables_guardian"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conditional_funding_plan (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id           uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            name                  text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
            obligation_id         uuid NOT NULL,
            expected_amount_minor bigint NOT NULL CHECK (expected_amount_minor > 0 AND expected_amount_minor <= 1000000000000000),
            currency              char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            trigger_date          date NOT NULL,
            funding_match_run_id uuid NOT NULL,
            product_id            text NOT NULL CHECK (char_length(product_id) BETWEEN 1 AND 40),
            created_by            uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, obligation_id) REFERENCES obligation (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, funding_match_run_id) REFERENCES funding_match_run (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX conditional_funding_plan_recent_idx ON conditional_funding_plan (business_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE conditional_funding_event (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id uuid NOT NULL,
            plan_id     uuid NOT NULL,
            event_type  text NOT NULL CHECK (event_type IN ('created', 'receipt_verified', 'trigger_missed', 'cash_recalculated', 'backup_reviewed')),
            details     jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
            actor_id    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, plan_id) REFERENCES conditional_funding_plan (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX conditional_funding_event_plan_idx ON conditional_funding_event (business_id, plan_id, created_at, id)")
    op.execute("CREATE UNIQUE INDEX conditional_funding_one_trigger_idx ON conditional_funding_event (plan_id) WHERE event_type IN ('receipt_verified', 'trigger_missed')")
    op.execute("CREATE UNIQUE INDEX conditional_funding_one_recalc_idx ON conditional_funding_event (plan_id) WHERE event_type = 'cash_recalculated'")
    op.execute("CREATE UNIQUE INDEX conditional_funding_one_review_idx ON conditional_funding_event (plan_id) WHERE event_type = 'backup_reviewed'")
    op.execute(
        """
        CREATE TABLE conditional_funding_snapshot (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL,
            plan_id        uuid NOT NULL,
            generation     integer NOT NULL CHECK (generation > 0),
            branch         text NOT NULL CHECK (branch IN ('receipt_expected', 'backup_if_missed', 'missed_no_borrow', 'missed_with_backup', 'settled_actual')),
            calc_version   text NOT NULL,
            as_of          date NOT NULL,
            inputs         jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256  char(64) NOT NULL,
            results        jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            created_by     uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            UNIQUE (plan_id, generation, branch),
            FOREIGN KEY (business_id, plan_id) REFERENCES conditional_funding_plan (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX conditional_funding_snapshot_plan_idx ON conditional_funding_snapshot (business_id, plan_id, generation, branch)")
    for table in ("conditional_funding_plan", "conditional_funding_event", "conditional_funding_snapshot"):
        op.execute(f"""
            CREATE FUNCTION {table}_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION '{table} is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege'; END
            $$
        """)
        op.execute(f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_insert_only()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION {table}_insert_only()")


def downgrade() -> None:
    for table in ("conditional_funding_snapshot", "conditional_funding_event", "conditional_funding_plan"):
        op.execute(f"DROP TABLE {table}")
        op.execute(f"DROP FUNCTION {table}_insert_only()")

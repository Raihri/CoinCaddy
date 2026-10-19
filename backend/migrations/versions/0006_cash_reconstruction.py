"""Trusted Cash Reconstruction: balance anchors, source coverage, restricted funds, own-account transfers.

All four tables are insert-only. A wrong entry is voided (voided_at/by/reason) instead of edited or
deleted; a trigger rejects DELETE and any UPDATE other than setting the void fields once. Every row is
tied to an account of the same business through composite foreign keys.

Revision ID: 0006_cash_reconstruction
Revises: 0005_records_import
"""
from alembic import op

revision = "0006_cash_reconstruction"
down_revision = "0005_records_import"
branch_labels = None
depends_on = None

VOID_COLUMNS = """
            voided_at    timestamptz,
            voided_by    uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            void_reason  text CHECK (void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 200),
            CHECK ((voided_at IS NULL) = (voided_by IS NULL) AND (voided_at IS NULL) = (void_reason IS NULL))
"""

TABLES = ("balance_anchor", "coverage_period", "restricted_fund", "own_transfer")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION only_void_updates() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '% rows cannot be deleted; void them instead', TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.voided_at IS NOT NULL THEN
                RAISE EXCEPTION '% row is already voided', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF (to_jsonb(OLD) - 'voided_at' - 'voided_by' - 'void_reason')
               IS DISTINCT FROM (to_jsonb(NEW) - 'voided_at' - 'voided_by' - 'void_reason') THEN
                RAISE EXCEPTION 'only voiding is allowed on %', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TABLE balance_anchor (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL,
            account_id       uuid NOT NULL,
            anchor_date      date NOT NULL CHECK (anchor_date >= DATE '2000-01-01'),
            balance_minor    bigint NOT NULL CHECK (abs(balance_minor) <= 1000000000000000),
            currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            source_kind      text NOT NULL CHECK (source_kind IN ('bank_statement', 'wallet_statement', 'cash_count', 'opening_balance')),
            source_reference text NOT NULL CHECK (char_length(source_reference) BETWEEN 1 AND 120),
            entered_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            {VOID_COLUMNS},
            FOREIGN KEY (business_id, account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX balance_anchor_one_per_day ON balance_anchor (business_id, account_id, anchor_date) WHERE voided_at IS NULL"
    )
    op.execute(
        f"""
        CREATE TABLE coverage_period (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL,
            account_id       uuid NOT NULL,
            period_start     date NOT NULL CHECK (period_start >= DATE '2000-01-01'),
            period_end       date NOT NULL,
            source_kind      text NOT NULL CHECK (source_kind IN ('import_batch', 'statement', 'manual_confirmation')),
            import_batch_id  uuid,
            note             text CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
            created_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            {VOID_COLUMNS},
            CHECK (period_end >= period_start AND period_end - period_start <= 366),
            CHECK ((source_kind = 'import_batch') = (import_batch_id IS NOT NULL)),
            FOREIGN KEY (business_id, account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, import_batch_id) REFERENCES import_batch (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE restricted_fund (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL,
            account_id       uuid NOT NULL,
            amount_minor     bigint NOT NULL CHECK (amount_minor > 0 AND amount_minor <= 1000000000000000),
            currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            reason           text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 200),
            effective_from   date NOT NULL,
            effective_to     date,
            created_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            {VOID_COLUMNS},
            CHECK (effective_to IS NULL OR effective_to >= effective_from),
            FOREIGN KEY (business_id, account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE own_transfer (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL,
            out_txn_id       uuid,
            in_txn_id        uuid,
            note             text CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
            created_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            {VOID_COLUMNS},
            CHECK (out_txn_id IS NOT NULL OR in_txn_id IS NOT NULL),
            CHECK (out_txn_id IS DISTINCT FROM in_txn_id),
            FOREIGN KEY (business_id, out_txn_id) REFERENCES ledger_transaction (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, in_txn_id) REFERENCES ledger_transaction (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX own_transfer_out_once ON own_transfer (out_txn_id) WHERE voided_at IS NULL AND out_txn_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX own_transfer_in_once ON own_transfer (in_txn_id) WHERE voided_at IS NULL AND in_txn_id IS NOT NULL")
    for t in TABLES:
        op.execute(
            f"CREATE TRIGGER {t}_only_void BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION only_void_updates()"
        )


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE {t}")
    op.execute("DROP FUNCTION only_void_updates()")

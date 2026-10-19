"""Receivables and Payables: parties, obligations (invoices/bills), and allocations (payments/reversals).

- An obligation's original terms (party, direction, number, dates, amount, currency, category,
  reference) never change. A trigger allows only the dispute flag, cancellation, and the expected
  settlement account to be updated, and rejects DELETE.
- Allocations are append-only. A payment is positive; a reversal is the exact negative of one earlier
  payment, linked to it, and each payment can be reversed at most once.
- Outstanding = amount - sum(allocations). Over-allocation is prevented by the API under a row lock.

Revision ID: 0007_obligations
Revises: 0006_cash_reconstruction
"""
from alembic import op

revision = "0007_obligations"
down_revision = "0006_cash_reconstruction"
branch_labels = None
depends_on = None

RECEIVABLE_CATEGORIES = ("sales", "other_income")
PAYABLE_CATEGORIES = ("inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing",
                      "loan_repayment", "tax", "other_expense")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE party (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id  uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            created_by   uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX party_name_unique ON party (business_id, lower(name))")
    op.execute(
        f"""
        CREATE TABLE obligation (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id            uuid NOT NULL,
            direction              text NOT NULL CHECK (direction IN ('receivable', 'payable')),
            party_id               uuid NOT NULL,
            document_number        text NOT NULL CHECK (char_length(document_number) BETWEEN 1 AND 60),
            issue_date             date NOT NULL CHECK (issue_date >= DATE '2000-01-01'),
            due_date               date NOT NULL,
            amount_minor           bigint NOT NULL CHECK (amount_minor > 0 AND amount_minor <= 1000000000000000),
            currency               char(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            category               text NOT NULL,
            description            text CHECK (description IS NULL OR char_length(description) BETWEEN 1 AND 200),
            source_reference       text NOT NULL CHECK (char_length(source_reference) BETWEEN 1 AND 120),
            settlement_account_id  uuid,
            disputed               boolean NOT NULL DEFAULT false,
            dispute_reason         text CHECK (dispute_reason IS NULL OR char_length(dispute_reason) BETWEEN 1 AND 200),
            disputed_at            timestamptz,
            cancelled_at           timestamptz,
            cancelled_by           uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            cancel_reason          text CHECK (cancel_reason IS NULL OR char_length(cancel_reason) BETWEEN 1 AND 200),
            created_by             uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at             timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            CHECK (due_date >= issue_date AND due_date - issue_date <= 1830),
            CHECK ((direction = 'receivable' AND category IN ({_in(RECEIVABLE_CATEGORIES)}))
                OR (direction = 'payable' AND category IN ({_in(PAYABLE_CATEGORIES)}))),
            CHECK (disputed = (dispute_reason IS NOT NULL)),
            CHECK ((cancelled_at IS NULL) = (cancelled_by IS NULL) AND (cancelled_at IS NULL) = (cancel_reason IS NULL)),
            FOREIGN KEY (business_id, party_id) REFERENCES party (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, settlement_account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX obligation_number_unique ON obligation (business_id, direction, party_id, lower(document_number))"
    )
    op.execute("CREATE INDEX obligation_due_idx ON obligation (business_id, direction, due_date)")
    op.execute(
        """
        CREATE FUNCTION obligation_terms_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'obligations cannot be deleted; cancel them instead' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.cancelled_at IS NOT NULL AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at THEN
                RAISE EXCEPTION 'a cancelled obligation stays cancelled' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF (to_jsonb(OLD) - 'disputed' - 'dispute_reason' - 'disputed_at' - 'cancelled_at' - 'cancelled_by'
                              - 'cancel_reason' - 'settlement_account_id')
               IS DISTINCT FROM
               (to_jsonb(NEW) - 'disputed' - 'dispute_reason' - 'disputed_at' - 'cancelled_at' - 'cancelled_by'
                              - 'cancel_reason' - 'settlement_account_id') THEN
                RAISE EXCEPTION 'the original terms of an obligation cannot be changed' USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER obligation_terms_immutable BEFORE UPDATE OR DELETE ON obligation FOR EACH ROW EXECUTE FUNCTION obligation_terms_immutable()"
    )
    op.execute(
        """
        CREATE TABLE allocation (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL,
            obligation_id    uuid NOT NULL,
            kind             text NOT NULL CHECK (kind IN ('payment', 'reversal')),
            amount_minor     bigint NOT NULL CHECK (amount_minor <> 0 AND abs(amount_minor) <= 1000000000000000),
            currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            allocation_date  date NOT NULL CHECK (allocation_date >= DATE '2000-01-01'),
            ledger_txn_id    uuid,
            reverses_id      uuid REFERENCES allocation(id) ON DELETE RESTRICT,
            note             text CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
            created_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CHECK ((kind = 'payment' AND amount_minor > 0 AND reverses_id IS NULL)
                OR (kind = 'reversal' AND amount_minor < 0 AND reverses_id IS NOT NULL AND ledger_txn_id IS NULL)),
            FOREIGN KEY (business_id, obligation_id) REFERENCES obligation (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, ledger_txn_id) REFERENCES ledger_transaction (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX allocation_reversed_once ON allocation (reverses_id) WHERE reverses_id IS NOT NULL")
    op.execute("CREATE INDEX allocation_obligation_idx ON allocation (obligation_id)")
    op.execute("CREATE INDEX allocation_txn_idx ON allocation (ledger_txn_id) WHERE ledger_txn_id IS NOT NULL")
    op.execute(
        """
        CREATE FUNCTION allocation_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'allocation is append-only: % is not allowed; post a reversal instead', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER allocation_append_only BEFORE UPDATE OR DELETE ON allocation FOR EACH ROW EXECUTE FUNCTION allocation_append_only()"
    )
    op.execute(
        "CREATE TRIGGER allocation_no_truncate BEFORE TRUNCATE ON allocation FOR EACH STATEMENT EXECUTE FUNCTION allocation_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE allocation")
    op.execute("DROP FUNCTION allocation_append_only()")
    op.execute("DROP TABLE obligation")
    op.execute("DROP FUNCTION obligation_terms_immutable()")
    op.execute("DROP TABLE party")

"""Records Import: private import batches, staged rows, and the canonical ledger.

- import_batch: one uploaded file for one account. The original bytes live in private storage
  (never served); only a SHA-256 and size are kept here. A partial unique index makes uploading the
  same file to the same account idempotent while the earlier batch is not discarded.
- import_row: staged rows. They are never official cash; nothing reads them for balances.
- ledger_transaction: canonical posted movements. Signed integer minor units with explicit currency
  and business date, plus immutable lineage (batch, row number, parser version). UNIQUE
  (source_batch_id, source_row_number) makes double posting a row impossible. Append-only.

Revision ID: 0005_records_import
Revises: 0004_business_setup
"""
from alembic import op

revision = "0005_records_import"
down_revision = "0004_business_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE import_batch (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id       uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            account_id        uuid NOT NULL,
            uploaded_by       uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            source_name       text NOT NULL CHECK (char_length(source_name) BETWEEN 1 AND 120),
            file_type         text NOT NULL CHECK (file_type IN ('csv', 'xlsx')),
            content_sha256    char(64) NOT NULL,
            size_bytes        integer NOT NULL CHECK (size_bytes > 0),
            storage_key       text NOT NULL,
            parser_version    text NOT NULL,
            status            text NOT NULL CHECK (status IN ('needs_mapping', 'staged', 'posted', 'discarded')),
            headers           jsonb NOT NULL CHECK (jsonb_typeof(headers) = 'array'),
            column_profile    jsonb NOT NULL CHECK (jsonb_typeof(column_profile) = 'array'),
            mapping           jsonb,
            revision          integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            row_count         integer NOT NULL CHECK (row_count >= 0),
            commit_key        uuid,
            posted_count      integer,
            posted_at         timestamptz,
            posted_by         uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT,
            CHECK ((status = 'posted') = (commit_key IS NOT NULL AND posted_at IS NOT NULL AND posted_count IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX import_batch_same_file_once ON import_batch (business_id, account_id, content_sha256)
        WHERE status <> 'discarded'
        """
    )
    op.execute(
        """
        CREATE TABLE import_row (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id         uuid NOT NULL,
            batch_id            uuid NOT NULL,
            row_number          integer NOT NULL CHECK (row_number >= 2),
            raw                 jsonb NOT NULL CHECK (jsonb_typeof(raw) = 'array'),
            status              text NOT NULL DEFAULT 'unmapped' CHECK (status IN ('unmapped', 'valid', 'error')),
            errors              jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(errors) = 'array'),
            business_date       date,
            amount_minor        bigint,
            currency            char(3),
            description         text,
            counterparty        text,
            reference           text,
            fingerprint         char(64),
            duplicate_kind      text CHECK (duplicate_kind IN ('in_file', 'in_ledger')),
            duplicate_of_row    integer,
            duplicate_of_txn    uuid,
            decision            text NOT NULL DEFAULT 'exclude' CHECK (decision IN ('include', 'exclude', 'pending')),
            decided_by          uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            UNIQUE (batch_id, row_number),
            FOREIGN KEY (business_id, batch_id) REFERENCES import_batch (business_id, id) ON DELETE RESTRICT,
            CHECK (status <> 'valid' OR (business_date IS NOT NULL AND amount_minor IS NOT NULL AND amount_minor <> 0
                                         AND currency IS NOT NULL AND description IS NOT NULL)),
            CHECK (status = 'valid' OR decision = 'exclude')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ledger_transaction (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id        uuid NOT NULL,
            account_id         uuid NOT NULL,
            business_date      date NOT NULL,
            amount_minor       bigint NOT NULL CHECK (amount_minor <> 0 AND abs(amount_minor) <= 1000000000000000),
            currency           char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            description        text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 200),
            counterparty       text,
            reference          text,
            fingerprint        char(64) NOT NULL,
            source_batch_id    uuid NOT NULL,
            source_row_number  integer NOT NULL,
            parser_version     text NOT NULL,
            posted_by          uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            posted_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_batch_id, source_row_number),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, account_id) REFERENCES cash_account (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, source_batch_id) REFERENCES import_batch (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ledger_transaction_match_idx ON ledger_transaction (business_id, account_id, business_date, amount_minor)"
    )
    op.execute(
        """
        CREATE FUNCTION ledger_transaction_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_transaction is append-only: % is not allowed; post a correction instead', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_transaction_no_update_delete BEFORE UPDATE OR DELETE ON ledger_transaction
        FOR EACH ROW EXECUTE FUNCTION ledger_transaction_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_transaction_no_truncate BEFORE TRUNCATE ON ledger_transaction
        FOR EACH STATEMENT EXECUTE FUNCTION ledger_transaction_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ledger_transaction")
    op.execute("DROP FUNCTION ledger_transaction_append_only()")
    op.execute("DROP TABLE import_row")
    op.execute("DROP TABLE import_batch")

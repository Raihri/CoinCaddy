"""Financial Health: classification of posted ledger transactions.

The ledger itself stays append-only; how a transaction is interpreted (operating receipt, supplier
payment, financing, owner money...) is a separate, changeable row, and every change is audited.
Own-account transfers are never classified here: they are identified by the own_transfer table.

Revision ID: 0008_classification
Revises: 0007_obligations
"""
from alembic import op

revision = "0008_classification"
down_revision = "0007_obligations"
branch_labels = None
depends_on = None

RECEIPT_CATEGORIES = ("sales", "other_income")
PAYMENT_CATEGORIES = ("inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing",
                      "fees_charges", "tax", "other_expense")


def _in(values):
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE txn_classification (
            business_id    uuid NOT NULL,
            txn_id         uuid PRIMARY KEY,
            kind           text NOT NULL CHECK (kind IN ('operating_receipt', 'operating_payment', 'financing_inflow',
                                                         'financing_outflow', 'owner_contribution', 'owner_drawing',
                                                         'other_non_operating')),
            category       text,
            classified_by  uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            classified_at  timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (business_id, txn_id) REFERENCES ledger_transaction (business_id, id) ON DELETE RESTRICT,
            CHECK ((kind = 'operating_receipt' AND category IN ({_in(RECEIPT_CATEGORIES)}))
                OR (kind = 'operating_payment' AND category IN ({_in(PAYMENT_CATEGORIES)}))
                OR (kind NOT IN ('operating_receipt', 'operating_payment') AND category IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX txn_classification_business_idx ON txn_classification (business_id)")


def downgrade() -> None:
    op.execute("DROP TABLE txn_classification")

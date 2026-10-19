"""Foundation data contract: business and cash account.

Money columns are signed BIGINT minor units with an explicit currency code. Every business has an
explicit IANA timezone used to derive business dates. cash_account carries business_id with a
composite unique key so later tables can use business-scoped composite foreign keys.

Revision ID: 0001_foundation
Revises:
"""
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE business (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug                  text NOT NULL UNIQUE,
            name                  text NOT NULL,
            jurisdiction          char(2) NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}$'),
            base_currency         char(3) NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
            timezone              text NOT NULL,
            reserve_amount_minor  bigint NOT NULL DEFAULT 0 CHECK (reserve_amount_minor >= 0),
            reserve_currency      char(3) NOT NULL CHECK (reserve_currency ~ '^[A-Z]{3}$'),
            is_synthetic          boolean NOT NULL DEFAULT true,
            created_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE cash_account (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id  uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            external_ref text NOT NULL,
            label        text NOT NULL,
            kind         text NOT NULL CHECK (kind IN ('bank', 'mobile_wallet', 'cash')),
            currency     char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            UNIQUE (business_id, external_ref)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE cash_account")
    op.execute("DROP TABLE business")

"""Business Setup: settings versioning and stricter profile/account constraints.

- business.version supports optimistic concurrency: an edit must name the version it was based on.
- The reserve is held in the business's base currency (single-currency reserve for now).
- Reserve is capped at 10^15 minor units so it stays exact in JavaScript clients (< 2^53).
- Account labels are 1-60 characters and unique per business, ignoring case.

Revision ID: 0004_business_setup
Revises: 0003_audit_trail
"""
from alembic import op

revision = "0004_business_setup"
down_revision = "0003_audit_trail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE business ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version >= 1)")
    op.execute("ALTER TABLE business ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE business ADD CONSTRAINT business_name_length CHECK (char_length(name) BETWEEN 1 AND 120)")
    op.execute("ALTER TABLE business ADD CONSTRAINT business_reserve_in_base_currency CHECK (reserve_currency = base_currency)")
    op.execute(
        "ALTER TABLE business ADD CONSTRAINT business_reserve_max CHECK (reserve_amount_minor <= 1000000000000000)"
    )
    op.execute("ALTER TABLE business ADD CONSTRAINT business_timezone_nonempty CHECK (char_length(timezone) BETWEEN 1 AND 64)")
    op.execute("ALTER TABLE cash_account ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")
    op.execute(
        "ALTER TABLE cash_account ADD CONSTRAINT cash_account_label_length CHECK (char_length(label) BETWEEN 1 AND 60)"
    )
    op.execute("CREATE UNIQUE INDEX cash_account_label_unique ON cash_account (business_id, lower(label))")


def downgrade() -> None:
    op.execute("DROP INDEX cash_account_label_unique")
    op.execute("ALTER TABLE cash_account DROP CONSTRAINT cash_account_label_length")
    op.execute("ALTER TABLE cash_account DROP COLUMN updated_at")
    for c in ("business_timezone_nonempty", "business_reserve_max", "business_reserve_in_base_currency", "business_name_length"):
        op.execute(f"ALTER TABLE business DROP CONSTRAINT {c}")
    op.execute("ALTER TABLE business DROP COLUMN updated_at")
    op.execute("ALTER TABLE business DROP COLUMN version")

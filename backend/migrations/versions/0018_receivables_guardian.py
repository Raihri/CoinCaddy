"""Receivables Guardian: verified customer contacts, internal reminder drafts, and the link from a draft to its approval.

customer_contact holds only a MASKED address (an e-mail like a***@example.com, or a phone as ••••4567) and a
business-scoped HMAC-SHA256 of the normalized full address; the full address is never stored. A contact is immutable. It becomes usable only when a
customer_contact_review verifies it, and a newer contact for the same customer and channel replaces it (so swapping a
contact needs a fresh verification). reminder_draft is internal text built from verified invoice facts; it is linked to
an approval proposal only through reminder_draft_link. Every table here is insert-only: no row is edited or deleted,
and none of them marks an invoice paid or changes any obligation.

Revision ID: 0018_receivables_guardian
Revises: 0017_sandbox_executor
"""
from alembic import op

revision = "0018_receivables_guardian"
down_revision = "0017_sandbox_executor"
branch_labels = None
depends_on = None

TABLES = ("customer_contact", "customer_contact_review", "reminder_draft", "reminder_draft_link")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION guardian_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is insert-only: % is not allowed', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE customer_contact (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id     uuid NOT NULL,
            party_id        uuid NOT NULL,
            channel         text NOT NULL CHECK (channel IN ('email', 'sms')),
            address_masked  text NOT NULL CHECK (char_length(address_masked) BETWEEN 3 AND 120),
            address_sha256  char(64) NOT NULL,
            recorded_by     uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, party_id) REFERENCES party (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX customer_contact_party_idx ON customer_contact (business_id, party_id, channel, created_at DESC)")
    op.execute(
        """
        CREATE TABLE customer_contact_review (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id uuid NOT NULL,
            contact_id  uuid NOT NULL UNIQUE,
            decision    text NOT NULL CHECK (decision IN ('verified', 'rejected')),
            method      text CHECK (method IN ('called_known_contact', 'met_in_person', 'written_confirmation')),
            reference   text CHECK (reference IS NULL OR char_length(reference) BETWEEN 3 AND 120),
            reason      text CHECK (reason IS NULL OR char_length(reason) BETWEEN 3 AND 200),
            reviewer_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CHECK ((decision = 'verified' AND method IS NOT NULL AND reference IS NOT NULL AND reason IS NULL)
                OR (decision = 'rejected' AND method IS NULL AND reference IS NULL AND reason IS NOT NULL)),
            FOREIGN KEY (business_id, contact_id) REFERENCES customer_contact (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE reminder_draft (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id   uuid NOT NULL,
            obligation_id uuid NOT NULL,
            contact_id    uuid NOT NULL,
            channel       text NOT NULL CHECK (channel IN ('email', 'sms')),
            message       text NOT NULL CHECK (char_length(message) BETWEEN 10 AND 500),
            facts         jsonb NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
            facts_sha256  char(64) NOT NULL,
            created_by    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, obligation_id) REFERENCES obligation (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, contact_id) REFERENCES customer_contact (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX reminder_draft_obligation_idx ON reminder_draft (business_id, obligation_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE reminder_draft_link (
            draft_id     uuid PRIMARY KEY,
            business_id  uuid NOT NULL,
            proposal_id  uuid NOT NULL UNIQUE,
            linked_by    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at   timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (business_id, draft_id) REFERENCES reminder_draft (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    for t in TABLES:
        op.execute(f"CREATE TRIGGER {t}_insert_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION guardian_insert_only()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION guardian_insert_only()")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE {t}")
    op.execute("DROP FUNCTION guardian_insert_only()")

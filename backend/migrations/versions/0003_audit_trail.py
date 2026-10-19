"""Audit Trail: append-only event log with a tamper-evident hash chain.

UPDATE, DELETE, and TRUNCATE on audit_event are rejected by triggers, so the application can only
append. Each row stores the previous row's hash and its own SHA-256 over its canonical content;
editing a row out-of-band breaks the chain, which app.modules.audit.verify detects.

business_id is NULL for user-level security events (sign-in, sign-out, attempts on a business the
user does not belong to); those never appear on a business's audit page.

Revision ID: 0003_audit_trail
Revises: 0002_business_access
"""
from alembic import op

revision = "0003_audit_trail"
down_revision = "0002_business_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_event (
            seq            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            id             uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
            occurred_at    timestamptz NOT NULL,
            business_id    uuid REFERENCES business(id) ON DELETE RESTRICT,
            actor_user_id  uuid REFERENCES app_user(id) ON DELETE RESTRICT,
            actor_name     text,
            actor_role     text CHECK (actor_role IN ('owner', 'bookkeeper', 'staff')),
            action         text NOT NULL CHECK (action ~ '^[a-z]+(\\.[a-z_]+)+$'),
            outcome        text NOT NULL CHECK (outcome IN ('success', 'denied', 'not_found', 'conflict', 'failed')),
            target_type    text,
            target_id      text,
            details        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
            prev_hash      char(64) NOT NULL,
            event_hash     char(64) NOT NULL UNIQUE
        )
        """
    )
    op.execute("CREATE INDEX audit_event_business_seq_idx ON audit_event (business_id, seq DESC)")
    op.execute(
        """
        CREATE FUNCTION audit_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION audit_event_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE audit_event")
    op.execute("DROP FUNCTION audit_event_append_only()")

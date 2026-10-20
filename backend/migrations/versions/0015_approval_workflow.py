"""Approved Action Workflow: immutable action proposals and an append-only decision history.

action_proposal stores the exact payload of one proposed external action (an invoice reminder, a sandbox supplier
payment, a financing application), its action tier, the role that must approve it, whether a second reviewer is
required, its expiry, and the SHA-256 of the canonical proposal envelope. A row is never updated or deleted.

action_decision appends what happened to a proposal: approved, rejected, withdrawn, or marked executable after the
release recheck. Its state is derived from these events; nothing executes here (the sandbox executor is a later
prompt). A trigger re-checks the policy in the database as well as in the API: every decision must carry the
proposal's hash and happen before expiry; approvals and releases need an owner; a T3 approval needs a recent
re-authentication and a documented verification; a second-reviewer proposal cannot be approved by its proposer; a
release needs a prior approval. At most one decision and one release exist per proposal.

Revision ID: 0015_approval_workflow
Revises: 0014_financing_readiness
"""
from alembic import op

revision = "0015_approval_workflow"
down_revision = "0014_financing_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE action_proposal (
            id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id               uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            kind                      text NOT NULL CHECK (kind IN ('invoice_reminder', 'supplier_payment', 'financing_application')),
            tier                      text NOT NULL CHECK (tier IN ('T2', 'T3')),
            required_role             text NOT NULL CHECK (required_role = 'owner'),
            second_reviewer_required  boolean NOT NULL,
            policy_version            text NOT NULL,
            payload                   jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            payload_sha256            char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
            proposed_by               uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            proposed_role             text NOT NULL CHECK (proposed_role IN ('owner', 'bookkeeper')),
            created_at                timestamptz NOT NULL,
            expires_at                timestamptz NOT NULL,
            CHECK ((kind = 'invoice_reminder') = (tier = 'T2')),
            CHECK (tier <> 'T3' OR second_reviewer_required),
            CHECK (expires_at > created_at AND expires_at <= created_at + interval '72 hours'),
            CHECK (tier <> 'T3' OR expires_at <= created_at + interval '24 hours'),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX action_proposal_inbox_idx ON action_proposal (business_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE action_decision (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id         uuid NOT NULL,
            proposal_id         uuid NOT NULL,
            event               text NOT NULL CHECK (event IN ('approved', 'rejected', 'withdrawn', 'executable')),
            actor_id            uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            actor_role          text NOT NULL CHECK (actor_role IN ('owner', 'bookkeeper')),
            payload_sha256      char(64) NOT NULL,
            reason              text CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 300),
            reauthenticated_at  timestamptz,
            verification        jsonb CHECK (verification IS NULL OR jsonb_typeof(verification) = 'object'),
            checks              jsonb CHECK (checks IS NULL OR jsonb_typeof(checks) = 'array'),
            created_at          timestamptz NOT NULL,
            CHECK (event <> 'rejected' OR reason IS NOT NULL),
            CHECK (event <> 'executable' OR checks IS NOT NULL),
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX action_decision_one_decision ON action_decision (proposal_id) WHERE event IN ('approved', 'rejected', 'withdrawn')")
    op.execute("CREATE UNIQUE INDEX action_decision_one_release ON action_decision (proposal_id) WHERE event = 'executable'")
    op.execute(
        """
        CREATE FUNCTION action_decision_policy() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            p action_proposal%ROWTYPE;
        BEGIN
            SELECT * INTO p FROM action_proposal WHERE id = NEW.proposal_id AND business_id = NEW.business_id;
            IF NEW.payload_sha256 <> p.payload_sha256 THEN
                RAISE EXCEPTION 'decision hash does not match the proposal' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.created_at >= p.expires_at THEN
                RAISE EXCEPTION 'proposal has expired' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.event IN ('approved', 'rejected', 'executable') AND NEW.actor_role <> p.required_role THEN
                RAISE EXCEPTION 'only the % can decide or release this proposal', p.required_role USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.event = 'approved' THEN
                IF p.second_reviewer_required AND NEW.actor_id = p.proposed_by THEN
                    RAISE EXCEPTION 'this proposal needs a second reviewer; its proposer cannot approve it' USING ERRCODE = 'check_violation';
                END IF;
                IF p.tier = 'T3' AND (NEW.verification IS NULL OR NEW.reauthenticated_at IS NULL
                                      OR NEW.reauthenticated_at < NEW.created_at - interval '5 minutes') THEN
                    RAISE EXCEPTION 'a T3 approval needs a recent re-authentication and a documented verification' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            IF NEW.event = 'executable' AND NOT EXISTS (
                SELECT 1 FROM action_decision WHERE proposal_id = NEW.proposal_id AND event = 'approved') THEN
                RAISE EXCEPTION 'only an approved proposal can be marked executable' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER action_decision_policy BEFORE INSERT ON action_decision FOR EACH ROW EXECUTE FUNCTION action_decision_policy()")
    op.execute(
        """
        CREATE FUNCTION approval_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for t in ("action_proposal", "action_decision"):
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION approval_append_only()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION approval_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE action_decision")
    op.execute("DROP TABLE action_proposal")
    op.execute("DROP FUNCTION action_decision_policy()")
    op.execute("DROP FUNCTION approval_append_only()")

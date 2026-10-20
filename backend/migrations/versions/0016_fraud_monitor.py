"""Transaction Fraud Monitor: payee destinations, versioned rule flags, payment holds, and reviews.

- payee_destination: where a supplier is paid. It never stores the account or wallet number: only its last four
  digits and a keyed HMAC, so a change can be detected without keeping the number. Insert-only.
- fraud_scan: one evaluation of the rules (manual, or when a payment is proposed, approved, or released). Insert-only.
- fraud_flag: one suspected-activity candidate found by a rule. It keeps the compared record IDs and a snapshot of
  what was compared, so the evidence survives later changes. A flag is suspicion, never a fraud verdict. Insert-only;
  UNIQUE (business_id, dedup_key) means a repeated scan never duplicates a flag.
- fraud_flag_hold: a proposed sandbox payment held by a flag. Insert-only.
- fraud_review: confirm, reject (clear), or defer a flag with a reason. Insert-only; a trigger refuses any review
  after a final one, a clearance of a destination change without independent verification, a clearance of a held
  payment by anyone other than an owner who did not propose it, and a clearance by the person who recorded the
  destination change.
- A trigger on action_decision refuses to approve or release a proposal while an uncleared flag holds it, so the
  hold is enforced by the database as well as by the API.

Revision ID: 0016_fraud_monitor
Revises: 0015_approval_workflow
"""
from alembic import op

revision = "0016_fraud_monitor"
down_revision = "0015_approval_workflow"
branch_labels = None
depends_on = None

NO_LONG_NUMBER = "regexp_replace({col}, '[[:space:]-]', '', 'g') !~ '[0-9]{{8}}'"
TABLES = ("payee_destination", "fraud_scan", "fraud_flag", "fraud_flag_hold", "fraud_review")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE payee_destination (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            party_id       uuid NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('bank', 'mobile_wallet')),
            institution    text NOT NULL CHECK (char_length(institution) BETWEEN 2 AND 80 AND {NO_LONG_NUMBER.format(col='institution')}),
            last4          char(4) NOT NULL CHECK (last4 ~ '^[0-9]{{4}}$'),
            number_hmac    char(64) NOT NULL CHECK (number_hmac ~ '^[0-9a-f]{{64}}$'),
            change_source  text NOT NULL CHECK (change_source IN ('onboarding', 'supplier_email', 'supplier_letter', 'phone_call_received',
                                                                  'in_person', 'invoice_document')),
            note           text CHECK (note IS NULL OR (char_length(note) BETWEEN 1 AND 200 AND {NO_LONG_NUMBER.format(col='note')})),
            recorded_by    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            recorded_role  text NOT NULL CHECK (recorded_role IN ('owner', 'bookkeeper')),
            created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, party_id) REFERENCES party (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX payee_destination_party_idx ON payee_destination (business_id, party_id, created_at)")
    op.execute(
        """
        CREATE TABLE fraud_scan (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id     uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            rules_version   text NOT NULL,
            as_of           date NOT NULL,
            trigger         text NOT NULL CHECK (trigger IN ('manual', 'proposal', 'approval', 'release', 'destination')),
            proposal_id     uuid,
            items_checked   integer NOT NULL CHECK (items_checked >= 0),
            flags_created   integer NOT NULL CHECK (flags_created >= 0),
            flags_existing  integer NOT NULL CHECK (flags_existing >= 0),
            holds_added     integer NOT NULL CHECK (holds_added >= 0),
            created_by      uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX fraud_scan_latest_idx ON fraud_scan (business_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE fraud_flag (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id     uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            rule            text NOT NULL CHECK (rule IN ('duplicate_payment', 'unusual_payee_amount', 'split_near_limit', 'lookalike_payee',
                                                          'destination_change', 'paid_without_settlement')),
            rules_version   text NOT NULL,
            dedup_key       text NOT NULL CHECK (char_length(dedup_key) BETWEEN 1 AND 400),
            severity        text NOT NULL CHECK (severity IN ('high', 'medium')),
            title           text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 160),
            reason          text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 600),
            currency        char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            amount_minor    bigint NOT NULL CHECK (amount_minor >= 0),
            subject_refs    jsonb NOT NULL CHECK (jsonb_typeof(subject_refs) = 'array' AND jsonb_array_length(subject_refs) >= 1),
            compared_refs   jsonb NOT NULL CHECK (jsonb_typeof(compared_refs) = 'array'),
            evidence        jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
            party_id        uuid,
            destination_id  uuid,
            first_scan_id   uuid NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (business_id, id),
            UNIQUE (business_id, dedup_key),
            CHECK ((rule = 'destination_change') = (destination_id IS NOT NULL)),
            FOREIGN KEY (business_id, first_scan_id) REFERENCES fraud_scan (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, party_id) REFERENCES party (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, destination_id) REFERENCES payee_destination (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX fraud_flag_business_idx ON fraud_flag (business_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE fraud_flag_hold (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id  uuid NOT NULL,
            flag_id      uuid NOT NULL,
            proposal_id  uuid NOT NULL,
            scan_id      uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (flag_id, proposal_id),
            FOREIGN KEY (business_id, flag_id) REFERENCES fraud_flag (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, scan_id) REFERENCES fraud_scan (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX fraud_flag_hold_proposal_idx ON fraud_flag_hold (proposal_id)")
    op.execute(
        f"""
        CREATE TABLE fraud_review (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL,
            flag_id        uuid NOT NULL,
            decision       text NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'deferred')),
            reason         text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 300 AND {NO_LONG_NUMBER.format(col='reason')}),
            defer_until    date,
            verification   jsonb CHECK (verification IS NULL OR jsonb_typeof(verification) = 'object'),
            reviewer_id    uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            reviewer_role  text NOT NULL CHECK (reviewer_role IN ('owner', 'bookkeeper')),
            created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK ((decision = 'deferred') = (defer_until IS NOT NULL)),
            FOREIGN KEY (business_id, flag_id) REFERENCES fraud_flag (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX fraud_review_flag_idx ON fraud_review (flag_id, created_at)")
    op.execute(
        """
        CREATE FUNCTION fraud_flag_cleared(f uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM fraud_review WHERE flag_id = f AND decision = 'rejected')
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fraud_hold_active(p uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM fraud_flag_hold h WHERE h.proposal_id = p AND NOT fraud_flag_cleared(h.flag_id))
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION fraud_review_policy() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            fl fraud_flag%ROWTYPE;
            recorder uuid;
        BEGIN
            SELECT * INTO fl FROM fraud_flag WHERE id = NEW.flag_id AND business_id = NEW.business_id;
            IF EXISTS (SELECT 1 FROM fraud_review WHERE flag_id = NEW.flag_id AND decision IN ('confirmed', 'rejected')) THEN
                RAISE EXCEPTION 'this flag already has a final review' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.decision = 'rejected' THEN
                IF EXISTS (SELECT 1 FROM fraud_flag_hold WHERE flag_id = NEW.flag_id) THEN
                    IF NEW.reviewer_role <> 'owner' THEN
                        RAISE EXCEPTION 'only an owner can clear a flag that holds a payment' USING ERRCODE = 'check_violation';
                    END IF;
                    IF EXISTS (SELECT 1 FROM fraud_flag_hold h JOIN action_proposal p ON p.id = h.proposal_id
                               WHERE h.flag_id = NEW.flag_id AND p.proposed_by = NEW.reviewer_id) THEN
                        RAISE EXCEPTION 'the person who proposed a held payment cannot clear its hold' USING ERRCODE = 'check_violation';
                    END IF;
                END IF;
                IF fl.rule = 'destination_change' THEN
                    SELECT recorded_by INTO recorder FROM payee_destination WHERE id = fl.destination_id;
                    IF NEW.verification IS NULL OR NEW.verification ->> 'method' NOT IN ('called_known_contact', 'met_in_person')
                       OR (NEW.verification ->> 'contact_predates_change') IS DISTINCT FROM 'true' THEN
                        RAISE EXCEPTION 'a destination change is cleared only with independent verification' USING ERRCODE = 'check_violation';
                    END IF;
                    IF NEW.reviewer_id = recorder THEN
                        RAISE EXCEPTION 'the person who recorded the destination change cannot verify it' USING ERRCODE = 'check_violation';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER fraud_review_policy BEFORE INSERT ON fraud_review FOR EACH ROW EXECUTE FUNCTION fraud_review_policy()")
    op.execute(
        """
        CREATE FUNCTION action_decision_fraud_hold() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.event IN ('approved', 'executable') AND fraud_hold_active(NEW.proposal_id) THEN
                RAISE EXCEPTION 'this proposal is held by the fraud monitor until its flags are cleared' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER action_decision_fraud_hold BEFORE INSERT ON action_decision FOR EACH ROW EXECUTE FUNCTION action_decision_fraud_hold()")
    op.execute(
        """
        CREATE FUNCTION fraud_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for t in TABLES:
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION fraud_append_only()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION fraud_append_only()")


def downgrade() -> None:
    op.execute("DROP TRIGGER action_decision_fraud_hold ON action_decision")
    op.execute("DROP FUNCTION action_decision_fraud_hold()")
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE {t}")
    op.execute("DROP FUNCTION fraud_review_policy()")
    op.execute("DROP FUNCTION fraud_hold_active(uuid)")
    op.execute("DROP FUNCTION fraud_flag_cleared(uuid)")
    op.execute("DROP FUNCTION fraud_append_only()")

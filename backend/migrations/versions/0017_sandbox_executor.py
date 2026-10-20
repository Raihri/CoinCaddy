"""Sandbox Action Executor: one execution per approved proposal and an append-only, redacted request/result log.

- sandbox_execution: the claim that an approved, released proposal was dispatched to its sandbox adapter. It binds the
  caller's idempotency key (unique per business) and the proposal (unique: one execution per proposal), the payload
  hash that was approved, and the key sent to the mock service, which is derived from the proposal so that the mock
  processes a proposal at most once whatever key the caller used. Insert-only.
- sandbox_execution_log: every attempt with a timestamp: blocked (a pre-execution check failed; nothing was sent),
  dispatched, succeeded, rejected (the sandbox refused it; final), failed (no answer; the same key may retry), and
  replayed (a retry that returned the stored outcome without calling the adapter). Requests and results are stored
  redacted. Insert-only; at most one final outcome (succeeded or rejected) per execution.
- A trigger on both tables re-checks, in the database, what the API checks before anything is sent: the proposal was
  approved and released with this exact hash, the stored proposal still has that hash, it has not expired, and no
  uncleared Fraud Monitor flag holds it.
- fraud_scan accepts the trigger 'execution', because the fraud rules run again just before a payment is sent.

Revision ID: 0017_sandbox_executor
Revises: 0016_fraud_monitor
"""
from alembic import op

revision = "0017_sandbox_executor"
down_revision = "0016_fraud_monitor"
branch_labels = None
depends_on = None

ADAPTERS = "('reminder_send', 'application_submit', 'payment_initiate', 'packet_share')"
TABLES = ("sandbox_execution", "sandbox_execution_log")


def upgrade() -> None:
    op.execute("ALTER TABLE fraud_scan DROP CONSTRAINT fraud_scan_trigger_check")
    op.execute("""ALTER TABLE fraud_scan ADD CONSTRAINT fraud_scan_trigger_check
                  CHECK (trigger IN ('manual', 'proposal', 'approval', 'release', 'destination', 'execution'))""")
    op.execute(
        f"""
        CREATE TABLE sandbox_execution (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id       uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            proposal_id       uuid NOT NULL,
            idempotency_key   text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{{16,100}}$'),
            adapter           text NOT NULL CHECK (adapter IN {ADAPTERS}),
            payload_sha256    char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{{64}}$'),
            mock_key          char(64) NOT NULL CHECK (mock_key ~ '^[0-9a-f]{{64}}$'),
            request_sha256    char(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{{64}}$'),
            created_by        uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_role      text NOT NULL CHECK (created_role = 'owner'),
            created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (business_id, idempotency_key),
            UNIQUE (proposal_id),
            UNIQUE (mock_key),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE sandbox_execution_log (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            seq              bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            business_id      uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            proposal_id      uuid NOT NULL,
            execution_id     uuid,
            event            text NOT NULL CHECK (event IN ('blocked', 'dispatched', 'succeeded', 'rejected', 'failed', 'replayed')),
            adapter          text CHECK (adapter IS NULL OR adapter IN {ADAPTERS}),
            idempotency_key  text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{{16,100}}$'),
            actor_id         uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            actor_role       text NOT NULL,
            request          jsonb CHECK (request IS NULL OR jsonb_typeof(request) = 'object'),
            result           jsonb CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
            checks           jsonb CHECK (checks IS NULL OR jsonb_typeof(checks) = 'array'),
            http_status      integer CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
            reference        text CHECK (reference IS NULL OR reference ~ '^SBX-[A-Z]{{3}}-[0-9A-F]{{10}}$'),
            created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK ((event = 'blocked') = (execution_id IS NULL)),
            CHECK (event <> 'blocked' OR checks IS NOT NULL),
            CHECK (event <> 'dispatched' OR request IS NOT NULL),
            CHECK (event NOT IN ('succeeded', 'rejected', 'replayed') OR result IS NOT NULL),
            CHECK (event <> 'succeeded' OR reference IS NOT NULL),
            FOREIGN KEY (business_id, proposal_id) REFERENCES action_proposal (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, execution_id) REFERENCES sandbox_execution (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX sandbox_execution_log_business_idx ON sandbox_execution_log (business_id, seq DESC)")
    op.execute("CREATE INDEX sandbox_execution_log_proposal_idx ON sandbox_execution_log (proposal_id, seq)")
    op.execute("CREATE UNIQUE INDEX sandbox_execution_one_outcome ON sandbox_execution_log (execution_id) WHERE event IN ('succeeded', 'rejected')")
    op.execute(
        """
        CREATE FUNCTION sandbox_dispatch_guard(b uuid, pid uuid, h text, at timestamptz) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            p action_proposal%ROWTYPE;
        BEGIN
            SELECT * INTO p FROM action_proposal WHERE id = pid AND business_id = b;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'proposal not found in this business' USING ERRCODE = 'check_violation';
            END IF;
            IF h <> p.payload_sha256 THEN
                RAISE EXCEPTION 'execution hash does not match the proposal' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM action_decision WHERE proposal_id = pid AND event = 'approved' AND payload_sha256 = h)
               OR NOT EXISTS (SELECT 1 FROM action_decision WHERE proposal_id = pid AND event = 'executable' AND payload_sha256 = h) THEN
                RAISE EXCEPTION 'only an approved and released proposal can be executed' USING ERRCODE = 'check_violation';
            END IF;
            IF at >= p.expires_at THEN
                RAISE EXCEPTION 'proposal has expired' USING ERRCODE = 'check_violation';
            END IF;
            IF fraud_hold_active(pid) THEN
                RAISE EXCEPTION 'proposal is on hold in the Fraud Monitor' USING ERRCODE = 'check_violation';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION sandbox_execution_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM sandbox_dispatch_guard(NEW.business_id, NEW.proposal_id, NEW.payload_sha256, NEW.created_at);
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER sandbox_execution_guard BEFORE INSERT ON sandbox_execution FOR EACH ROW EXECUTE FUNCTION sandbox_execution_guard()")
    op.execute(
        """
        CREATE FUNCTION sandbox_log_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            e sandbox_execution%ROWTYPE;
        BEGIN
            IF NEW.execution_id IS NOT NULL THEN
                SELECT * INTO e FROM sandbox_execution WHERE id = NEW.execution_id;
                IF e.proposal_id <> NEW.proposal_id THEN
                    RAISE EXCEPTION 'log entry names a different proposal than its execution' USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.event = 'dispatched' THEN
                    IF EXISTS (SELECT 1 FROM sandbox_execution_log WHERE execution_id = NEW.execution_id AND event IN ('succeeded', 'rejected')) THEN
                        RAISE EXCEPTION 'this execution already has a final outcome' USING ERRCODE = 'check_violation';
                    END IF;
                    PERFORM sandbox_dispatch_guard(NEW.business_id, NEW.proposal_id, e.payload_sha256, NEW.created_at);
                END IF;
                IF NEW.event IN ('succeeded', 'rejected', 'failed') AND NOT EXISTS (
                    SELECT 1 FROM sandbox_execution_log WHERE execution_id = NEW.execution_id AND event = 'dispatched') THEN
                    RAISE EXCEPTION 'an outcome needs a dispatched request' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER sandbox_log_guard BEFORE INSERT ON sandbox_execution_log FOR EACH ROW EXECUTE FUNCTION sandbox_log_guard()")
    op.execute(
        """
        CREATE FUNCTION sandbox_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for t in TABLES:
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION sandbox_append_only()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} FOR EACH STATEMENT EXECUTE FUNCTION sandbox_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE sandbox_execution_log")
    op.execute("DROP TABLE sandbox_execution")
    op.execute("DROP FUNCTION sandbox_log_guard()")
    op.execute("DROP FUNCTION sandbox_execution_guard()")
    op.execute("DROP FUNCTION sandbox_dispatch_guard(uuid, uuid, text, timestamptz)")
    op.execute("DROP FUNCTION sandbox_append_only()")
    op.execute("ALTER TABLE fraud_scan DROP CONSTRAINT fraud_scan_trigger_check")
    op.execute("""ALTER TABLE fraud_scan ADD CONSTRAINT fraud_scan_trigger_check
                  CHECK (trigger IN ('manual', 'proposal', 'approval', 'release', 'destination'))""")

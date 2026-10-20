"""Smart Alerts: deterministic alerts and the runs that produced them.

An alert is derived state, not a ledger record: one open alert exists per dedup key (a partial unique
index enforces it, so repeated or concurrent runs cannot create a duplicate). When the condition stops
holding the alert is marked resolved; it is never deleted. Each run stores the hash of the frozen inputs
it evaluated and the minimal external notification text (no amounts, parties, or dates).

Revision ID: 0010_alerts
Revises: 0009_forecast_snapshot
"""
from alembic import op

revision = "0010_alerts"
down_revision = "0009_forecast_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE alert_run (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id        uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            as_of              date NOT NULL,
            rules_version      text NOT NULL,
            inputs_sha256      char(64) NOT NULL,
            created_count      integer NOT NULL CHECK (created_count >= 0),
            updated_count      integer NOT NULL CHECK (updated_count >= 0),
            unchanged_count    integer NOT NULL CHECK (unchanged_count >= 0),
            resolved_count     integer NOT NULL CHECK (resolved_count >= 0),
            notification_text  text,
            created_by         uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX alert_run_latest_idx ON alert_run (business_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE alert (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id        uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            alert_type         text NOT NULL CHECK (alert_type IN ('reserve_breach', 'overdue_invoice', 'unusual_outflow', 'repayment_pressure')),
            severity           text NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
            status             text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
            dedup_key          text NOT NULL CHECK (char_length(dedup_key) BETWEEN 1 AND 200),
            alert_date         date NOT NULL,
            amount_minor       bigint NOT NULL,
            currency           char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            cause              text NOT NULL CHECK (char_length(cause) BETWEEN 1 AND 500),
            record_ids         jsonb NOT NULL CHECK (jsonb_typeof(record_ids) = 'array'),
            details            jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
            first_run_id       uuid NOT NULL,
            last_run_id        uuid NOT NULL,
            detection_count    integer NOT NULL DEFAULT 1 CHECK (detection_count >= 1),
            first_detected_at  timestamptz NOT NULL DEFAULT now(),
            last_detected_at   timestamptz NOT NULL DEFAULT now(),
            resolved_at        timestamptz,
            CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
            UNIQUE (business_id, id),
            FOREIGN KEY (business_id, first_run_id) REFERENCES alert_run (business_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (business_id, last_run_id) REFERENCES alert_run (business_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX alert_one_open_per_key ON alert (business_id, dedup_key) WHERE status = 'open'")
    op.execute("CREATE INDEX alert_business_status_idx ON alert (business_id, status, alert_date)")
    op.execute(
        """
        CREATE FUNCTION alert_run_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'alert_run is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER alert_run_append_only BEFORE UPDATE OR DELETE ON alert_run FOR EACH ROW EXECUTE FUNCTION alert_run_append_only()")
    op.execute("CREATE TRIGGER alert_run_no_truncate BEFORE TRUNCATE ON alert_run FOR EACH STATEMENT EXECUTE FUNCTION alert_run_append_only()")


def downgrade() -> None:
    op.execute("DROP TABLE alert")
    op.execute("DROP TABLE alert_run")
    op.execute("DROP FUNCTION alert_run_append_only()")

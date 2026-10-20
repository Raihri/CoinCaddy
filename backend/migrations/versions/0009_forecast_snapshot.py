"""Cash Outlook: frozen, versioned forecast snapshots.

A snapshot stores the exact inputs it was calculated from (and their SHA-256), the calculation version,
and the results. It is append-only, so any figure shown later can be traced to, and recomputed from,
the inputs that produced it.

Revision ID: 0009_forecast_snapshot
Revises: 0008_classification
"""
from alembic import op

revision = "0009_forecast_snapshot"
down_revision = "0008_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE forecast_snapshot (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id    uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            calc_version   text NOT NULL,
            as_of          date NOT NULL,
            inputs         jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
            inputs_sha256  char(64) NOT NULL,
            results        jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
            created_by     uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX forecast_snapshot_latest_idx ON forecast_snapshot (business_id, created_at DESC)")
    op.execute(
        """
        CREATE FUNCTION forecast_snapshot_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'forecast_snapshot is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER forecast_snapshot_append_only BEFORE UPDATE OR DELETE ON forecast_snapshot FOR EACH ROW EXECUTE FUNCTION forecast_snapshot_append_only()"
    )
    op.execute(
        "CREATE TRIGGER forecast_snapshot_no_truncate BEFORE TRUNCATE ON forecast_snapshot FOR EACH STATEMENT EXECUTE FUNCTION forecast_snapshot_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE forecast_snapshot")
    op.execute("DROP FUNCTION forecast_snapshot_append_only()")

"""Region Pack: an append-only history of which region pack each business chose.

Revision ID: 0023_region_pack
Revises: 0022_finance_chat
"""
from alembic import op

revision = "0023_region_pack"
down_revision = "0022_finance_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE region_pack_selection (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id      uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            pack_id          text NOT NULL CHECK (pack_id ~ '^[a-z][a-z0-9-]{1,19}$'),
            pack_version     text NOT NULL CHECK (pack_version ~ '^region-[a-z0-9-]+-[0-9]{4}[.][0-9]{2}$'),
            previous_pack_id text CHECK (previous_pack_id IS NULL OR previous_pack_id ~ '^[a-z][a-z0-9-]{1,19}$'),
            selected_by      uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            selected_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute("CREATE INDEX region_pack_selection_latest_idx ON region_pack_selection (business_id, selected_at DESC, id)")
    op.execute("""
        CREATE FUNCTION region_pack_selection_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'region_pack_selection is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege'; END
        $$
    """)
    op.execute("CREATE TRIGGER region_pack_selection_insert_only BEFORE UPDATE OR DELETE ON region_pack_selection FOR EACH ROW EXECUTE FUNCTION region_pack_selection_insert_only()")
    op.execute("CREATE TRIGGER region_pack_selection_no_truncate BEFORE TRUNCATE ON region_pack_selection FOR EACH STATEMENT EXECUTE FUNCTION region_pack_selection_insert_only()")


def downgrade() -> None:
    op.execute("DROP TABLE region_pack_selection")
    op.execute("DROP FUNCTION region_pack_selection_insert_only()")

"""Business Access: local demo users, business memberships with roles, and server-side sessions.

Session tokens are random bearer secrets held in an HttpOnly cookie; only their SHA-256 hash is
stored. Business scope is never stored on the session: every request re-derives it from the
authenticated user's membership rows.

Revision ID: 0002_business_access
Revises: 0001_foundation
"""
from alembic import op

revision = "0002_business_access"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app_user (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email          text NOT NULL UNIQUE CHECK (email = lower(email)),
            display_name   text NOT NULL,
            password_hash  text NOT NULL,
            is_synthetic   boolean NOT NULL DEFAULT true,
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE membership (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            business_id  uuid NOT NULL REFERENCES business(id) ON DELETE RESTRICT,
            user_id      uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
            role         text NOT NULL CHECK (role IN ('owner', 'bookkeeper', 'staff')),
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (business_id, user_id),
            UNIQUE (business_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE auth_session (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash  char(64) NOT NULL UNIQUE,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            created_at  timestamptz NOT NULL DEFAULT now(),
            expires_at  timestamptz NOT NULL,
            revoked_at  timestamptz
        )
        """
    )
    op.execute("CREATE INDEX auth_session_user_idx ON auth_session (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE auth_session")
    op.execute("DROP TABLE membership")
    op.execute("DROP TABLE app_user")

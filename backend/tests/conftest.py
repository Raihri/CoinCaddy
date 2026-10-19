"""Point the whole test session at a separate `<dev db>_test` database, create it if needed, and migrate it.

Tests append audit events and create businesses, so they must never run against the demo database.
Override with TEST_DATABASE_URL; the database name must end in `_test`.
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
# Make the repo root importable so tests can load mock_services alongside the backend app.
sys.path.insert(0, str(BACKEND.parent))

from app.config import Settings, get_settings  # noqa: E402

_dev_url = make_url(Settings().database_url)
_test_url = make_url(os.environ.get("TEST_DATABASE_URL") or _dev_url.set(database=f"{_dev_url.database}_test"))
if not (_test_url.database or "").endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against non-test database {_test_url.database!r}")

os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
# Uploaded originals from tests go to their own private folder, never the demo one.
os.environ["STORAGE_DIR"] = str(BACKEND.parent / "storage" / "test-private")
get_settings.cache_clear()

_admin = create_engine(_test_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with _admin.connect() as conn:
    if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": _test_url.database}).scalar():
        conn.execute(text(f'CREATE DATABASE "{_test_url.database}"'))
_admin.dispose()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

_cfg = Config(str(BACKEND / "alembic.ini"))
_cfg.set_main_option("script_location", str(BACKEND / "migrations"))
command.upgrade(_cfg, "head")

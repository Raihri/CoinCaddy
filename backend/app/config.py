"""Runtime settings loaded from environment variables or the repository-level .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://smecopilot:change-me-local-only@127.0.0.1:5433/smecopilot"
    api_cors_origins: str = "http://localhost:5173"
    mock_services_url: str = "http://127.0.0.1:8100"
    # Shared password for the synthetic demo users created by the seed script. Local demo only.
    demo_user_password: str = "change-me-demo"
    # Key for the keyed hash of payee bank/wallet numbers (only the hash and last four digits are stored). Local demo only.
    destination_hash_key: str = "change-me-local-destination-key"
    # Private directory for original uploaded files; empty means <repo>/storage/private.
    storage_dir: str = ""
    # Finance Chat summary text: "template" (default, no model) or "claude" (optional; needs `pip install anthropic` and
    # ANTHROPIC_API_KEY). The model only drafts wording; every number comes from the app's calculations.
    finance_chat_assistant: str = "template"
    finance_chat_model: str = "claude-opus-5-5"

    @property
    def sqlalchemy_database_url(self) -> str:
        """Render exposes PostgreSQL URLs without a driver suffix; use the installed psycopg v3 driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

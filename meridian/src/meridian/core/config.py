"""Settings, read from the environment once and validated at import.

Every value that differs between a laptop, CI and Railway lives here. Nothing
else in the package reads ``os.environ``, so what the system needs to run is one
file rather than a scavenger hunt.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the backend needs from its environment.

    Read from ``meridian/.env``, a sibling of ``pyproject.toml``. Every Make
    target runs from this directory and Railway's root directory for both
    backend services points here, so the relative path is the one that holds
    everywhere. The frontend has its own ``ui/.env`` and shares nothing with
    this file — notably not the Supabase URL or anon key, which the backend
    never needs because it talks to Postgres directly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="",
        description="asyncpg connection string. Supabase in production, docker-compose locally.",
    )
    test_database_url: str = Field(
        default="postgresql://meridian:meridian@localhost:54329/meridian?sslmode=disable",
        description="Integration tests only. The local Postgres `make db` starts; no SSL.",
    )
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="")

    composio_api_key: str = Field(default="")
    composio_entity_id: str = Field(default="")

    temporal_address: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    temporal_task_queue: str = Field(default="meridian")

    def requires_database(self) -> str:
        """The connection string, or a clear failure if nothing configured one."""
        if not self.database_url:
            msg = "DATABASE_URL is not set; copy .env.example to .env and fill it in"
            raise RuntimeError(msg)
        return self.database_url

    def requires_test_database(self) -> str:
        """Where integration tests run — never the production database.

        The suite rolls every transaction back, so nothing it writes survives.
        That is not the protection that matters: a rollback cannot undo a
        migration, and ``make db`` applies migrations to whatever it is aimed
        at. So the guarantee is that the two are never the same database.
        """
        if not self.test_database_url:
            msg = "TEST_DATABASE_URL is not set; run `make db` for a local Postgres"
            raise RuntimeError(msg)
        if self.test_database_url == self.database_url:
            msg = (
                "TEST_DATABASE_URL is the same database as DATABASE_URL. "
                "Integration tests run against the local container from `make db`."
            )
            raise RuntimeError(msg)
        return self.test_database_url


@lru_cache
def settings() -> Settings:
    """The process-wide settings, read once."""
    return Settings()

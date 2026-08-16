"""Settings, read from the environment once and validated at import.

Every value that differs between a laptop, CI and Railway lives here. Nothing
else in the package reads ``os.environ``, so what the system needs to run is one
file rather than a scavenger hunt.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the process needs from its environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="",
        description="asyncpg connection string. Supabase in production, docker-compose locally.",
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


@lru_cache
def settings() -> Settings:
    """The process-wide settings, read once."""
    return Settings()

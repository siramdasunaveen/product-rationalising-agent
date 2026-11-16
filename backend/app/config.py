"""Application settings. Every variable uses the ``PRA_`` prefix (see ``.env.example``)."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root (pra-starter/.env); also accept backend/.env.
_ROOT = Path(__file__).resolve().parents[2]


class PlatformMode(StrEnum):
    STANDALONE = "standalone"
    STUDIO = "studio"


class AIProviderName(StrEnum):
    NONE = "none"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRA_",
        env_file=(str(_ROOT / ".env"), ".env"),
        extra="ignore",
    )

    platform_mode: PlatformMode = PlatformMode.STANDALONE
    environment: str = "dev"

    database_url: str = "postgresql+psycopg://pra:pra@localhost:5432/pra"
    test_database_url: str = "postgresql+psycopg://pra:pra@localhost:5432/pra_test"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = Field(default="dev-only-insecure-secret-change-me-0000000000", min_length=32)
    jwt_issuer: str = "pra-standalone"
    jwt_ttl_minutes: int = 480
    # Password for the demo users created by `python -m app.cli seed-demo-users` (standalone only).
    demo_password: str = ""

    fernet_key: str = ""

    ai_provider: AIProviderName = AIProviderName.NONE
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    ai_max_retries: int = 2

    storage_dir: Path = Path("./var/storage")
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_max_sheets: int = 20
    upload_max_rows: int = 100_000  # data rows per sheet
    upload_max_columns: int = 500
    # Zip-bomb guard: total unpacked size and number of parts inside an .xlsx.
    upload_max_unpacked_bytes: int = 200 * 1024 * 1024
    upload_max_parts: int = 2000

    cors_origins: str = "http://localhost:5173"  # comma-separated

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_standalone(self) -> bool:
        return self.platform_mode == PlatformMode.STANDALONE


@lru_cache
def get_settings() -> Settings:
    return Settings()

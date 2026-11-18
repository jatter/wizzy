from pathlib import Path
from uuid import UUID

from alembic.config import Config
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[3]


def migration_config():
    return Config(ROOT / "backend" / "alembic.ini")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIZZY_", env_file=ROOT / ".env", extra="ignore")

    database_url: str | None = Field(default=None, repr=False)
    app_password: str = Field(default="", repr=False)
    app_role: str = "wizzy_app"
    dispatcher_database_url: str | None = Field(default=None, repr=False)
    dispatcher_password: str = Field(default="", repr=False)
    dispatcher_role: str = "wizzy_app_dispatch"
    worker_database_url: str | None = Field(default=None, repr=False)
    worker_password: str = Field(default="", repr=False)
    worker_role: str = "wizzy_app_worker"
    broker_url: str = Field(default="redis://127.0.0.1:16379/0", repr=False)
    grading_queue: str = "wizzy.grading.v1"
    environment: str = "development"
    family_public_origin: str = ""
    passkey_android_origin: str = Field(default="", pattern=r"^(?:android:apk-key-hash:[A-Za-z0-9_-]{43})?$")
    storage_root: Path = ROOT / ".local" / "private-media"
    clamav_host: str | None = None
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout: float = Field(default=30, gt=0, le=60)
    clamav_database_timezone: str = 'UTC'
    platform_mfa_key: str = Field(default="", repr=False)
    model_secret_key: str = Field(default="", repr=False)
    session_hours: int = 24
    learning_device_days: int = Field(default=180, ge=1, le=365)
    monthly_page_limit: int = Field(default=100, ge=1)
    homework_list_model_id: UUID | None = None

    @property
    def db_url(self):
        if self.database_url:
            return self.database_url
        if not self.app_password:
            raise RuntimeError("Run `uv run python -m wizzy.bootstrap` before starting API")
        return URL.create("postgresql+psycopg", username=self.app_role, password=self.app_password,
                          host="127.0.0.1", port=5432, database="wizzy")

    @property
    def dispatcher_db_url(self):
        if self.dispatcher_database_url:
            return self.dispatcher_database_url
        if not self.dispatcher_password:
            raise RuntimeError("Run bootstrap before starting dispatcher")
        return URL.create("postgresql+psycopg", username=self.dispatcher_role,
                          password=self.dispatcher_password, host="127.0.0.1", port=5432, database="wizzy")

    @property
    def worker_db_url(self):
        if self.worker_database_url:
            return self.worker_database_url
        if not self.worker_password:
            raise RuntimeError("Run bootstrap before using execution store")
        return URL.create("postgresql+psycopg", username=self.worker_role,
                          password=self.worker_password, host="127.0.0.1", port=5432, database="wizzy")

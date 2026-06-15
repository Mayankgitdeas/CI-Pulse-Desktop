"""
config.py — Application configuration

Reads settings from environment variables (or a .env file in development).
All other modules import `settings` to access config values.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# Absolute path to project root (where this config.py file lives)
PROJECT_ROOT = Path(__file__).parent.resolve()
# Database always lives next to main.py, regardless of where python is run from
DB_FILE = PROJECT_ROOT / "pulse.db"


class Settings(BaseSettings):
    """All app configuration in one place."""

    # Database — uses DATABASE_URL env var on Render (PostgreSQL),
    # falls back to local SQLite for development
    database_url: str = f"sqlite:///{DB_FILE.as_posix()}"

    # Admin panel credentials — MUST set ADMIN_PASSWORD env var in production
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # Session signing — MUST set SESSION_SECRET env var in production
    session_secret: str = "please-change-this-to-a-random-long-string"

    # Server — Render provides PORT env var automatically
    host: str = "0.0.0.0"
    port: int = 8000

    # Logging
    log_level: str = "INFO"

    # Signal retention — older than this gets auto-pruned
    signal_retention_days: int = 90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()

# Render gives PostgreSQL URLs starting with "postgres://" but SQLAlchemy
# needs "postgresql://" — fix automatically
if settings.database_url.startswith("postgres://"):
    settings.database_url = settings.database_url.replace("postgres://", "postgresql://", 1)

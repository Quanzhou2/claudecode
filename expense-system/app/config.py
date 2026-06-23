"""Application configuration, loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root (the `expense-system/` dir). Used to anchor default file paths
# so the DB and uploads are the SAME regardless of the current working
# directory the server / seed script happens to be launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Core
    app_name: str = "报销管理系统"
    secret_key: str = "dev-secret-change-me"
    database_url: str = f"sqlite:///{_PROJECT_ROOT / 'expense.db'}"

    # Uploads
    upload_dir: str = str(_PROJECT_ROOT / "uploads")
    max_upload_mb: int = 10
    default_currency: str = "CNY"

    # Create demo accounts (admin/alice) automatically on startup when the
    # database has no users yet. Set AUTO_SEED=false to disable.
    auto_seed: bool = True

    # LLM (OpenAI-compatible)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_vision_model: str = "gpt-4o"

    @property
    def llm_enabled(self) -> bool:
        """True when an API key is configured, enabling live LLM calls."""
        return bool(self.llm_api_key.strip())

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()

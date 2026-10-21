"""Настройки: секреты из .env, поведение из config.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    bridge_api_key: str = ""
    groq_api_key: str = ""
    apify_token: str = ""
    telegram_bot_token: str = ""
    telegram_user_id: int = 0
    reels_proxy: str = ""

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def _empty_as_zero(cls, v):
        return 0 if v == "" else v


class AppConfig(BaseModel):
    vault_path: Path = Path("~/ReelsVault")
    data_dir: Path = Path("./data")
    downloaders: list[str] = ["ytdlp", "apify"]
    cookies_from_browser: str = "chrome"
    # Путь к cookies.txt (формат Netscape). Если задан — имеет приоритет над
    # cookies_from_browser и обходит проблемы расшифровки cookies браузера на macOS.
    cookies_file: str = ""
    analyzer: str = "gemini-video"
    gemini_model: str = "gemini-2.5-flash"
    # Локальный Aziz Bridge: OpenAI-compatible текстовый режим. Видео/картинки
    # остаются на Gemini, пока шлюз не получит поддержку мультимодального ввода.
    bridge_base_url: str = "http://127.0.0.1:8791/v1"
    bridge_model: str = "gpt-6.1-sol"
    bridge_timeout_sec: float = 190.0
    # Потолок выходных токенов: без него длинный транскрипт обрывает JSON (truncation).
    gemini_max_output_tokens: int = 16384
    # Бюджет «размышлений» thinking-модели. Ограничиваем — иначе мышление съедает
    # бюджет вывода (риск обрыва JSON) и биллится по output-цене. 0 = выключить.
    gemini_thinking_budget: int = 2048
    groq_whisper_model: str = "whisper-large-v3-turbo"
    gemini_price_per_1m_input: float = 0.30
    gemini_price_per_1m_output: float = 2.50
    pacing_sec: tuple[float, float] = (30, 60)
    max_cost_per_run: float = 2.0
    keep_video: bool = False
    max_attempts: int = 3

    @field_validator("vault_path", "data_dir", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        v = v.expanduser()
        if not v.is_absolute():
            v = PROJECT_ROOT / v
        return v


class Settings(BaseModel):
    secrets: Secrets
    cfg: AppConfig

    @property
    def proxy(self) -> Optional[str]:
        return self.secrets.reels_proxy or None

    @property
    def downloads_dir(self) -> Path:
        return self.cfg.data_dir / "downloads"

    @property
    def ledger_path(self) -> Path:
        return self.cfg.data_dir / "ledger.sqlite"

    @property
    def archive_path(self) -> Path:
        return self.cfg.data_dir / "archive.txt"

    @property
    def logs_dir(self) -> Path:
        return self.cfg.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.cfg.data_dir, self.downloads_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cfg_path = Path(os.environ.get("REELS_INBOX_CONFIG", PROJECT_ROOT / "config.yaml"))
    data = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    settings = Settings(secrets=Secrets(), cfg=AppConfig(**data))
    settings.ensure_dirs()
    return settings

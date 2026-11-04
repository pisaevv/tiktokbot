"""Настройки бота. Берутся из переменных окружения или файла .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str

    # Лимит Bot API на загрузку файла ботом. Считаем в десятичных мегабайтах,
    # чтобы оставить небольшой запас под точную границу Telegram.
    max_file_size_mb: int = 50

    # Сколько видео качаем одновременно. yt-dlp блокирующий, каждая загрузка
    # занимает поток, поэтому число небольшое.
    max_concurrent_downloads: int = 3

    # По умолчанию берём максимум качества, обычно это 1080p в H.265.
    # Включи, если видео нужно смотреть в Telegram Web или на старых Android.
    prefer_h264: bool = False

    cache_db: Path = Path("cache.sqlite3")

    # Куда класть временные файлы. Пусто означает системную temp-папку.
    download_dir: Path | None = None

    log_level: str = "INFO"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1_000_000

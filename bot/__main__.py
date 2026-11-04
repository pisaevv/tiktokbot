"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .cache import FileIdCache
from .config import Settings
from .downloader import Downloader
from .handlers import router

log = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram").setLevel(logging.WARNING)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    cache = FileIdCache(settings.cache_db)
    downloader = Downloader(
        max_bytes=settings.max_file_size_bytes,
        prefer_h264=settings.prefer_h264,
        max_concurrent=settings.max_concurrent_downloads,
        download_dir=settings.download_dir,
    )
    dp = Dispatcher(downloader=downloader, cache=cache)
    dp.include_router(router)

    try:
        me = await bot.get_me()
        log.info(
            "Запущен @%s, лимит файла %d МБ, prefer_h264=%s",
            me.username, settings.max_file_size_mb, settings.prefer_h264,
        )
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cache.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

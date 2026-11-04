"""Обработчики сообщений. Только личные чаты."""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message

from aiogram.utils.chat_action import ChatActionSender

from .cache import FileIdCache
from .downloader import Downloader, DownloaderError
from .links import extract_tiktok_urls, is_photo_url, normalize_url, video_id_from_url

log = logging.getLogger(__name__)

router = Router(name="tiktok")
router.message.filter(F.chat.type == ChatType.PRIVATE)

MAX_LINKS_PER_MESSAGE = 5
UPLOAD_TIMEOUT = 300

START_TEXT = (
    "Привет! Кидай ссылку на видео из TikTok, а я верну его без водяного знака "
    "в максимальном качестве.\n\n"
    "Подходят любые ссылки: из приложения, короткие vm.tiktok.com и обычные с сайта. "
    "Фото-посты не поддерживаю, только видео."
)
NO_LINK_TEXT = "Пришли ссылку на видео из TikTok, и я верну его без водяного знака."
PHOTO_TEXT = "Это фото-пост, а я умею только видео."
TELEGRAM_FAILED_TEXT = "Скачал видео, но Telegram не принял файл. Попробуй ещё раз чуть позже."


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(F.text | F.caption)
async def on_message(message: Message, downloader: Downloader, cache: FileIdCache) -> None:
    urls = _urls_from_message(message)
    if not urls:
        await message.answer(NO_LINK_TEXT)
        return
    for url in urls[:MAX_LINKS_PER_MESSAGE]:
        await _handle_link(message, url, downloader, cache)


def _urls_from_message(message: Message) -> list[str]:
    text = message.text or message.caption or ""
    urls = extract_tiktok_urls(text)
    for entity in message.entities or message.caption_entities or []:
        if entity.type == "text_link" and entity.url:
            urls.extend(extract_tiktok_urls(entity.url))
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


async def _send_cached(message: Message, video_id: str, cache: FileIdCache) -> bool:
    """Отправляет видео по file_id из кэша. Возвращает True, если получилось."""
    file_id = cache.get(video_id)
    if not file_id:
        return False
    try:
        await message.answer_video(file_id)
    except TelegramBadRequest as exc:
        log.warning("file_id для %s протух (%s), удаляю из кэша", video_id, exc)
        cache.delete(video_id)
        return False
    log.info("Отдал %s из кэша пользователю %s", video_id, message.from_user.id if message.from_user else "?")
    return True


async def _handle_link(message: Message, url: str, downloader: Downloader, cache: FileIdCache) -> None:
    user_id = message.from_user.id if message.from_user else "?"
    if is_photo_url(url):
        await message.reply(PHOTO_TEXT)
        return

    url = normalize_url(url)
    video_id = video_id_from_url(url)
    if video_id and await _send_cached(message, video_id, cache):
        return

    started = time.monotonic()
    async with ChatActionSender.upload_video(bot=message.bot, chat_id=message.chat.id):
        try:
            probe = await downloader.probe(url)
            if await _send_cached(message, probe.id, cache):
                return
            downloaded = await downloader.download(probe)
        except DownloaderError as exc:
            log.warning("Не скачал %s для %s: %s: %s", url, user_id, type(exc).__name__, exc)
            await message.reply(exc.user_message)
            return
        except Exception:
            log.exception("Неожиданная ошибка при скачивании %s для %s", url, user_id)
            await message.reply(DownloaderError.user_message)
            return

        try:
            sent = await message.answer_video(
                FSInputFile(downloaded.path, filename=f"tiktok_{probe.id}.mp4"),
                width=downloaded.width,
                height=downloaded.height,
                duration=probe.duration,
                supports_streaming=True,
                request_timeout=UPLOAD_TIMEOUT,
            )
        except TelegramAPIError as exc:
            log.error("Telegram не принял %s (%.1f МБ): %s", probe.id, downloaded.size / 1e6, exc)
            await message.reply(TELEGRAM_FAILED_TEXT)
            return
        finally:
            downloaded.cleanup()

    if sent.video:
        cache.put(probe.id, sent.video.file_id, downloaded.format_id)
    else:
        log.warning("Telegram вернул %s вместо video для %s", sent.content_type, probe.id)
    log.info(
        "Отправил %s пользователю %s: формат %s, %.1f МБ, %.1f с",
        probe.id, user_id, downloaded.format_id, downloaded.size / 1e6, time.monotonic() - started,
    )

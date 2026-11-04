"""Скачивание видео TikTok через yt-dlp.

Схема работы:
1. probe()    - один запрос к TikTok, получаем метаданные и список форматов.
2. download() - выбираем лучший формат без водяного знака, качаем во временную
                папку. Если файл не влез в лимит Telegram, исключаем этот формат
                и берём следующий по качеству. Повторных запросов к TikTok нет,
                форматы уже на руках.
3. После скачивания проверяем HEVC-файлы: Apple требует тег hvc1, а не hev1.
   Если нужно, ремуксим через ffmpeg без перекодирования.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from .links import is_photo_url

log = logging.getLogger(__name__)

# Сколько форматов подряд пробуем, пока не влезем в лимит размера.
MAX_ATTEMPTS = 4

# Сколько раз идём по редиректам url_result (короткие ссылки vm/vt/t).
MAX_URL_HOPS = 3

# Лучший формат со звуком и видео, без водяного знака и без неиграбельного
# bytevc2. Знак вопроса означает "не выкидывать формат, если поле не задано".
BASE_SELECTOR = "b[format_note!*=?watermark][format_note!*=?UNPLAYABLE][vcodec!=?none][acodec!=?none]"

_SAFE_FORMAT_ID = re.compile(r"^[\w.-]+$")


class DownloaderError(Exception):
    """Базовая ошибка. У каждого подкласса есть текст для пользователя."""

    user_message = "Не получилось скачать видео. Попробуй ещё раз чуть позже."


class UnsupportedLink(DownloaderError):
    user_message = "Не могу разобрать эту ссылку. Пришли ссылку на конкретное видео TikTok."


class NotAVideo(DownloaderError):
    user_message = "Это не видео. Фото-посты и слайдшоу я не поддерживаю, только видео."


class VideoUnavailable(DownloaderError):
    user_message = "Видео недоступно: оно удалено, приватное или закрыто для твоего региона."


class TooLarge(DownloaderError):
    user_message = (
        "Не нашёл подходящий вариант: все доступные версии видео больше лимита Telegram "
        "или не годятся для отправки."
    )


@dataclass(eq=False)
class Probe:
    """Результат разведки: метаданные и сырой info-словарь yt-dlp с форматами."""

    id: str
    title: str | None
    uploader: str | None
    width: int | None
    height: int | None
    duration: int | None
    webpage_url: str
    info: dict[str, Any]


@dataclass(eq=False)
class Downloaded:
    probe: Probe
    path: Path
    format_id: str
    size: int
    width: int | None
    height: int | None
    remuxed: bool = False
    _workdir: Path | None = None

    def cleanup(self) -> None:
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)


def build_selector(max_bytes: int, prefer_h264: bool = False, exclude: Iterable[str] = ()) -> str:
    """Собирает селектор формата yt-dlp.

    Размер проверяется только для форматов, у которых TikTok сообщил размер
    заранее. Остальные проверяем уже по факту скачивания.
    """
    selector = f"{BASE_SELECTOR}[filesize<=?{max_bytes}]"
    for format_id in exclude:
        if _SAFE_FORMAT_ID.match(format_id):
            selector += f"[format_id!={format_id}]"
        else:
            log.warning("Пропускаю исключение формата с подозрительным id %r", format_id)
    if prefer_h264:
        return f"{selector}[vcodec=h264]/{selector}"
    return selector


def translate_error(exc: Exception) -> DownloaderError:
    """Превращает ошибку yt-dlp в понятную пользователю."""
    message = str(exc).lower()
    if "unsupported url" in message or "not a valid url" in message:
        return UnsupportedLink(str(exc))
    if any(
        marker in message
        for marker in (
            "private",
            "log in",
            "login required",
            "not available",
            "unavailable",
            "404",
            "removed",
            "does not exist",
            "geo",
            "region",
            "unable to find video",
        )
    ):
        return VideoUnavailable(str(exc))
    return DownloaderError(str(exc))


def probe_from_info(info: dict[str, Any]) -> Probe:
    """Проверяет, что yt-dlp вернул именно видео, и собирает Probe."""
    formats = info.get("formats") or []
    if not formats:
        raise VideoUnavailable("yt-dlp вернул пустой список форматов")
    has_video = any(f.get("vcodec") not in (None, "none") for f in formats)
    if not has_video:
        raise NotAVideo("только аудио-форматы, похоже на слайдшоу")
    return Probe(
        id=str(info["id"]),
        title=info.get("title"),
        uploader=info.get("uploader"),
        width=info.get("width"),
        height=info.get("height"),
        duration=_as_int(info.get("duration")),
        webpage_url=info.get("webpage_url") or "",
        info=info,
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _YdlLogger:
    """Перенаправляет вывод yt-dlp в logging."""

    def debug(self, msg: str) -> None:
        # yt-dlp шлёт сюда и обычные информационные строки с префиксом [debug].
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


class Downloader:
    def __init__(
        self,
        *,
        max_bytes: int,
        prefer_h264: bool = False,
        max_concurrent: int = 3,
        download_dir: Path | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._prefer_h264 = prefer_h264
        self._download_dir = download_dir
        self._semaphore = asyncio.Semaphore(max_concurrent)
        if download_dir is not None:
            download_dir.mkdir(parents=True, exist_ok=True)

    # ---- публичный API -------------------------------------------------

    async def probe(self, url: str) -> Probe:
        async with self._semaphore:
            info = await asyncio.to_thread(self._extract_sync, url)
        return probe_from_info(info)

    async def download(self, probe: Probe) -> Downloaded:
        async with self._semaphore:
            downloaded = await asyncio.to_thread(self._download_sync, probe)
            try:
                downloaded.remuxed = await fix_hevc_tag(downloaded.path)
            except Exception:
                downloaded.cleanup()
                raise
            if downloaded.remuxed:
                downloaded.size = downloaded.path.stat().st_size
        return downloaded

    # ---- yt-dlp, блокирующая часть ---------------------------------------

    def _ydl_opts(self, **extra: Any) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "logger": _YdlLogger(),
            **extra,
        }

    def _extract_sync(self, url: str) -> dict[str, Any]:
        """Достаёт сырой info без выбора формата, сам разворачивает редиректы."""
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts()) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
                for _ in range(MAX_URL_HOPS):
                    if not info or info.get("_type") not in ("url", "url_transparent"):
                        break
                    next_url = info["url"]
                    if is_photo_url(next_url):
                        raise NotAVideo(f"короткая ссылка ведёт на фото-пост: {next_url}")
                    info = ydl.extract_info(
                        next_url, download=False, process=False, ie_key=info.get("ie_key")
                    )
        except YoutubeDLError as exc:
            raise translate_error(exc) from exc
        if not info or info.get("_type") in ("url", "url_transparent"):
            raise UnsupportedLink("не удалось развернуть ссылку")
        if info.get("_type") == "playlist":
            raise UnsupportedLink("ссылка ведёт на список, а не на видео")
        return info

    def _download_sync(self, probe: Probe) -> Downloaded:
        workdir = Path(tempfile.mkdtemp(prefix="tiktok_", dir=self._download_dir))
        exclude: list[str] = []
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                selector = build_selector(self._max_bytes, self._prefer_h264, exclude)
                path, format_id, width, height = self._run_download(probe.info, selector, workdir)
                size = path.stat().st_size
                if size <= self._max_bytes:
                    log.info(
                        "Скачал %s: формат %s, %dx%s, %.1f МБ, попытка %d",
                        probe.id, format_id, width or 0, height or 0, size / 1e6, attempt,
                    )
                    return Downloaded(
                        probe=probe,
                        path=path,
                        format_id=format_id,
                        size=size,
                        width=width,
                        height=height,
                        _workdir=workdir,
                    )
                log.info(
                    "Формат %s для %s весит %.1f МБ, больше лимита, пробую следующий",
                    format_id, probe.id, size / 1e6,
                )
                path.unlink(missing_ok=True)
                exclude.append(format_id)
            raise TooLarge(f"{MAX_ATTEMPTS} форматов подряд не влезли в лимит")
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    def _run_download(
        self, info: dict[str, Any], selector: str, workdir: Path
    ) -> tuple[Path, str, int | None, int | None]:
        """Один проход yt-dlp по уже извлечённому info с заданным селектором."""
        opts = self._ydl_opts(
            format=selector,
            outtmpl=str(workdir / "%(id)s.%(ext)s"),
            overwrites=True,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                result = ydl.process_ie_result(copy.deepcopy(info), download=True)
        except YoutubeDLError as exc:
            if "requested format is not available" in str(exc).lower():
                raise TooLarge(str(exc)) from exc
            raise translate_error(exc) from exc
        try:
            path = Path(result["requested_downloads"][0]["filepath"])
        except (KeyError, IndexError, TypeError) as exc:
            raise DownloaderError("yt-dlp не сообщил путь к файлу") from exc
        return path, str(result.get("format_id")), result.get("width"), result.get("height")


# ---- HEVC: hev1 -> hvc1 ------------------------------------------------------


async def probe_codec_tag(path: Path) -> tuple[str | None, str | None]:
    """(codec_name, codec_tag_string) первой видеодорожки по данным ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,codec_tag_string",
        "-of", "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("ffprobe завершился с кодом %s: %s", proc.returncode, err.decode(errors="replace").strip())
        return None, None
    streams = json.loads(out or b"{}").get("streams") or []
    if not streams:
        return None, None
    return streams[0].get("codec_name"), streams[0].get("codec_tag_string")


async def fix_hevc_tag(path: Path) -> bool:
    """Если файл HEVC с тегом hev1, перемаркирует в hvc1 без перекодирования.

    Возвращает True, если файл был изменён. Без ffmpeg просто пропускает шаг.
    """
    try:
        codec, tag = await probe_codec_tag(path)
    except FileNotFoundError:
        log.warning("ffprobe не найден, пропускаю проверку тега HEVC")
        return False
    if codec != "hevc" or tag == "hvc1":
        return False
    tmp = path.with_name(f"{path.stem}.hvc1{path.suffix}")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y", "-loglevel", "error",
        "-i", str(path),
        "-c", "copy",
        "-tag:v", "hvc1",
        "-movflags", "+faststart",
        str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("ffmpeg не смог ремуксить %s: %s", path.name, err.decode(errors="replace").strip())
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(path)
    log.info("Перемаркировал %s: %s -> hvc1", path.name, tag)
    return True

"""Распознавание и нормализация ссылок TikTok."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TIKTOK_HOST_RE = re.compile(r"^(?:[\w-]+\.)*tiktok\.com$", re.IGNORECASE)
_VIDEO_ID_RE = re.compile(r"/(?:@(?P<user>[\w.-]+)/video|share/video|embed|v)/(?P<id>\d+)")
_PHOTO_RE = re.compile(r"/@[\w.-]+/photo/\d+")
_TRAILING_PUNCT = ".,;:!?)]}>\"'»"


def extract_tiktok_urls(text: str) -> list[str]:
    """Все ссылки на tiktok.com из текста, без дублей, в порядке появления."""
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if is_tiktok_url(url) and url not in urls:
            urls.append(url)
    return urls


def is_tiktok_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return bool(_TIKTOK_HOST_RE.match(host))


def is_photo_url(url: str) -> bool:
    """Ссылка на фото-пост вида /@user/photo/123."""
    return bool(_PHOTO_RE.search(urlsplit(url).path))


def video_id_from_url(url: str) -> str | None:
    """Числовой id видео, если он есть прямо в ссылке. Для коротких ссылок None."""
    match = _VIDEO_ID_RE.search(urlsplit(url).path)
    return match.group("id") if match else None


def normalize_url(url: str) -> str:
    """Приводит ссылку с id видео к каноническому виду, который понимает yt-dlp.

    Покрывает tiktok.com без www, m.tiktok.com/v/<id>.html и хвосты из
    query-параметров. Короткие ссылки vm/vt/t возвращаются как есть,
    их разворачивает сам yt-dlp.
    """
    match = _VIDEO_ID_RE.search(urlsplit(url).path)
    if not match:
        return url
    user = match.group("user") or "_"
    return f"https://www.tiktok.com/@{user}/video/{match.group('id')}"

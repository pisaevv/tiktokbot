"""Кэш file_id: один раз отправили видео в Telegram, дальше отдаём по id мгновенно."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class FileIdCache:
    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id   TEXT PRIMARY KEY,
                file_id    TEXT NOT NULL,
                format_id  TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, video_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT file_id FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row[0] if row else None

    def put(self, video_id: str, file_id: str, format_id: str | None = None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO videos (video_id, file_id, format_id, created_at) VALUES (?, ?, ?, ?)",
            (video_id, file_id, format_id, int(time.time())),
        )
        self._conn.commit()

    def delete(self, video_id: str) -> None:
        self._conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

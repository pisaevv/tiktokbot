import asyncio
import copy
import shutil
import subprocess
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from bot.downloader import (
    MAX_ATTEMPTS,
    Downloader,
    NotAVideo,
    Probe,
    TooLarge,
    UnsupportedLink,
    VideoUnavailable,
    build_selector,
    fix_hevc_tag,
    probe_codec_tag,
    probe_from_info,
    translate_error,
)

MB = 1_000_000


def fmt(format_id, **extra):
    base = {
        "format_id": format_id,
        "url": f"https://v.example.com/{format_id}.mp4",
        "ext": "mp4",
        "acodec": "aac",
        "preference": -1,
    }
    base.update(extra)
    return base


# Набор форматов, похожий на то, что отдаёт экстрактор TikTok в yt-dlp.
FORMATS = [
    fmt("download_addr", vcodec="h264", width=1080, height=1920, tbr=5000,
        format_note="Download video, watermarked", preference=-2),
    fmt("bytevc1_1080p_3000", vcodec="h265", width=1080, height=1920, tbr=3000, format_note="Playback video"),
    fmt("h264_720p_2500", vcodec="h264", width=720, height=1280, tbr=2500, format_note="Playback video"),
    fmt("h264_540p_1200", vcodec="h264", width=576, height=1024, tbr=1200, format_note="Playback video"),
    fmt("bytevc2_1080p_2000", vcodec="bytevc2", width=1080, height=1920, tbr=2000,
        format_note="Playback video (UNPLAYABLE)", preference=-100),
    fmt("hvc1_video_only", vcodec="h265", acodec="none", width=1080, height=1920, tbr=3500),
    fmt("audio", ext="m4a", vcodec="none"),
]


def make_info(formats):
    return {
        "id": "7000000000000000001",
        "title": "test",
        "extractor": "tiktok",
        "webpage_url": "https://www.tiktok.com/@a/video/7000000000000000001",
        "formats": copy.deepcopy(formats),
        "_format_sort_fields": ("quality", "codec", "size", "br"),
    }


def select(selector, formats=FORMATS):
    opts = {"format": selector, "quiet": True, "simulate": True, "no_warnings": True}
    with YoutubeDL(opts) as ydl:
        return ydl.process_ie_result(make_info(formats), download=False)["format_id"]


def test_selector_takes_best_playable_without_watermark():
    assert select(build_selector(50 * MB)) == "bytevc1_1080p_3000"


def test_selector_prefers_h264_when_asked():
    assert select(build_selector(50 * MB, prefer_h264=True)) == "h264_720p_2500"


def test_selector_falls_back_to_any_codec_when_no_h264():
    only_h265 = [f for f in FORMATS if f.get("vcodec") != "h264"]
    assert select(build_selector(50 * MB, prefer_h264=True), only_h265) == "bytevc1_1080p_3000"


def test_selector_exclusions_descend_in_quality():
    assert select(build_selector(50 * MB, exclude=["bytevc1_1080p_3000"])) == "h264_720p_2500"
    assert select(build_selector(50 * MB, exclude=["bytevc1_1080p_3000", "h264_720p_2500"])) == "h264_540p_1200"


def test_selector_skips_known_oversized_but_keeps_unknown_size():
    formats = copy.deepcopy(FORMATS)
    for f in formats:
        if f["format_id"] == "bytevc1_1080p_3000":
            f["filesize"] = 80 * MB
    assert select(build_selector(50 * MB), formats) == "h264_720p_2500"


def test_selector_keeps_format_without_note():
    formats = [fmt("play", vcodec="h264", width=720, height=1280), fmt("audio", ext="m4a", vcodec="none")]
    assert select(build_selector(50 * MB), formats) == "play"


def test_selector_raises_when_nothing_fits():
    formats = [fmt("download", vcodec="h264", width=720, height=1280, format_note="watermarked", preference=-2)]
    # process_ie_result бросает ExtractorError, а не DownloadError; ловим общий базовый класс,
    # как это делает Downloader._run_download.
    with pytest.raises(YoutubeDLError, match="Requested format is not available"):
        select(build_selector(50 * MB), formats)


def test_build_selector_ignores_suspicious_format_ids():
    selector = build_selector(10, exclude=["ok_id", "bad id]"])
    assert "[format_id!=ok_id]" in selector
    assert "bad id" not in selector


def test_probe_from_info_rejects_audio_only_and_empty():
    with pytest.raises(NotAVideo):
        probe_from_info(make_info([fmt("audio", ext="mp3", vcodec="none")]))
    with pytest.raises(VideoUnavailable):
        probe_from_info(make_info([]))
    probe = probe_from_info({**make_info(FORMATS), "duration": 12.7, "width": 1080, "height": 1920})
    assert probe.id == "7000000000000000001"
    assert probe.duration == 12
    assert probe.height == 1920


def test_translate_error_mapping():
    assert isinstance(translate_error(Exception("ERROR: Unsupported URL: https://x")), UnsupportedLink)
    assert isinstance(translate_error(Exception("ERROR: [TikTok] 1: Video not available")), VideoUnavailable)
    assert isinstance(translate_error(Exception("ERROR: This video is private")), VideoUnavailable)
    generic = translate_error(Exception("ERROR: something odd"))
    assert type(generic).__name__ == "DownloaderError"


class FakeRuns:
    """Подменяет _run_download: отдаёт файлы заданных размеров по очереди."""

    def __init__(self, sizes):
        self.sizes = list(sizes)
        self.selectors = []

    def __call__(self, info, selector, workdir: Path):
        self.selectors.append(selector)
        size = self.sizes.pop(0)
        path = workdir / f"{len(self.selectors)}.mp4"
        path.write_bytes(b"\0" * size)
        return path, f"fmt{len(self.selectors)}", 720, 1280


def run(coro):
    return asyncio.run(coro)


def make_probe():
    return Probe(id="1", title=None, uploader=None, width=None, height=None, duration=None,
                 webpage_url="", info=make_info(FORMATS))


def test_download_retries_until_file_fits(tmp_path, monkeypatch):
    downloader = Downloader(max_bytes=100, download_dir=tmp_path)
    runs = FakeRuns([300, 200, 90])
    monkeypatch.setattr(downloader, "_run_download", runs)
    monkeypatch.setattr("bot.downloader.fix_hevc_tag", lambda path: _async(False))

    downloaded = run(downloader.download(make_probe()))
    try:
        assert downloaded.size == 90
        assert downloaded.format_id == "fmt3"
        assert "[format_id!=fmt1][format_id!=fmt2]" in runs.selectors[-1]
        assert downloaded.path.exists()
    finally:
        downloaded.cleanup()
    assert not downloaded.path.exists()


def test_download_gives_up_after_max_attempts(tmp_path, monkeypatch):
    downloader = Downloader(max_bytes=100, download_dir=tmp_path)
    runs = FakeRuns([500] * MAX_ATTEMPTS)
    monkeypatch.setattr(downloader, "_run_download", runs)

    with pytest.raises(TooLarge):
        run(downloader.download(make_probe()))
    assert len(runs.selectors) == MAX_ATTEMPTS
    assert list(tmp_path.iterdir()) == []  # временная папка убрана


def _async(value):
    async def coro():
        return value
    return coro()


HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


@pytest.fixture
def hevc_file(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("нет ffmpeg")
    path = tmp_path / "clip.mp4"
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5:r=10",
         "-c:v", "libx265", "-x265-params", "log-level=none", "-tag:v", "hev1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"ffmpeg не собрал HEVC: {result.stderr.strip()}")
    return path


def test_fix_hevc_tag_remuxes_hev1_to_hvc1(hevc_file):
    assert run(probe_codec_tag(hevc_file)) == ("hevc", "hev1")
    assert run(fix_hevc_tag(hevc_file)) is True
    assert run(probe_codec_tag(hevc_file)) == ("hevc", "hvc1")
    # повторный вызов ничего не делает
    assert run(fix_hevc_tag(hevc_file)) is False


def test_fix_hevc_tag_leaves_h264_alone(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("нет ffmpeg")
    path = tmp_path / "h264.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5:r=10",
         "-c:v", "libx264", str(path)],
        check=True, capture_output=True,
    )
    before = path.read_bytes()
    assert run(fix_hevc_tag(path)) is False
    assert path.read_bytes() == before

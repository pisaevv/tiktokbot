from bot.links import (
    extract_tiktok_urls,
    is_photo_url,
    is_tiktok_url,
    normalize_url,
    video_id_from_url,
)


def test_extracts_links_and_strips_trailing_punctuation():
    text = (
        "смотри https://www.tiktok.com/@user.name/video/7123456789012345678?_t=abc&_r=1, "
        "и ещё (https://vm.tiktok.com/ZMabcDEF/). А это не тикток: https://example.com/x"
    )
    assert extract_tiktok_urls(text) == [
        "https://www.tiktok.com/@user.name/video/7123456789012345678?_t=abc&_r=1",
        "https://vm.tiktok.com/ZMabcDEF/",
    ]


def test_dedupes_links():
    url = "https://vt.tiktok.com/ZSe4FqkKd/"
    assert extract_tiktok_urls(f"{url} {url}") == [url]


def test_is_tiktok_url_matches_known_hosts_only():
    assert is_tiktok_url("https://tiktok.com/@a/video/1")
    assert is_tiktok_url("https://m.tiktok.com/v/1.html")
    assert is_tiktok_url("https://www.tiktok.com/t/ZTRC5xgJp")
    assert not is_tiktok_url("https://nottiktok.com/@a/video/1")
    assert not is_tiktok_url("https://tiktok.com.evil.net/@a/video/1")


def test_photo_detection():
    assert is_photo_url("https://www.tiktok.com/@user/photo/7234567890123456789")
    assert not is_photo_url("https://www.tiktok.com/@user/video/7234567890123456789")
    assert not is_photo_url("https://vm.tiktok.com/ZMabcDEF/")


def test_video_id_from_url():
    assert video_id_from_url("https://www.tiktok.com/@user/video/7234567890123456789?x=1") == "7234567890123456789"
    assert video_id_from_url("https://m.tiktok.com/v/7234567890123456789.html") == "7234567890123456789"
    assert video_id_from_url("https://www.tiktok.com/share/video/7234567890123456789") == "7234567890123456789"
    assert video_id_from_url("https://vm.tiktok.com/ZMabcDEF/") is None


def test_normalize_url_builds_canonical_form():
    assert normalize_url("https://tiktok.com/@user/video/7234567890123456789?_t=1") == (
        "https://www.tiktok.com/@user/video/7234567890123456789"
    )
    assert normalize_url("https://m.tiktok.com/v/7234567890123456789.html") == (
        "https://www.tiktok.com/@_/video/7234567890123456789"
    )
    short = "https://vm.tiktok.com/ZMabcDEF/"
    assert normalize_url(short) == short

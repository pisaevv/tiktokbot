from bot.cache import FileIdCache


def test_roundtrip_and_overwrite(tmp_path):
    cache = FileIdCache(tmp_path / "cache.sqlite3")
    try:
        assert cache.get("1") is None
        cache.put("1", "file_a", "h264_720p")
        assert cache.get("1") == "file_a"
        cache.put("1", "file_b")
        assert cache.get("1") == "file_b"
        cache.delete("1")
        assert cache.get("1") is None
    finally:
        cache.close()


def test_persists_between_instances(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = FileIdCache(path)
    first.put("42", "file_42")
    first.close()
    second = FileIdCache(path)
    try:
        assert second.get("42") == "file_42"
    finally:
        second.close()

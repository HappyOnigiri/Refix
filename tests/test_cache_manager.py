"""Unit tests for cache_manager."""

import json

import cache_manager


def test_cache_round_trip_io(tmp_path):
    cache_file = tmp_path / ".refix_cache.json"
    expected = {
        "owner/repo-A": {
            "12": "2026-03-10T15:30:00Z",
            "15": "2026-03-11T09:00:00Z",
        },
        "owner/repo-B": {"3": "2026-03-01T10:00:00Z"},
    }

    cache_manager.save_cache(expected, cache_file)
    loaded = cache_manager.load_cache(cache_file)

    assert loaded == expected


def test_load_cache_missing_file_returns_empty(tmp_path):
    cache_file = tmp_path / ".refix_cache.json"

    loaded = cache_manager.load_cache(cache_file)

    assert loaded == {}


def test_load_cache_invalid_json_returns_empty_with_warning(tmp_path, capsys):
    cache_file = tmp_path / ".refix_cache.json"
    cache_file.write_text(
        '{"owner/repo": {"1": "2026-03-10T00:00:00Z"', encoding="utf-8"
    )

    loaded = cache_manager.load_cache(cache_file)

    assert loaded == {}
    err = capsys.readouterr().err
    assert "Warning: invalid cache JSON" in err


def test_load_cache_schema_mismatch_returns_empty_with_warning(tmp_path, capsys):
    cache_file = tmp_path / ".refix_cache.json"
    cache_file.write_text(json.dumps(["unexpected", "list"]), encoding="utf-8")

    loaded = cache_manager.load_cache(cache_file)

    assert loaded == {}
    err = capsys.readouterr().err
    assert "Warning: cache schema mismatch" in err


def test_set_and_get_cached_updated_at():
    cache: cache_manager.CacheData = {}

    cache_manager.set_cached_updated_at(cache, "owner/repo", 42, "2026-03-12T00:00:00Z")

    assert (
        cache_manager.get_cached_updated_at(cache, "owner/repo", "42")
        == "2026-03-12T00:00:00Z"
    )

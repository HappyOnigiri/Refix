#!/usr/bin/env python3
"""Local cache manager for Refix early-skip optimization."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypeAlias

CacheData: TypeAlias = dict[str, dict[str, str]]
DEFAULT_CACHE_PATH = Path(".refix_cache.json")


def _is_valid_cache_data(data: object) -> bool:
    """Return True when data matches CacheData schema exactly."""
    if not isinstance(data, dict):
        return False
    for repo, repo_cache in data.items():
        if not isinstance(repo, str):
            return False
        if not isinstance(repo_cache, dict):
            return False
        for pr_number, updated_at in repo_cache.items():
            if not isinstance(pr_number, str):
                return False
            if not isinstance(updated_at, str):
                return False
    return True


def load_cache(path: Path | str = DEFAULT_CACHE_PATH) -> CacheData:
    """Load local PR updatedAt cache; fallback to empty dict on invalid input."""
    cache_path = Path(path)
    try:
        raw = cache_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        print(
            f"Warning: failed to read cache file {cache_path}: {exc}. Resetting cache.",
            file=sys.stderr,
        )
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"Warning: invalid cache JSON in {cache_path}: {exc}. Resetting cache.",
            file=sys.stderr,
        )
        return {}

    if not _is_valid_cache_data(parsed):
        print(
            f"Warning: cache schema mismatch in {cache_path}. Resetting cache.",
            file=sys.stderr,
        )
        return {}

    return {repo: dict(repo_cache) for repo, repo_cache in parsed.items()}


def save_cache(cache: CacheData, path: Path | str = DEFAULT_CACHE_PATH) -> None:
    """Persist local PR updatedAt cache to disk."""
    if not _is_valid_cache_data(cache):
        raise ValueError("Invalid cache data schema")
    cache_path = Path(path)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_cached_updated_at(
    cache: CacheData,
    repo: str,
    pr_number: int | str,
) -> str | None:
    """Return cached updatedAt for repo/pr."""
    repo_cache = cache.get(repo)
    if not isinstance(repo_cache, dict):
        return None
    return repo_cache.get(str(pr_number))


def set_cached_updated_at(
    cache: CacheData,
    repo: str,
    pr_number: int | str,
    updated_at: str | None,
) -> None:
    """Upsert updatedAt cache entry for repo/pr when value is present."""
    if not updated_at:
        return
    repo_cache = cache.setdefault(repo, {})
    repo_cache[str(pr_number)] = updated_at

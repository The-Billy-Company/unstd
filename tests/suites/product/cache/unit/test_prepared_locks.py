"""Canonical identities and file hashes are prepared once, outside hot calls."""

from __future__ import annotations

from pathlib import Path

from unstd.cache import Cache, locking


def test_cache_resolves_database_identity_only_once(tmp_path, monkeypatch):
    resolutions = []
    resolve = Path.resolve

    def observed(path, *arguments, **keywords):
        resolutions.append(path)
        return resolve(path, *arguments, **keywords)

    monkeypatch.setattr(Path, "resolve", observed)
    cache = Cache(tmp_path / "cache.sqlite3")
    assert len(resolutions) == 1
    cache.set("one", 1)
    cache.set_many({"two": 2, "three": 3})
    assert cache.get("one") == 1
    assert cache.get_many(["two", "three"]) == {"two": 2, "three": 3}
    assert cache.get_or_compute("four", lambda: 4) == 4
    assert cache.get_or_compute("four", lambda: 5) == 4
    assert cache.delete("one") is True
    assert cache.clear() == 3
    assert len(resolutions) == 1


def test_database_file_hash_is_reused_across_mutations_and_fills(tmp_path, monkeypatch):
    paths = []
    digest = locking.sha256

    def observed(raw=b""):
        if raw:
            paths.append(raw)
        return digest(raw)

    monkeypatch.setattr(locking, "sha256", observed)
    cache = Cache(tmp_path / "cache.sqlite3")
    assert len(paths) == 1
    cache.set("one", 1)
    cache.set_many({"two": 2})
    cache.get_or_compute("three", lambda: 3)
    cache.delete("two")
    cache.clear()
    assert len(paths) == 1

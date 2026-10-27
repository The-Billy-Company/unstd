"""Persistent JSON, explicit expiry, namespace isolation, and FIFO cache laws."""

from __future__ import annotations

import os
import sqlite3
import stat
import time

import pytest

from unstd.cache import Cache


@pytest.fixture
def clock(monkeypatch):
    current = [1_000.0]
    monkeypatch.setattr(time, "time", lambda: current[0])
    return current


@pytest.mark.parametrize(
    "value",
    [None, False, 0, 1.25, "", "café\ud800", [], {}, {"rows": [1, None, True]}],
)
def test_values_survive_reopening(tmp_path, value):
    path = tmp_path / "cache.sqlite3"
    Cache(path).set("value", value)
    assert Cache(str(path)).get("value", "missing") == value


def test_missing_returns_exact_default_and_null_is_a_hit(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    sentinel = object()
    assert cache.get("absent", sentinel) is sentinel
    cache.set("null", None)
    assert cache.get("null", sentinel) is None
    assert (
        cache.get_or_compute("null", lambda: pytest.fail("cached null recomputed"))
        is None
    )


def test_reads_are_independent_decoded_values(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    source = {"rows": [{"name": "original"}]}
    cache.set("snapshot", source)
    source["rows"][0]["name"] = "source mutated"
    first = cache.get("snapshot")
    first["rows"][0]["name"] = "read mutated"
    assert cache.get("snapshot") == {"rows": [{"name": "original"}]}


def test_cold_and_warm_compute_results_have_same_json_normalization(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    calls = []

    def produce():
        calls.append("computed")
        return {1: ("value", 2)}

    cold = cache.get_or_compute("normalized", produce)
    warm = cache.get_or_compute("normalized", produce)
    assert cold == warm == {"1": ["value", 2]}
    assert calls == ["computed"]


def test_namespaces_and_arbitrary_sql_shaped_keys_are_bound_values(tmp_path):
    path = tmp_path / "cache.sqlite3"
    namespace = "x'; DROP TABLE _unstd_cache_v1; --"
    key = "a' OR 1=1 --\x00é"
    first = Cache(path, namespace=namespace)
    second = Cache(path, namespace="other")
    first.set(key, {"owner": "first"})
    second.set(key, {"owner": "second"})
    assert first.get(key) == {"owner": "first"}
    assert second.get(key) == {"owner": "second"}
    assert first.delete(key) is True
    assert first.delete(key) is False
    assert second.get(key) == {"owner": "second"}


def test_clear_counts_only_its_own_namespace(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = Cache(path)
    second = Cache(path, namespace="other")
    first.set("one", 1)
    first.set("two", 2)
    second.set("one", 3)
    assert first.clear() == 2
    assert first.clear() == 0
    assert first.get("one") is None
    assert second.get("one") == 3


@pytest.mark.parametrize("operation", ["set", "get_or_compute"])
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), {"nested": -float("inf")}]
)
def test_nonfinite_values_are_rejected_without_a_partial_entry(
    tmp_path, operation, value
):
    cache = Cache(tmp_path / "cache.sqlite3")
    write = (
        (lambda: cache.set("invalid", value))
        if operation == "set"
        else (lambda: cache.get_or_compute("invalid", lambda: value))
    )
    with pytest.raises(ValueError, match=r"JSON compliant|range"):
        write()
    assert cache.get("invalid", "missing") == "missing"
    cache.set("valid", 1)
    assert cache.get("valid") == 1


def test_encoding_failure_preserves_prior_committed_value(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("value", {"committed": True})
    with pytest.raises(TypeError):
        cache.set("value", object())
    assert cache.get("value") == {"committed": True}


def test_expiry_boundary_and_zero_ttl(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("short", "present", ttl=10)
    cache.set("forever", "retained")
    cache.set("zero", "already expired", ttl=0)
    assert cache.get("zero", "missing") == "missing"
    clock[0] = 1_009.999
    assert cache.get("short") == "present"
    clock[0] = 1_010.0
    assert cache.get("short", "missing") == "missing"
    assert cache.get("forever") == "retained"


def test_delete_and_clear_include_expired_physical_rows(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("delete_expired", 1, ttl=1)
    cache.set("clear_expired", 2, ttl=1)
    cache.set("clear_live", 3)
    clock[0] += 1
    assert cache.delete("delete_expired") is True
    assert cache.delete("delete_expired") is False
    assert cache.clear() == 2


def test_zero_ttl_compute_returns_value_without_reusable_entry(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3")
    assert cache.get_or_compute("immediate", lambda: [1, 2], ttl=0) == [1, 2]
    assert cache.get("immediate", "absent") == "absent"


@pytest.mark.parametrize("ttl", [0, 1e-30])
@pytest.mark.parametrize("operation", ["set", "get_or_compute"])
def test_immediately_expired_write_does_not_evict_a_live_entry(
    tmp_path, clock, ttl, operation
):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=1)
    cache.set("live", {"preserved": True})
    if operation == "set":
        cache.set("expired", {"temporary": True}, ttl=ttl)
    else:
        assert cache.get_or_compute(
            "expired", lambda: {"temporary": True}, ttl=ttl
        ) == {"temporary": True}
    assert cache.get("expired", "absent") == "absent"
    assert cache.get("live") == {"preserved": True}


def test_zero_ttl_overwrite_invalidates_its_existing_key(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=2)
    cache.set("invalidate", "original")
    cache.set("preserve", "unrelated")
    cache.set("invalidate", "already expired", ttl=0)
    assert cache.get("invalidate", "absent") == "absent"
    assert cache.get("preserve") == "unrelated"


def test_expired_value_is_recomputed_and_ttl_begins_after_factory(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("value", "stale", ttl=1)
    clock[0] = 1_002.0
    calls = []

    def produce():
        calls.append(clock[0])
        clock[0] = 2_000.0
        return {"fresh": True}

    assert cache.get_or_compute("value", produce, ttl=10) == {"fresh": True}
    clock[0] = 2_009.0
    assert cache.get("value") == {"fresh": True}
    clock[0] = 2_010.0
    assert cache.get("value", "missing") == "missing"
    assert calls == [1_002.0]


@pytest.mark.parametrize(
    "ttl", [-1, -0.5, float("nan"), float("inf"), -float("inf"), 10**1_000]
)
@pytest.mark.parametrize("operation", ["set", "get_or_compute"])
def test_invalid_expiry_is_rejected_before_writing_or_computing(
    tmp_path, ttl, operation
):
    cache = Cache(tmp_path / "cache.sqlite3")
    write = (
        (lambda: cache.set("value", "never stored", ttl=ttl))
        if operation == "set"
        else (
            lambda: cache.get_or_compute(
                "value", lambda: pytest.fail("invalid TTL computed"), ttl=ttl
            )
        )
    )
    with pytest.raises(ValueError, match="ttl"):
        write()
    assert cache.get("value") is None


@pytest.mark.parametrize("max_entries", [0, -1])
def test_entry_limit_must_be_positive(tmp_path, max_entries):
    with pytest.raises(ValueError, match="max_entries"):
        Cache(tmp_path / "cache.sqlite3", max_entries=max_entries)


@pytest.mark.parametrize("max_entries", [True, 1.5, "2"])
def test_entry_limit_must_be_an_integer(tmp_path, max_entries):
    with pytest.raises(ValueError, match="max_entries"):
        Cache(tmp_path / "cache.sqlite3", max_entries=max_entries)


@pytest.mark.parametrize("max_entries", [2**63, 10**100])
def test_large_positive_capacity_accepts_writes_without_sql_integer_overflow(
    tmp_path, max_entries
):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=max_entries)
    cache.set("first", {"value": 1})
    cache.set("second", {"value": 2})
    assert cache.get_or_compute("third", lambda: {"value": 3}) == {"value": 3}
    assert cache.get("first") == {"value": 1}
    assert cache.get("second") == {"value": 2}
    assert cache.get("third") == {"value": 3}


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), 10**1_000])
def test_timeout_must_be_finite_and_nonnegative(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        Cache(tmp_path / "cache.sqlite3", timeout=timeout)


@pytest.mark.parametrize("timeout", [True, "1", None])
def test_timeout_rejects_non_numeric_values(tmp_path, timeout):
    with pytest.raises(TypeError, match="timeout"):
        Cache(tmp_path / "cache.sqlite3", timeout=timeout)


@pytest.mark.parametrize("ttl", [True, "1", object()])
def test_expiry_rejects_non_numeric_values_before_factory(tmp_path, ttl):
    cache = Cache(tmp_path / "cache.sqlite3")
    with pytest.raises(TypeError, match="ttl"):
        cache.get_or_compute(
            "value", lambda: pytest.fail("invalid TTL computed"), ttl=ttl
        )


@pytest.mark.parametrize("operation", ["get", "set", "delete", "get_or_compute"])
def test_keys_must_be_strings(tmp_path, operation):
    cache = Cache(tmp_path / "cache.sqlite3")
    arguments = {
        "get": (1,),
        "set": (1, "value"),
        "delete": (1,),
        "get_or_compute": (1, lambda: pytest.fail("invalid key computed")),
    }
    method = getattr(cache, operation)
    with pytest.raises(TypeError, match="key"):
        method(*arguments[operation])


def test_parent_directories_are_created(tmp_path):
    path = tmp_path / "nested" / "parents" / "cache.sqlite3"
    cache = Cache(path)
    cache.set("value", 1)
    assert path.is_file()
    assert cache.get("value") == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_new_database_is_private_and_existing_mode_is_retained(tmp_path):
    path = tmp_path / "cache.sqlite3"
    Cache(path)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    path.chmod(0o640)
    Cache(path).set("value", 1)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_entry_bound_evicts_oldest_write_without_promoting_hits(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=2)
    cache.set("first", 1)
    cache.set("second", 2)
    assert cache.get("first") == 1
    cache.set("third", 3)
    assert cache.get("first") is None
    assert cache.get("second") == 2
    assert cache.get("third") == 3


def test_overwrite_becomes_newest_write_even_when_clock_is_unchanged(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=2)
    cache.set("first", 1)
    cache.set("second", 2)
    cache.set("first", 10)
    cache.set("third", 3)
    assert cache.get("first") == 10
    assert cache.get("second") is None
    assert cache.get("third") == 3


def test_fifo_order_survives_wall_clock_moving_backwards(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=2)
    cache.set("first", 1)
    clock[0] -= 100
    cache.set("second", 2)
    clock[0] -= 100
    cache.set("third", 3)
    assert cache.get("first") is None
    assert cache.get("second") == 2
    assert cache.get("third") == 3


@pytest.mark.parametrize("operation", ["set", "get_or_compute"])
def test_overflowing_expiry_timestamp_rolls_back_without_publishing(
    tmp_path, clock, operation
):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("committed", "preserved")
    clock[0] = 1e308
    write = (
        (lambda: cache.set("overflow", "value", ttl=1e308))
        if operation == "set"
        else (lambda: cache.get_or_compute("overflow", lambda: "value", ttl=1e308))
    )
    with pytest.raises(ValueError, match="ttl"):
        write()
    assert cache.get("overflow", "missing") == "missing"
    assert cache.get("committed") == "preserved"


def test_expired_entries_are_removed_before_fifo_eviction(tmp_path, clock):
    cache = Cache(tmp_path / "cache.sqlite3", max_entries=2)
    cache.set("live", 1)
    cache.set("expired", 2, ttl=1)
    clock[0] += 1
    cache.set("new", 3)
    assert cache.get("live") == 1
    assert cache.get("expired") is None
    assert cache.get("new") == 3


def test_entry_bounds_apply_per_namespace_and_after_compute(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = Cache(path, max_entries=1)
    second = Cache(path, namespace="other", max_entries=1)
    first.set("old", 1)
    second.set("retained", 2)
    assert first.get_or_compute("new", lambda: 3) == 3
    assert first.get("old") is None
    assert first.get("new") == 3
    assert second.get("retained") == 2


def test_factory_failure_rolls_back_and_releases_the_writer(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.set("other", {"preserved": True})

    def fail():
        message = "producer failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="producer failed"):
        cache.get_or_compute("missing", fail)
    assert cache.get("missing", "absent") == "absent"
    assert cache.get("other") == {"preserved": True}
    assert cache.get_or_compute("missing", lambda: "recovered") == "recovered"


@pytest.mark.parametrize("operation", ["get", "get_or_compute"])
def test_corrupt_stored_json_fails_loud_instead_of_recomputing(tmp_path, operation):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path)
    cache.set("corrupt", {"valid": "original"})
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE _unstd_cache_v1 SET value = ? WHERE namespace = ? AND key = ?",
            (b"{not JSON", "default", "corrupt"),
        )
    read = (
        (lambda: cache.get("corrupt"))
        if operation == "get"
        else (
            lambda: cache.get_or_compute(
                "corrupt", lambda: pytest.fail("corruption recomputed")
            )
        )
    )
    with pytest.raises(ValueError, match=r"JSON|json|Expecting"):
        read()


def test_warm_reads_do_not_commit_database_writes(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path)
    cache.set("warm", {"value": 1})
    with sqlite3.connect(path) as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        for _ in range(3):
            assert cache.get("warm") == {"value": 1}
            assert cache.get_or_compute(
                "warm", lambda: pytest.fail("warm recomputed")
            ) == {"value": 1}
        after = observer.execute("PRAGMA data_version").fetchone()[0]
    assert after == before

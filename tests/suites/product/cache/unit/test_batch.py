"""Atomic JSON batches, consistent read snapshots, expiry, and bounded FIFO."""

from __future__ import annotations

from contextlib import closing
import sqlite3
import time

import pytest

import unstd.cache as cache_module
from unstd.cache import Cache


def test_batch_reads_preserve_input_order_and_distinguish_null_from_missing(tmp_path):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path)
    cache.set_many({"first": {"rows": [1, 2]}, "second": None, "third": 3})
    Cache(path, namespace="other").set_many({"third": "other namespace"})
    result = cache.get_many(
        key for key in ["third", "missing", "second", "third", "first"]
    )
    assert result == {"third": 3, "second": None, "first": {"rows": [1, 2]}}
    assert list(result) == ["third", "second", "first"]


def test_batch_values_survive_reopening_and_are_independent(tmp_path):
    path = tmp_path / "results.sqlite3"
    values = {"first": {"rows": [1, 2]}, "second": ["café", None]}
    Cache(path).set_many(values)
    values["first"]["rows"].append(3)
    cache = Cache(path)
    first_read = cache.get_many(["first", "second"])
    first_read["first"]["rows"].append(4)
    assert cache.get_many(["first", "second"]) == {
        "first": {"rows": [1, 2]},
        "second": ["café", None],
    }


def test_empty_batches_do_not_open_a_connection(tmp_path, monkeypatch):
    cache = Cache(tmp_path / "results.sqlite3")

    def unexpected_connection():
        pytest.fail("an empty batch opened a database connection")

    monkeypatch.setattr(cache, "_connect", unexpected_connection)
    assert cache.get_many(iter(())) == {}
    assert cache.set_many({}) is None


@pytest.mark.parametrize("operation", ["get_many", "set_many"])
def test_all_batch_keys_are_validated_before_database_access(
    tmp_path, monkeypatch, operation
):
    cache = Cache(tmp_path / "results.sqlite3")
    monkeypatch.setattr(
        cache, "_connect", lambda: pytest.fail("invalid keys reached SQLite")
    )
    argument = ["valid", 1] if operation == "get_many" else {"valid": 1, 2: 2}
    with pytest.raises(TypeError, match="key"):
        getattr(cache, operation)(argument)


@pytest.mark.parametrize(
    "bad_value", [object(), float("nan"), {"nested": float("inf")}]
)
def test_late_encoding_failure_leaves_every_prior_value_intact(tmp_path, bad_value):
    cache = Cache(tmp_path / "results.sqlite3")
    cache.set_many({"existing": "committed", "other": "retained"})
    with pytest.raises((TypeError, ValueError)):
        cache.set_many({"existing": "uncommitted", "invalid": bad_value})
    assert cache.get_many(["existing", "other", "invalid"]) == {
        "existing": "committed",
        "other": "retained",
    }


def test_late_database_failure_rolls_back_the_entire_batch(tmp_path):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path)
    cache.set_many({"first": "old", "other": "retained"})
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER reject_batch_key BEFORE INSERT ON _unstd_cache_v1 "
            "WHEN NEW.key = 'blocked' BEGIN SELECT RAISE(ABORT, 'blocked key'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="blocked key"):
        cache.set_many({"first": "new", "blocked": "rejected"})
    assert cache.get_many(["first", "other", "blocked"]) == {
        "first": "old",
        "other": "retained",
    }
    cache.set_many({"after_failure": "recovered"})
    assert cache.get("after_failure") == "recovered"


def test_all_batch_values_share_expiry_after_every_value_is_encoded(
    tmp_path, monkeypatch
):
    cache = Cache(tmp_path / "results.sqlite3")
    current = [1000.0]
    monkeypatch.setattr(time, "time", lambda: current[0])
    encode = cache_module._encode

    def expensive_encode(value):
        current[0] += 10
        return encode(value)

    monkeypatch.setattr(cache_module, "_encode", expensive_encode)
    cache.set_many({"first": 1, "second": 2}, ttl=10)
    current[0] = 1029.0
    assert cache.get_many(["first", "second"]) == {"first": 1, "second": 2}
    current[0] = 1030.0
    assert cache.get_many(["first", "second"]) == {}


def test_batch_capacity_keeps_newest_mapping_items_and_isolates_namespaces(tmp_path):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path, max_entries=2)
    other = Cache(path, namespace="other", max_entries=1)
    other.set_many({"unrelated": "retained"})
    cache.set_many({"first": 1, "second": 2, "third": 3})
    assert cache.get_many(["first", "second", "third"]) == {"second": 2, "third": 3}
    cache.set_many({"second": "updated", "fourth": 4})
    assert cache.get_many(["second", "third", "fourth"]) == {
        "second": "updated",
        "fourth": 4,
    }
    assert other.get("unrelated") == "retained"


def test_immediately_expired_batch_does_not_evict_live_values(tmp_path):
    cache = Cache(tmp_path / "results.sqlite3", max_entries=1)
    cache.set("live", "retained")
    cache.set_many({"expired_first": 1, "expired_second": 2}, ttl=0)
    assert cache.get_many(["live", "expired_first", "expired_second"]) == {
        "live": "retained"
    }


def test_immediate_expiry_replaces_existing_batch_keys_without_removing_others(
    tmp_path,
):
    cache = Cache(tmp_path / "results.sqlite3")
    cache.set_many({"first": 1, "second": 2, "unrelated": 3})
    cache.set_many({"first": "expired", "second": "expired"}, ttl=0)
    assert cache.get_many(["first", "second", "unrelated"]) == {"unrelated": 3}


def test_batched_lookups_bind_sql_shaped_keys(tmp_path):
    cache = Cache(tmp_path / "results.sqlite3")
    values = {"x' OR 1=1 --": "quoted", "NULL\x00key": "zero", "plain": "plain"}
    cache.set_many(values)
    assert cache.get_many(values) == values
    assert cache.get_many(["not' OR 1=1 --"]) == {}


def test_chunked_reads_use_one_expiry_cutoff(tmp_path, monkeypatch):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path)
    current = [1000.0]
    monkeypatch.setattr(time, "time", lambda: current[0])
    values = {f"key-{index}": index for index in range(8)}
    cache.set_many(values, ttl=1)

    def small_connection():
        connection = sqlite3.connect(path, isolation_level=None)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 5)
        return connection

    calls = []

    def advancing_time():
        calls.append(None)
        return 1000.0 if len(calls) == 1 else 1002.0

    monkeypatch.setattr(cache, "_connect", small_connection)
    monkeypatch.setattr(time, "time", advancing_time)
    assert cache.get_many(values) == values
    assert len(calls) == 1


def test_chunked_reads_cannot_mix_versions_across_a_concurrent_commit(
    tmp_path, monkeypatch
):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path)
    writer = Cache(path)
    original = {f"key-{index}": "old" for index in range(8)}
    replacement = dict.fromkeys(original, "new")
    writer.set_many(original)

    class ObservedConnection(sqlite3.Connection):
        first_lookup = True

        def execute(self, sql, parameters=(), /):
            cursor = super().execute(sql, parameters)
            if self.first_lookup and sql.startswith("SELECT key, value "):
                self.first_lookup = False
                writer.set_many(replacement)
            return cursor

    def small_connection():
        connection = sqlite3.connect(
            path, isolation_level=None, factory=ObservedConnection
        )
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 5)
        return connection

    monkeypatch.setattr(cache, "_connect", small_connection)
    assert cache.get_many(original) == original
    assert writer.get_many(original) == replacement


def test_corrupt_entry_fails_the_entire_read_batch(tmp_path):
    path = tmp_path / "results.sqlite3"
    cache = Cache(path)
    cache.set_many({"valid": 1, "corrupt": 2})
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE _unstd_cache_v1 SET value = ? WHERE key = ?",
            (b"{broken", "corrupt"),
        )
    with pytest.raises(ValueError, match=r"JSON|json|Expecting"):
        cache.get_many(["valid", "corrupt"])
    assert cache.get_many(["valid"]) == {"valid": 1}


@pytest.mark.parametrize("ttl", [-1, float("inf"), float("nan"), 10**1000])
def test_invalid_batch_ttl_is_rejected_without_modification(tmp_path, ttl):
    cache = Cache(tmp_path / "results.sqlite3")
    cache.set_many({"existing": "retained"})
    with pytest.raises(ValueError, match="ttl"):
        cache.set_many({"existing": "rejected"}, ttl=ttl)
    assert cache.get("existing") == "retained"

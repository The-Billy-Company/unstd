"""Independent fills overlap while same-key mutations and clears stay ordered."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os

import pytest

from tests.support.environment.process import python_worker, run_python, worker_line
from unstd.cache import Cache, locking


_PRODUCER = """
import json
import sys
from unstd.cache import Cache
cache = Cache(sys.argv[1], namespace=sys.argv[3], timeout=5)
print('READY', flush=True)
sys.stdin.read(1)
def produce():
    print('PRODUCING', flush=True)
    sys.stdin.read(1)
    return {'key': sys.argv[2]}
print(json.dumps(cache.get_or_compute(sys.argv[2], produce)), flush=True)
"""


def _other_stripe(key, namespace="default"):
    return next(
        candidate
        for index in range(100)
        if locking._stripe(namespace, candidate := f"independent-{index}")
        != locking._stripe(namespace, key)
    )


def test_distinct_cold_keys_enter_producers_before_either_finishes(tmp_path):
    path = tmp_path / "cache.sqlite3"
    Cache(path)
    keys = ("first", _other_stripe("first"))
    with ExitStack() as stack:
        workers = [
            stack.enter_context(
                python_worker(_PRODUCER, [str(path), key, "default"], cwd=tmp_path)
            )
            for key in keys
        ]
        for worker in workers:
            assert worker_line(worker, timeout=5) == "READY\n"
            worker.stdin.write("s")
            worker.stdin.flush()
        # Neither producer can finish until released by this parent. Observing
        # both entries therefore proves overlap without a timing assertion.
        for worker in workers:
            assert worker_line(worker, timeout=5) == "PRODUCING\n"
        cache = Cache(path, timeout=0.01)
        unrelated = next(
            key
            for index in range(100)
            if all(
                locking._stripe("default", key := f"writer-{index}")
                != locking._stripe("default", held)
                for held in keys
            )
        )
        cache.set(unrelated, "writer remains available")
        assert cache.get(unrelated) == "writer remains available"
        for worker in workers:
            worker.stdin.write("r")
            worker.stdin.flush()
        for worker, key in zip(workers, keys, strict=True):
            stdout, stderr = worker.communicate(timeout=10)
            assert worker.returncode == 0, stderr
            assert json.loads(stdout) == {"key": key}


@pytest.mark.parametrize("operation", ["set", "set_many", "delete", "clear"])
def test_mutations_cannot_overtake_a_fill_of_the_same_key(tmp_path, operation):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, timeout=0.01)
    with python_worker(
        _PRODUCER, [str(path), "shared", "default"], cwd=tmp_path
    ) as worker:
        assert worker_line(worker, timeout=5) == "READY\n"
        worker.stdin.write("s")
        worker.stdin.flush()
        assert worker_line(worker, timeout=5) == "PRODUCING\n"
        actions = {
            "set": lambda: cache.set("shared", "new"),
            "set_many": lambda: cache.set_many({"shared": "new", "other": 1}),
            "delete": lambda: cache.delete("shared"),
            "clear": cache.clear,
        }
        import sqlite3

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            actions[operation]()
        assert cache.get("other") is None
        worker.stdin.write("r")
        worker.stdin.flush()
        stdout, stderr = worker.communicate(timeout=10)
        assert worker.returncode == 0, stderr
        assert json.loads(stdout) == {"key": "shared"}
    actions[operation]()
    assert cache.get("shared") == ("new" if operation in ("set", "set_many") else None)


@pytest.mark.parametrize("operation", ["set", "set_many", "delete", "clear", "fill"])
def test_factory_reentry_fails_before_waiting_and_releases_its_lock(
    tmp_path, operation
):
    cache = Cache(tmp_path / "cache.sqlite3", timeout=0)
    actions = {
        "set": lambda: cache.set("nested", 1),
        "set_many": lambda: cache.set_many({"nested": 1}),
        "delete": lambda: cache.delete("nested"),
        "clear": cache.clear,
        "fill": lambda: cache.get_or_compute("nested", lambda: 1),
    }
    with pytest.raises(RuntimeError, match="factory cannot write"):
        cache.get_or_compute("outer", actions[operation])
    assert cache.get("outer") is None
    assert cache.get_or_compute("outer", lambda: "recovered") == "recovered"


def test_factory_can_read_warm_dependencies(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", timeout=0)
    cache.set("dependency", [1, 2])

    def unexpected():
        pytest.fail("warm dependency recomputed")

    assert cache.get_or_compute(
        "derived", lambda: cache.get_or_compute("dependency", unexpected)
    ) == [1, 2]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink aliases")
def test_symlink_alias_uses_same_fill_lock(tmp_path):
    path = tmp_path / "cache.sqlite3"
    Cache(path)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(path)
    with python_worker(
        _PRODUCER, [str(path), "shared", "default"], cwd=tmp_path
    ) as worker:
        assert worker_line(worker, timeout=5) == "READY\n"
        worker.stdin.write("s")
        worker.stdin.flush()
        assert worker_line(worker, timeout=5) == "PRODUCING\n"
        import sqlite3

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            Cache(alias, timeout=0).get_or_compute("shared", lambda: "wrong")
        worker.stdin.write("r")
        worker.stdin.flush()
        stdout, stderr = worker.communicate(timeout=10)
        assert worker.returncode == 0, stderr
        assert json.loads(stdout) == {"key": "shared"}
    assert Cache(alias).get("shared") == {"key": "shared"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX fork ownership")
def test_forked_producer_cannot_publish_without_its_parent_lock(tmp_path):
    code = """
import os
from unstd.cache import Cache
cache = Cache('cache.sqlite3')
child = None
def produce():
    global child
    child = os.fork()
    return {'owner': 'parent' if child else 'child'}
try:
    result = cache.get_or_compute('shared', produce)
except RuntimeError as error:
    assert child == 0 and 'after fork' in str(error)
    os._exit(0)
assert result == {'owner': 'parent'}
_, status = os.waitpid(child, 0)
assert os.waitstatus_to_exitcode(status) == 0
assert cache.get('shared') == result
print('FORK_OK')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "FORK_OK\n"

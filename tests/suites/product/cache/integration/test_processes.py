"""Process persistence, transactional fills, reader progress, and lock recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3

import pytest

from tests.support.environment.process import python_worker, run_python, worker_line
from unstd.cache import Cache


_FILL = """
import json
from pathlib import Path
import sqlite3
import sys
from unstd.cache import Cache

cache = Cache(sys.argv[1], timeout=5)
role = sys.argv[2]
calls = Path(sys.argv[3])
if role == 'contender':
    # Pause AFTER a real SELECT has observed the missing value. The owner will
    # commit before this contender continues, proving the second lookup matters.
    connect = sqlite3.connect
    class ObservedConnection(sqlite3.Connection):
        first_lookup = True

        def execute(self, sql, parameters=(), /):
            cursor = super().execute(sql, parameters)
            if ObservedConnection.first_lookup and sql.startswith('SELECT value '):
                ObservedConnection.first_lookup = False
                assert cursor.fetchone() is None
                print('MISS_OBSERVED', flush=True)
                sys.stdin.read(1)
            return cursor

    sqlite3.connect = lambda *args, **kwargs: connect(
        *args, factory=ObservedConnection, **kwargs
    )
print('READY', flush=True)
sys.stdin.read(1)
print('LOOKUP', flush=True)

def produce():
    with calls.open('a', encoding='utf-8') as stream:
        stream.write(role + '\\n')
    print('PRODUCING', flush=True)
    if role == 'owner':
        sys.stdin.read(1)
    return {'producer': role, 'rows': [1, 2, 3]}

print(json.dumps(cache.get_or_compute('shared', produce)), flush=True)
"""


def test_other_interpreter_reuses_value_without_calling_producer(tmp_path):
    path = tmp_path / "cache.sqlite3"
    Cache(path).set("persisted", {"rows": [1, 2, 3]})
    code = """
import json
import sys
from unstd.cache import Cache

def unexpected():
    raise AssertionError('persisted value was recomputed')

cache = Cache(sys.argv[1])
print(json.dumps(cache.get_or_compute('persisted', unexpected)))
cache.set('from_child', {'written': True})
"""
    result = run_python(code, [str(path)], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"rows": [1, 2, 3]}
    assert Cache(path).get("from_child") == {"written": True}


def test_cache_operates_when_serialization_extras_cannot_import(tmp_path):
    code = """
import sys

class NoSerializationBackends:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'orjson', 'msgspec', 'pybase64'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, NoSerializationBackends())
from unstd.cache import Cache
cache = Cache('base.sqlite3')
assert cache.get_or_compute('one', lambda: {'value': [1, 2]}) == {'value': [1, 2]}
assert Cache('base.sqlite3').get('one') == {'value': [1, 2]}
print('BASE_OK')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "BASE_OK\n"


def test_same_missing_key_has_one_producer_across_processes(tmp_path):
    path = tmp_path / "cache.sqlite3"
    calls = tmp_path / "producer-calls.txt"
    Cache(path)
    with (
        python_worker(_FILL, [str(path), "owner", str(calls)], cwd=tmp_path) as owner,
        python_worker(
            _FILL, [str(path), "contender", str(calls)], cwd=tmp_path
        ) as contender,
    ):
        assert worker_line(owner, timeout=5) == "READY\n"
        assert worker_line(contender, timeout=5) == "READY\n"
        owner.stdin.write("s")
        owner.stdin.flush()
        assert worker_line(owner, timeout=5) == "LOOKUP\n"
        assert worker_line(owner, timeout=5) == "PRODUCING\n"
        contender.stdin.write("s")
        contender.stdin.flush()
        assert worker_line(contender, timeout=5) == "LOOKUP\n"
        assert worker_line(contender, timeout=5) == "MISS_OBSERVED\n"
        owner.stdin.write("r")
        owner.stdin.flush()
        owner_out, owner_err = owner.communicate(timeout=10)
        contender.stdin.write("r")
        contender.stdin.flush()
        contender_out, contender_err = contender.communicate(timeout=10)
        assert owner.returncode == 0, owner_err
        assert contender.returncode == 0, contender_err
        expected = {"producer": "owner", "rows": [1, 2, 3]}
        assert json.loads(owner_out) == expected
        assert json.loads(contender_out) == expected
    assert calls.read_text(encoding="utf-8").splitlines() == ["owner"]


def test_warm_reads_continue_while_another_process_produces(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, timeout=0.05)
    cache.set("warm", {"committed": True})
    calls = tmp_path / "producer-calls.txt"
    with python_worker(_FILL, [str(path), "owner", str(calls)], cwd=tmp_path) as owner:
        assert worker_line(owner, timeout=5) == "READY\n"
        owner.stdin.write("s")
        owner.stdin.flush()
        assert worker_line(owner, timeout=5) == "LOOKUP\n"
        assert worker_line(owner, timeout=5) == "PRODUCING\n"
        assert cache.get("warm") == {"committed": True}
        assert Cache(path, timeout=0.05).get("warm") == {"committed": True}
        assert cache.get_or_compute(
            "warm", lambda: pytest.fail("warm hit called producer")
        ) == {"committed": True}
        cache.set("unrelated_writer", "continues during fill")
        assert cache.get("unrelated_writer") == "continues during fill"
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            cache.set("shared", "must wait for the same-key producer")
        owner.stdin.write("r")
        owner.stdin.flush()
        stdout, stderr = owner.communicate(timeout=10)
        assert owner.returncode == 0, stderr
        assert json.loads(stdout)["producer"] == "owner"
    cache.set("unblocked_writer", "recovered")
    assert cache.get("unblocked_writer") == "recovered"


def test_busy_fill_times_out_before_calling_factory_and_recovers(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, timeout=0.01)
    with sqlite3.connect(path) as database:
        database.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            cache.get_or_compute("missing", lambda: pytest.fail("writer lock bypassed"))
        assert cache.get("missing") is None
        database.rollback()
    assert cache.get_or_compute("missing", lambda: {"recovered": True}) == {
        "recovered": True
    }


def test_killed_producer_publishes_nothing_and_does_not_strand_lock(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, timeout=0.1)
    cache.set("warm", "committed before interruption")
    calls = tmp_path / "producer-calls.txt"
    with python_worker(_FILL, [str(path), "owner", str(calls)], cwd=tmp_path) as owner:
        assert worker_line(owner, timeout=5) == "READY\n"
        owner.stdin.write("s")
        owner.stdin.flush()
        assert worker_line(owner, timeout=5) == "LOOKUP\n"
        assert worker_line(owner, timeout=5) == "PRODUCING\n"
        assert cache.get("shared", "absent") == "absent"
        owner.kill()
        owner.wait(timeout=5)
        assert owner.returncode != 0
    assert cache.get("shared", "absent") == "absent"
    assert cache.get("warm") == "committed before interruption"
    assert cache.get_or_compute("shared", lambda: "recovered") == "recovered"


def test_one_cache_handle_is_usable_from_multiple_threads(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")

    def write_and_read(index):
        cache.set(f"key-{index}", {"index": index})
        return cache.get(f"key-{index}")

    with ThreadPoolExecutor(max_workers=4) as workers:
        values = list(workers.map(write_and_read, range(24)))
    assert values == [{"index": index} for index in range(24)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX fork ownership")
def test_cache_handle_remains_usable_after_fork(tmp_path):
    code = """
import os
from unstd.cache import Cache

cache = Cache('fork.sqlite3')
cache.set('parent', {'written': 'before fork'})
child = os.fork()
if child == 0:
    try:
        assert cache.get('parent') == {'written': 'before fork'}
        cache.set('child', {'written': 'after fork'})
    except BaseException:
        os._exit(1)
    os._exit(0)
_, status = os.waitpid(child, 0)
assert os.waitstatus_to_exitcode(status) == 0
assert cache.get('child') == {'written': 'after fork'}
cache.set('parent', {'written': 'after child exit'})
print('FORK_OK')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "FORK_OK\n"

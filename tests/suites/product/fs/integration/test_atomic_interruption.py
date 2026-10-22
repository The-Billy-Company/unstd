"""Bounded writer interruption and simultaneous-reader atomicity experiments.

Readiness is an explicit pipe message after data has reached the chosen stage.
Killing before commit may orphan a scratch file; it must never alter the prior
destination. These tests exercise process interruption, not power-loss durability.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import python_worker, run_python, worker_line
from unstd import fs


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("stage", ["partial", "synced", "committed"])
def test_killed_writer_exposes_only_committed_destination(
    tmp_path: Path, stage: str
) -> None:
    dest = tmp_path / "state.bin"
    prior = b"prior committed version" * 128
    replacement = b"new complete version" * 4096
    dest.write_bytes(prior)
    code = """
import os
from pathlib import Path
import sys
from unstd import fs

dest = Path(sys.argv[1])
stage = sys.argv[2]
payload = b'new complete version' * 4096
with fs.atomic_writer(dest, mode='wb') as writer:
    writer.write(payload[:4096] if stage == 'partial' else payload)
    writer.flush()
    if stage == 'synced':
        os.fsync(writer.fileno())
    if stage != 'committed':
        print('READY', flush=True)
        sys.stdin.read(1)
if stage == 'committed':
    print('READY', flush=True)
    sys.stdin.read(1)
"""
    with python_worker(code, [str(dest), stage], cwd=tmp_path) as child:
        assert worker_line(child, timeout=5) == "READY\n"
        observed = dest.read_bytes()
        assert observed == (replacement if stage == "committed" else prior)
        child.kill()
        child.wait(timeout=5)
        assert child.returncode != 0
    assert dest.read_bytes() == (replacement if stage == "committed" else prior)
    scratch = list(tmp_path.glob(".state.bin.*.tmp"))
    assert len(scratch) == (0 if stage == "committed" else 1)
    for orphan in scratch:
        assert orphan.read_bytes() == (
            replacement[:4096] if stage == "partial" else replacement
        )
        orphan.unlink()
    fs.atomic_write(dest, b"recovered")
    assert dest.read_bytes() == b"recovered"
    assert list(tmp_path.iterdir()) == [dest]


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX replacement of an open destination"
)
def test_reader_observes_only_whole_versions_during_replacement(tmp_path: Path) -> None:
    code = """
from pathlib import Path
import threading
from unstd import fs

dest = Path('state.bin')
versions = [bytes([value]) * (131072 + value) for value in range(8)]
fs.atomic_write(dest, versions[0])
generations = 64
phase = threading.Barrier(4, timeout=5)
committed = [threading.Event() for _ in range(generations)]
errors = []
precommit_counts = [0, 0, 0]
postcommit_counts = [0, 0, 0]
racing_counts = [0, 0, 0]

def reader(index):
    try:
        for generation in range(generations):
            old = versions[(generation - 1) % len(versions)] if generation else versions[0]
            new = versions[generation % len(versions)]
            phase.wait()
            # The writer has flushed only half its scratch file at this barrier.
            assert dest.read_bytes() == old, 'partial scratch became visible'
            precommit_counts[index] += 1
            phase.wait()
            while not committed[generation].is_set():
                observed = dest.read_bytes()
                assert observed in (old, new), f'torn read: {len(observed)} bytes'
                racing_counts[index] += 1
            phase.wait()
            assert dest.read_bytes() == new, 'committed version not visible'
            postcommit_counts[index] += 1
            phase.wait()
    except BaseException as error:
        errors.append(repr(error))
        phase.abort()
        for event in committed:
            event.set()

threads = [threading.Thread(target=reader, args=(i,), daemon=True) for i in range(3)]
for thread in threads:
    thread.start()
finished = False
try:
    for generation in range(generations):
        new = versions[generation % len(versions)]
        middle = len(new) // 2
        with fs.atomic_writer(dest, mode='wb') as writer:
            writer.write(new[:middle])
            writer.flush()
            phase.wait()
            phase.wait()
            writer.write(new[middle:])
        committed[generation].set()
        phase.wait()
        phase.wait()
    finished = True
finally:
    if not finished:
        phase.abort()
    for event in committed:
        event.set()
    for thread in threads:
        thread.join(timeout=5)
assert not any(thread.is_alive() for thread in threads), 'reader did not stop'
assert not errors, errors
assert precommit_counts == [generations] * 3, precommit_counts
assert postcommit_counts == [generations] * 3, postcommit_counts
assert dest.read_bytes() == versions[7]
assert list(Path('.').glob('.state.bin.*.tmp')) == []
print('WHOLE_VERSIONS', precommit_counts, postcommit_counts, racing_counts)
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("WHOLE_VERSIONS ")

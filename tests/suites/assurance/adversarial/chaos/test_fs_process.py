"""Recorded mixed fault histories with durable state and child ownership oracles.

P0 integration tests cover isolated boundaries. These scenarios retain the same
destination/process worker across shuffled failures, secondary cleanup failures,
successful commits, pipe pressure, cancellation and recovery. Writer kills are
handshaken at actual commit boundaries, never selected by a timing guess.
"""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.chaos.fs_process import ATOMIC_ACTIONS, PROCESS_ACTIONS
from tests.support.chaos.journal import ordered
from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.process import python_worker, run_python, worker_line
from unstd import fs


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _schedule_worker(
    name: str, actions: Sequence[str], directory: Path, journal: Any
) -> dict:
    code = f"""
import json
from pathlib import Path
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from tests.support.chaos.fs_process import {name}
def record(event):
    print(json.dumps({{'progress': event}}, sort_keys=True), flush=True)
result = {name}(Path.cwd(), tuple(json.loads(sys.argv[1])), record=record)
print(json.dumps({{'report': result}}, sort_keys=True), flush=True)
"""
    result = run_python(code, [json.dumps(list(actions))], cwd=directory, timeout=35)
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    for frame in frames:
        if "progress" in frame:
            journal.record("worker-progress", evidence=frame["progress"])
    assert result.returncode == 0, result.stderr
    return frames[-1]["report"]


def test_atomic_fault_history_preserves_model_and_recovers_after_every_step(
    tmp_path: Path, chaos_seed: int, chaos_journal: Any
) -> None:
    supported = tuple(
        action
        for action in ATOMIC_ACTIONS
        if os.name == "posix" or action not in {"directory-sync", "directory-close"}
    )
    actions = ordered(chaos_seed, "atomic-mixed-history", supported * 2)
    chaos_journal.record("schedule", actions=list(actions))
    report = _schedule_worker("atomic_schedule", actions, tmp_path, chaos_journal)
    chaos_journal.record("observed", evidence=report)
    assert [event["action"] for event in report["events"]] == list(actions)
    assert len(report["events"]) == len(supported) * 2
    assert sum(event["rollback"] for event in report["events"]) == 16
    assert sum(event["orphan_count"] for event in report["events"]) == 2
    assert all(event["committed_sha256"] for event in report["events"])


@pytest.mark.skipif(os.name != "posix", reason="strict directory-sync acknowledgments")
def test_strict_durability_history_retains_commit_state_and_recovers(
    tmp_path: Path, chaos_seed: int, chaos_journal: Any
) -> None:
    actions = ordered(
        chaos_seed,
        "strict-durability-history",
        (
            "success",
            "preflight-fsync",
            "post-commit-fsync",
            "post-commit-cancel",
            "post-commit-fsync-close",
        )
        * 2,
    )
    chaos_journal.record("schedule", actions=list(actions))
    report = _schedule_worker(
        "strict_atomic_schedule", actions, tmp_path, chaos_journal
    )
    events = report["events"]
    assert [event["action"] for event in events] == list(actions)
    assert sum(not event["committed_at_failure"] for event in events) == 2
    assert all(event["recovered"] for event in events)
    assert sum("directory-close" in event["hits"] for event in events) == 2
    chaos_journal.record("observed", evidence=report)


def test_process_fault_history_preserves_outputs_reaps_and_recovers(
    tmp_path: Path, chaos_seed: int, chaos_journal: Any
) -> None:
    supported = (
        (*PROCESS_ACTIONS, "interrupt") if os.name == "posix" else PROCESS_ACTIONS
    )
    actions = ordered(chaos_seed, "process-mixed-history", supported * 2)
    chaos_journal.record("schedule", actions=list(actions))
    report = _schedule_worker("process_schedule", actions, tmp_path, chaos_journal)
    chaos_journal.record("observed", evidence=report)
    assert [event["action"] for event in report["events"]] == list(actions)
    assert all(event["recovery"] for event in report["events"])
    assert all(
        event["stdout_bytes"] == event["stderr_bytes"] == 524288
        for event in report["events"]
    )
    if os.name == "posix":
        assert all(event["reaped"] for event in report["events"])


_KILLED_WRITER = """
from pathlib import Path
import os
import stat
import sys
from unstd import fs

dest = Path(sys.argv[1])
phase = sys.argv[2]
index = int(sys.argv[3])
payload = index.to_bytes(4, 'big') + bytes([index + 1]) * (131072 + index)
real_os = os
real_replace = Path.replace
ready = False

def checkpoint(name):
    global ready
    if phase == name:
        ready = True
        print('READY:' + name, flush=True)
        sys.stdin.read(1)
        raise AssertionError('supervisor released a worker selected for kill')

class Boundary:
    def __getattr__(self, name):
        return getattr(real_os, name)
    def fsync(self, fd):
        directory = stat.S_ISDIR(real_os.fstat(fd).st_mode)
        real_os.fsync(fd)
        checkpoint('directory-synced' if directory else 'file-synced')

def replace(path, target):
    checkpoint('before-replace')
    result = real_replace(path, target)
    checkpoint('after-replace')
    return result

fs.os = Boundary()
Path.replace = replace
with fs.atomic_writer(dest, mode='wb', durable=True) as writer:
    middle = len(payload) // 2
    writer.write(payload[:middle])
    writer.flush()
    checkpoint('partial-write')
    writer.write(payload[middle:])
assert ready, 'selected checkpoint was not reached'
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGKILL and mode preservation")
def test_repeated_writer_kills_match_the_actual_commit_boundary_and_recover(
    tmp_path: Path, chaos_seed: int, chaos_journal: Any
) -> None:
    phases = ordered(
        chaos_seed,
        "atomic-kill-history",
        (
            "partial-write",
            "file-synced",
            "before-replace",
            "after-replace",
            "directory-synced",
        )
        * 2,
    )
    chaos_journal.record("schedule", actions=list(phases))
    dest = tmp_path / "durable.bin"
    committed = b"initial durable state" * 1024
    fs.atomic_write(dest, committed, durable=True)
    dest.chmod(0o640)
    evidence = []
    for index, phase in enumerate(phases):
        proposed = index.to_bytes(4, "big") + bytes([index + 1]) * (131072 + index)
        after_replace = phase in {"after-replace", "directory-synced"}
        expected = proposed if after_replace else committed
        with python_worker(
            _KILLED_WRITER, [str(dest), phase, str(index)], cwd=tmp_path
        ) as child:
            assert worker_line(child, timeout=8) == f"READY:{phase}\n"
            assert dest.read_bytes() == expected, "commit checkpoint exposed torn data"
            child.kill()
            child.wait(timeout=5)
            assert child.returncode < 0
        assert dest.read_bytes() == expected, "kill changed the observed commit"
        assert stat.S_IMODE(dest.stat().st_mode) == 0o640
        scratch = list(tmp_path.glob(".durable.bin.*.tmp"))
        assert len(scratch) == (0 if after_replace else 1)
        for orphan in scratch:
            assert orphan.read_bytes() == (
                proposed[: len(proposed) // 2] if phase == "partial-write" else proposed
            )
            orphan.unlink()
        committed = b"recovered:" + proposed
        fs.atomic_write(dest, committed, durable=True)
        assert dest.read_bytes() == committed
        assert stat.S_IMODE(dest.stat().st_mode) == 0o640
        assert list(tmp_path.iterdir()) == [dest]
        evidence.append(
            {
                "step": index,
                "phase": phase,
                "committed": after_replace,
                "orphans_removed": len(scratch),
            }
        )
        chaos_journal.record("checkpoint", **evidence[-1])
    assert len(evidence) == 10
    assert sum(step["committed"] for step in evidence) == 4


@pytest.mark.parametrize("mutation", ["commit-before-failure", "swallow-failure"])
def test_atomic_oracle_rejects_broken_rollback_and_suppressed_faults(
    tmp_path: Path, mutation: str, chaos_journal: Any
) -> None:
    chaos_journal.record("canary", mutation=mutation)
    code = f"""
from pathlib import Path
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from unstd import fs
from tests.support.chaos.fs_process import atomic_schedule
original = fs.atomic_write
def broken(path, data, **kwargs):
    if data.startswith(b'recovered:'):
        return original(path, data, **kwargs)
    try:
        return original(path, data, **kwargs)
    except OSError:
        if sys.argv[1] == 'commit-before-failure':
            Path(path).write_bytes(data)
            raise
        return Path(path)
fs.atomic_write = broken
atomic_schedule(Path.cwd(), ('write-enospc',))
"""
    result = run_python(code, [mutation], cwd=tmp_path, timeout=10)
    assert result.returncode != 0
    expected = (
        "destination changed before commit"
        if mutation == "commit-before-failure"
        else "operation exception was lost"
    )
    assert expected in result.stderr
    chaos_journal.record("rejected", mutation=mutation, diagnostic=expected)


@pytest.mark.parametrize("mutation", ["lose-stderr", "suppress-timeout"])
def test_process_oracle_rejects_output_loss_and_timeout_suppression(
    tmp_path: Path, mutation: str, chaos_journal: Any
) -> None:
    chaos_journal.record("canary", mutation=mutation)
    code = f"""
from pathlib import Path
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from unstd import proc
from tests.support.chaos.fs_process import process_schedule
original = proc.run
def broken(*args, **kwargs):
    try:
        result = original(*args, **kwargs)
    except proc.TimeoutExpired:
        if sys.argv[1] == 'suppress-timeout':
            return proc.CompletedProcess(tuple(args[0]), 0, '', '', 0)
        raise
    return proc.CompletedProcess(result.argv, result.returncode, result.stdout, '', result.duration)
proc.run = broken
process_schedule(Path.cwd(), ('timeout' if sys.argv[1] == 'suppress-timeout' else 'success',))
"""
    result = run_python(code, [mutation], cwd=tmp_path, timeout=12)
    assert result.returncode != 0
    expected = (
        "timeout accepted as completion"
        if mutation == "suppress-timeout"
        else "stderr corruption accepted"
    )
    assert expected in result.stderr
    chaos_journal.record("rejected", mutation=mutation, diagnostic=expected)

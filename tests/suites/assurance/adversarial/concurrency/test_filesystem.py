"""Real competing writers follow a gated whole-generation visibility model.

Three independent writer handles overlap partial bodies and pending renames.
Two independent reader actors check the exact model before and after every
commit, including rollback while a competing writer succeeds. Spawned process
actors exercise cross-process visibility; thread actors exercise shared module
state. Neither schedule selection nor correctness depends on elapsed time.
"""

from __future__ import annotations

import json
import os
from queue import Queue
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.chaos import ordered
from tests.support.process import python_worker


if TYPE_CHECKING:
    from pathlib import Path


_WORKER = """
import json
from pathlib import Path
import sys
from tests.support.concurrency_filesystem import atomic_history
options = json.loads(sys.argv[1])
def record(row):
    print(json.dumps({'progress': row}, sort_keys=True), flush=True)
try:
    report = atomic_history(Path.cwd(), tuple(options.pop('order')), record=record, **options)
except AssertionError as error:
    print(json.dumps({'rejected': str(error)}, sort_keys=True), flush=True)
    sys.exit(23)
print(json.dumps({'report': report}, sort_keys=True), flush=True)
"""


def _run_history(directory: Path, journal: Any, **options: Any) -> tuple[int, dict]:
    """Publish live boundaries under an independent 35-second parent deadline."""
    journal.record("schedule", **options, worker_wall_seconds=35)
    frames: Queue = Queue()
    with python_worker(_WORKER, [json.dumps(options)], cwd=directory) as child:
        assert child.stdout is not None

        def collect() -> None:
            assert child.stdout is not None
            for line in child.stdout:
                frames.put(line)
            frames.put(None)

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        deadline = time.monotonic() + 35
        last: dict = {}
        try:
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, "filesystem schedule exceeded its parent deadline"
                line = frames.get(timeout=remaining)
                if line is None:
                    break
                frame = json.loads(line)
                if "progress" in frame:
                    journal.record("worker-boundary", evidence=frame["progress"])
                else:
                    last = frame
            remaining = deadline - time.monotonic()
            assert remaining > 0, "filesystem schedule exceeded its parent deadline"
            child.wait(timeout=remaining)
            reader.join(timeout=1)
            assert not reader.is_alive(), "filesystem evidence reader did not finish"
            assert child.stderr is not None
            diagnostic = child.stderr.read()
            assert last, diagnostic
            return child.returncode, last
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            reader.join(timeout=1)


def _order(seed: int, namespace: str) -> tuple[str, ...]:
    return ordered(
        seed, f"concurrency.filesystem.{namespace}", ("alpha", "beta", "gamma")
    )


def _verify_report(
    report: dict, order: tuple[str, ...], *, failure: str | None, existing: bool
) -> None:
    events = report["events"]
    failed = order[1] if failure else None
    successful = [name for name in order if name != failed]
    assert report["commits"] == successful
    assert report["failed"] == failed
    assert [row["actor"] for row in events if row["event"] == "commit"] == successful
    assert [row["actor"] for row in events if row["event"] == "partial"] == list(order)
    assert len([row for row in events if row["event"] == "prepared"]) == (
        2 if failure == "body" else 3
    )
    rollbacks = [row for row in events if row["event"] == "rollback"]
    assert len(rollbacks) == (1 if failure else 0)
    if failure:
        assert rollbacks[0]["actor"] == failed
        assert rollbacks[0]["boundary"] == failure
        # A successful competing commit precedes rollback, and a later commit
        # proves the failed writer did not poison the other pending handle.
        meaningful = [
            row["event"] for row in events if row["event"] in {"commit", "rollback"}
        ]
        assert meaningful == ["commit", "rollback", "commit"]
    observations = [row for row in events if row["event"] == "reader"]
    expected_per_reader = 2 + 2 * len(successful) + bool(failure)
    assert report["reader_count"] == len(observations) == 2 * expected_per_reader
    for name in ("reader-0", "reader-1"):
        seen = [row for row in observations if row["actor"] == name]
        assert len(seen) == expected_per_reader
        assert sum(row["phase"] == "after-commit" for row in seen) == len(successful)
        assert seen[-1]["sha256"] == report["final_sha256"]
        if not existing:
            assert seen[0]["sha256"] is None
            assert seen[0]["size"] is None
    expected_retained = 2 if existing and os.name == "posix" else 0
    assert report["retained_descriptors"] == expected_retained
    for name in ("reader-0", "reader-1"):
        pinned = [
            row
            for row in events
            if row["event"] == "reader-pinned" and row["actor"] == name
        ]
        retired = [
            row
            for row in events
            if row["event"] == "reader-retired" and row["actor"] == name
        ]
        assert (
            len(pinned)
            == len(retired)
            == (len(successful) if os.name == "posix" else 0)
        )
        assert [row["generation_sha256"] for row in pinned] == [
            row["sha256"] for row in retired
        ]
        if pinned:
            assert pinned[0]["generation_sha256"] == (
                observations[0]["sha256"] if existing else None
            )
            for pin, commit, retire in zip(
                pinned,
                [row for row in events if row["event"] == "commit"],
                retired,
                strict=True,
            ):
                assert events.index(pin) < events.index(commit) < events.index(retire)
    assert events[-1] == {
        "event": "joined",
        "writers": 3,
        "readers": 2,
        "retained_descriptors": expected_retained,
    }


@pytest.mark.parametrize("actor_kind", ["thread", "process"])
@pytest.mark.parametrize("mode", ["binary", "text"])
@pytest.mark.parametrize("durable", [False, True], ids=["rename", "durable"])
@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
def test_overlapping_writers_and_readers_observe_exact_committed_generations(
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: Any,
    actor_kind: str,
    mode: str,
    durable: bool,
    existing: bool,
) -> None:
    order = _order(
        concurrency_seed, f"whole-generations.{actor_kind}.{mode}.{durable}.{existing}"
    )
    code, frame = _run_history(
        tmp_path,
        concurrency_journal,
        order=order,
        actor_kind=actor_kind,
        mode=mode,
        durable=durable,
        existing=existing,
    )
    assert code == 0, frame
    _verify_report(frame["report"], order, failure=None, existing=existing)
    concurrency_journal.record("verified", commits=3, readers=2, observations=16)


@pytest.mark.parametrize("actor_kind", ["thread", "process"])
@pytest.mark.parametrize("mode", ["binary", "text"])
@pytest.mark.parametrize("failure", ["body", "replace"])
def test_failed_writer_rolls_back_between_competing_successful_commits(
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: Any,
    actor_kind: str,
    mode: str,
    failure: str,
) -> None:
    order = _order(concurrency_seed, f"failed-writer.{actor_kind}.{mode}.{failure}")
    code, frame = _run_history(
        tmp_path,
        concurrency_journal,
        order=order,
        actor_kind=actor_kind,
        mode=mode,
        durable=True,
        failure=failure,
    )
    assert code == 0, frame
    _verify_report(frame["report"], order, failure=failure, existing=True)
    concurrency_journal.record("verified", commits=2, rollbacks=1, failure=failure)


@pytest.mark.parametrize("actor_kind", ["thread", "process"])
def test_scratch_name_collision_cannot_mix_open_competing_writer_handles(
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: Any,
    actor_kind: str,
) -> None:
    order = _order(concurrency_seed, f"scratch-collision.{actor_kind}")
    code, frame = _run_history(
        tmp_path,
        concurrency_journal,
        order=order,
        actor_kind=actor_kind,
        collision=True,
    )
    assert code == 0, frame
    _verify_report(frame["report"], order, failure=None, existing=True)
    calls = [
        row["name_calls"]
        for row in frame["report"]["events"]
        if row["event"] == "partial"
    ]
    assert calls[0] == ["shared"]
    assert all(names[0] == "shared" and len(names) == 2 for names in calls[1:])
    concurrency_journal.record("verified", protected_open_scratch=True, collisions=2)


@pytest.mark.parametrize("actor_kind", ["thread", "process"])
@pytest.mark.parametrize("mutation", ["drop-commit", "tear-commit"])
def test_visibility_oracle_rejects_missing_or_torn_production_commits(
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: Any,
    actor_kind: str,
    mutation: str,
) -> None:
    order = _order(concurrency_seed, f"oracle.{actor_kind}.{mutation}")
    code, frame = _run_history(
        tmp_path,
        concurrency_journal,
        order=order,
        actor_kind=actor_kind,
        _mutation=mutation,
    )
    assert code == 23, frame
    assert frame == {"rejected": "committed generation not visible as a whole payload"}
    concurrency_journal.record(
        "rejected", mutation=mutation, diagnostic=frame["rejected"]
    )

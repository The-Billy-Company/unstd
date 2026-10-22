"""Bounded UUID contention workers and an independent interval-history oracle.

Every patched clock, entropy provider, fork and multiprocessing context lives in
a disposable interpreter. The oracle orders UUID fields, then checks actual
invocation/response intervals; completion-list order is deliberately irrelevant.
"""

from __future__ import annotations

from contextlib import suppress
from itertools import pairwise
import json
import multiprocessing
import os
import signal
import sys
import threading
import time
from unittest.mock import patch
from uuid import RFC_4122, UUID


BASE_MS = 1_700_000_000_000
TAIL = int.from_bytes(b"\x25" * 8, "big") & ((1 << 62) - 1)


def validate_history(history, *, expected=None, monotonic=True):
    """Reject malformed, duplicate or impossible sequential UUID histories.

    For a monotonic generator, ascending UUID order is a candidate linearization.
    It is valid iff it respects each thread's program order and every observed
    response-before-invocation edge. Overlapping operations may return in either
    order. Optional expected values pin the complete independently authored model.
    """
    assert history, "empty history"
    calls = set()
    events = set()
    values = []
    by_thread = {}
    for row in history:
        identity = row["thread"], row["sequence"]
        assert identity not in calls, "duplicate invocation identity"
        calls.add(identity)
        assert type(row["start"]) is int
        assert type(row["end"]) is int
        assert row["start"] < row["end"], "response precedes invocation"
        assert row["start"] not in events
        assert row["end"] not in events
        events.update((row["start"], row["end"]))
        value = UUID(row["text"])
        assert value.version == 7, "UUIDv7 layout"
        assert value.variant == RFC_4122, "UUIDv7 layout"
        assert row["text"] == str(value), "canonical lowercase UUID text"
        if row["api"] == "new_hex":
            assert row["raw"] == value.hex, "bare hex API result"
        else:
            assert row["api"] == "new"
            assert row["raw"] == str(value)
        values.append(value.int)
        by_thread.setdefault(row["thread"], []).append((row, value.int))
    assert len(values) == len(set(values)), "duplicate UUID"
    if expected is not None:
        assert sorted(values) == expected, (
            "UUID sequence differs from independent model"
        )
    for rows in by_thread.values():
        rows.sort(key=lambda pair: pair[0]["sequence"])
        assert [row["sequence"] for row, _ in rows] == list(range(len(rows)))
        for (left, first), (right, second) in pairwise(rows):
            assert left["end"] < right["start"], "overlapping same-thread calls"
            if monotonic:
                assert first < second, "per-thread UUID order"
    if monotonic:
        parsed = [(row, UUID(row["text"]).int) for row in history]
        for left, first in parsed:
            for right, second in parsed:
                if left["end"] < right["start"]:
                    assert first < second, "UUID order violates a real-time edge"
    return {"count": len(values), "threads": len(by_thread), "events": len(events)}


def fallback_model(*, count, initial_counter, counter_seed=0, tail=TAIL):
    """Construct the frozen/rollback-clock sequence without product helpers."""
    timestamp, counter = BASE_MS, initial_counter
    values = []
    for _ in range(count):
        counter += 1
        if counter == 4096:
            timestamp += 1
            counter = counter_seed
        values.append(timestamp << 80 | 7 << 76 | counter << 64 | 2 << 62 | tail)
    return values


def _backend_name(uid):
    function = uid._uuid7
    return f"{function.__module__}.{function.__name__}"


def _threads(config):
    from unstd.ids import uid

    forced = config["forced_fallback"]
    threads, count = config["threads"], config["count"]
    profile = config["profile"]
    initial = config["initial_counter"]
    history, failures = [], []
    event_lock = threading.Lock()
    start = threading.Barrier(threads)
    tail_barrier = threading.Barrier(threads)
    local = threading.local()
    sequence = 0

    def event():
        nonlocal sequence
        with event_lock:
            sequence += 1
            return sequence

    def worker(index):
        local.index = index
        try:
            start.wait(timeout=4)
            for ordinal in range(count):
                local.ordinal = ordinal
                method = "new_hex" if (ordinal + index) % 2 else "new"
                row = {
                    "thread": index,
                    "sequence": ordinal,
                    "api": method,
                    "start": event(),
                }
                raw = getattr(uid, method)()
                row.update(end=event(), raw=raw, text=str(UUID(raw)))
                with event_lock:
                    history.append(row)
        except BaseException as caught:  # relay thread failures to the owned parent
            with event_lock:
                failures.append({"thread": index, "error": repr(caught)})

    def clock():
        # All inputs remain <= the initial model timestamp. Rollback placement
        # is local program order, independent of how the host schedules threads.
        offset = (local.ordinal + local.index) % 5 if profile == "rollback" else 0
        return (BASE_MS - offset * 1000) * 1_000_000

    def entropy(size):
        if size == 2:
            return b"\x00\x00"
        assert size == 8
        # Every round holds all return paths open until each thread reserved
        # its UUID. This proves overlap without scheduler sleeps or list-order
        # assumptions; the generator's own counter lock is still unmodified.
        tail_barrier.wait(timeout=4)
        return b"\x25" * size

    def run():
        workers = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in config["order"]
        ]
        for thread in workers:
            thread.start()
        deadline = time.monotonic() + 7
        for thread in workers:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        return [thread.name for thread in workers if thread.is_alive()]

    backend_before = _backend_name(uid)
    if forced:
        uid._last_ms, uid._last_counter = BASE_MS, initial
        with (
            patch.object(uid, "_uuid7", uid._stdlib_uuid7),
            patch.object(uid.time, "time_ns", clock),
            patch.object(uid.os, "urandom", entropy),
        ):
            backend = _backend_name(uid)
            alive = run()
        state = [uid._last_ms, uid._last_counter]
    else:
        backend = _backend_name(uid)
        alive = run()
        state = None
    return {
        "history": history,
        "failures": failures,
        "alive": alive,
        "backend": backend,
        "backend_before": backend_before,
        "state": state,
    }


def _overlap():
    from unstd.ids import uid

    allocated = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    history, failures = [], []
    sequence = 0

    def event():
        nonlocal sequence
        with lock:
            sequence += 1
            return sequence

    def entropy(size):
        if size == 8 and threading.current_thread().name == "first-reservation":
            allocated.set()
            if not release.wait(timeout=4):
                message = "second mint did not release the first reservation"
                raise TimeoutError(message)
        return b"\x00" * size if size == 2 else b"\x25" * size

    def worker(index):
        row = {"thread": index, "sequence": 0, "api": "new", "start": event()}
        try:
            row["raw"] = uid.new()
            row.update(end=event(), text=str(UUID(row["raw"])))
            with lock:
                history.append(row)
        except BaseException as caught:
            failures.append(repr(caught))

    uid._last_ms, uid._last_counter = BASE_MS, 10
    with (
        patch.object(uid, "_uuid7", uid._stdlib_uuid7),
        patch.object(uid.time, "time_ns", lambda: BASE_MS * 1_000_000),
        patch.object(uid.os, "urandom", entropy),
    ):
        first = threading.Thread(
            target=worker, args=(0,), name="first-reservation", daemon=True
        )
        second = threading.Thread(target=worker, args=(1,), daemon=True)
        first.start()
        ready = allocated.wait(timeout=4)
        try:
            if ready:
                second.start()
                second.join(timeout=4)
        finally:
            release.set()
            first.join(timeout=4)
        alive = first.is_alive() or (second.ident is not None and second.is_alive())
    return {"ready": ready, "alive": alive, "history": history, "failures": failures}


def _spawn_child(index, start, sender):
    from tests.support.chaos.journal import apply_backend_policy

    apply_backend_policy()
    from unstd.ids import uid

    try:
        before = [uid._last_ms, uid._last_counter, uid._counter_lock.locked()]
        if not start.wait(timeout=5):
            message = "spawn parent did not release children"
            raise TimeoutError(message)  # noqa: TRY301 - relay this child failure over its owned pipe
        with (
            patch.object(uid, "_uuid7", uid._stdlib_uuid7),
            patch.object(uid.time, "time_ns", lambda: BASE_MS * 1_000_000),
            patch.object(
                uid.os,
                "urandom",
                lambda size: b"\x00\x03" if size == 2 else bytes([index + 1]) * size,
            ),
        ):
            backend = _backend_name(uid)
            minted = [uid.new() for _ in range(32)]
        sender.send(
            {
                "index": index,
                "pid": os.getpid(),
                "before": before,
                "values": minted,
                "backend": backend,
            }
        )
    except BaseException as caught:
        sender.send({"index": index, "error": repr(caught)})
    finally:
        sender.close()


def _spawn(config):
    from unstd.ids import uid

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    uid._last_ms, uid._last_counter = BASE_MS + 777, 999
    children, receivers = [], []
    try:
        # A held parent threading lock must not poison a fresh spawn interpreter.
        with uid._counter_lock:
            for index in config["order"]:
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(
                    target=_spawn_child, args=(index, start, sender)
                )
                process.start()
                sender.close()
                children.append(process)
                receivers.append(receiver)
            start.set()
            deadline = time.monotonic() + 9
            results = []
            for receiver in receivers:
                if not receiver.poll(max(0, deadline - time.monotonic())):
                    message = "spawn UUID worker exceeded its response deadline"
                    raise TimeoutError(message)
                results.append(receiver.recv())
            for process in children:
                process.join(timeout=max(0, deadline - time.monotonic()))
            parent = [uid._last_ms, uid._last_counter, uid._counter_lock.locked()]
        return {
            "children": results,
            "parent": parent,
            "exitcodes": [process.exitcode for process in children],
        }
    finally:
        for process in children:
            if process.is_alive():
                process.kill()
            process.join(timeout=2)
            process.close()
        for receiver in receivers:
            receiver.close()


def _fork(config):
    from unstd.ids import uid

    held, release = threading.Event(), threading.Event()

    def owner():
        with uid._counter_lock:
            held.set()
            release.wait(timeout=8)

    uid._last_ms, uid._last_counter = BASE_MS, 91
    backend_before = _backend_name(uid)
    holder = threading.Thread(target=owner, daemon=True)
    holder.start()
    ready = held.wait(timeout=3)
    if not ready:
        message = "fork lock owner never reached the controlled boundary"
        raise TimeoutError(message)
    reader, writer = os.pipe()
    child = None
    try:
        if config.get("poison_lock"):
            inherited = uid._counter_lock

            def restore_poisoned_lock():
                # Harness canary: reproduce the pre-fix inherited locked object
                # after the production after-fork handler has had its chance.
                uid._counter_lock = inherited

            os.register_at_fork(after_in_child=restore_poisoned_lock)
        with (
            patch.object(uid, "_uuid7", uid._stdlib_uuid7),
            patch.object(uid.time, "time_ns", lambda: BASE_MS * 1_000_000),
            patch.object(
                uid.os,
                "urandom",
                lambda size: b"\x00\x13" if size == 2 else b"\x25" * size,
            ),
        ):
            child = os.fork()
            if child == 0:
                os.close(reader)
                signal.alarm(3)
                try:
                    before = [
                        uid._last_ms,
                        uid._last_counter,
                        uid._counter_lock.locked(),
                    ]
                    values = [uid.new(), uid.new_hex()]
                    payload = {
                        "before": before,
                        "values": values,
                        "backend": _backend_name(uid),
                    }
                    os.write(writer, json.dumps(payload).encode())
                except BaseException as caught:
                    os.write(writer, json.dumps({"error": repr(caught)}).encode())
                    os._exit(2)
                os._exit(0)
            os.close(writer)
            writer = None
            _, status = os.waitpid(child, 0)
            child = None
            encoded = os.read(reader, 8192)
            parent = [uid._last_ms, uid._last_counter, uid._counter_lock.locked()]
    finally:
        release.set()
        holder.join(timeout=2)
        if child is not None:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        os.close(reader)
        if writer is not None:
            os.close(writer)
    return {
        "ready": ready,
        "status": os.waitstatus_to_exitcode(status),
        "child": json.loads(encoded) if encoded else None,
        "parent": parent,
        "holder_alive": holder.is_alive(),
        "seed": config["seed"],
        "backend_before": backend_before,
        "backend_after": _backend_name(uid),
    }


SCENARIOS = {
    "threads": _threads,
    "overlap": lambda _: _overlap(),
    "spawn": _spawn,
    "fork": _fork,
}


def main():
    """Emit observations from one reviewed scenario, never an in-process hang."""
    from tests.support.chaos.journal import apply_backend_policy

    apply_backend_policy()
    observed = SCENARIOS[sys.argv[1]](json.loads(sys.argv[2]))
    print(json.dumps(observed, sort_keys=True))


if __name__ == "__main__":
    main()

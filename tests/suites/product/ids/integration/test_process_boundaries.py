"""Import-time backend selection and fork inheritance in fresh interpreters.

``uid`` binds its generator once at import: ``uuid_utils.uuid7`` when the ``ids``
extra imports, else stdlib ``uuid.uuid7`` (Python 3.14+), else the hand-rolled
fallback. Each order is observed in a disposable worker so import state cannot
leak between cases.

The fork case extends the inherited-lock regression in two ways. The forking
thread itself holds the counter lock, and the parent's counter is saturated at
0xFFF. ``multiprocessing``'s fork context must still give each child an
unlocked, unseeded counter. Expected UUIDs come from the RFC 9562 bit layout.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING
import uuid

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


_SELECT = """
import json, sys, uuid
mode = sys.argv[1]
if mode != "installed":
    sys.modules["uuid_utils"] = None
if mode == "no-stdlib":
    vars(uuid).pop("uuid7", None)
if mode == "stdlib":
    fixed = uuid.UUID("017f22e2-79b0-7cc3-98c4-dc0c0c07398f")
    uuid.uuid7 = lambda: fixed
from unstd.ids import uid
backend = uid._uuid7
print(json.dumps({
    "backend": f"{backend.__module__}.{backend.__qualname__}",
    "is_fallback": backend is uid._stdlib_uuid7,
    "is_stdlib": backend is getattr(uuid, "uuid7", None),
    "new": uid.new(),
    "hex": uid.new_hex(),
}))
"""


def _select(mode: str, tmp_path: Path) -> dict[str, object]:
    completed = run_python(_SELECT, (mode,), cwd=tmp_path, timeout=20)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _assert_v7(text: object, hex_text: object) -> None:
    for parsed in (uuid.UUID(str(text)), uuid.UUID(hex=str(hex_text))):
        assert parsed.version == 7
        assert parsed.variant == uuid.RFC_4122
    assert str(uuid.UUID(str(text))) == text
    assert uuid.UUID(hex=str(hex_text)).hex == hex_text


def test_without_any_native_generator_the_fallback_is_bound(tmp_path: Path) -> None:
    observed = _select("no-stdlib", tmp_path)
    assert observed["is_fallback"] is True
    assert observed["backend"] == "unstd.ids.uid._stdlib_uuid7"
    _assert_v7(observed["new"], observed["hex"])


def test_a_stdlib_uuid7_is_preferred_over_the_fallback(tmp_path: Path) -> None:
    observed = _select("stdlib", tmp_path)
    assert (observed["is_stdlib"], observed["is_fallback"]) == (True, False)
    assert observed["new"] == "017f22e2-79b0-7cc3-98c4-dc0c0c07398f"
    assert observed["hex"] == "017f22e279b07cc398c4dc0c0c07398f"


def test_the_running_interpreter_selects_by_its_own_stdlib(tmp_path: Path) -> None:
    observed = _select("base", tmp_path)
    native = hasattr(uuid, "uuid7")
    assert (observed["is_stdlib"], observed["is_fallback"]) == (native, not native)
    assert observed["backend"] == (
        "uuid.uuid7" if native else "unstd.ids.uid._stdlib_uuid7"
    )
    _assert_v7(observed["new"], observed["hex"])


def test_the_ids_extra_wins_when_installed(tmp_path: Path) -> None:
    require_backend("uuid_utils")
    observed = _select("installed", tmp_path)
    assert str(observed["backend"]).startswith("uuid_utils.")
    assert (observed["is_stdlib"], observed["is_fallback"]) == (False, False)
    _assert_v7(observed["new"], observed["hex"])


_FORK = """
import json, multiprocessing, signal
from unittest.mock import patch
from unstd.ids import uid

BASE = 1_700_000_000_000

def child(index, conn):
    signal.alarm(5)
    seed = bytes([0x0A, index])
    with patch.object(uid.os, "urandom", lambda n: seed if n == 2 else bytes([index]) * n):
        before = [uid._last_ms, uid._last_counter, uid._counter_lock.locked()]
        conn.send({"before": before, "values": [uid.new(), uid.new_hex()]})
    conn.close()

context = multiprocessing.get_context("fork")
uid._last_ms, uid._last_counter = BASE, 0xFFF
inherited = uid._counter_lock
children = []
with (
    patch.object(uid, "_uuid7", uid._stdlib_uuid7),
    patch.object(uid.time, "time_ns", lambda: BASE * 1_000_000),
):
    with uid._counter_lock:
        for index in (1, 2, 3):
            reader, writer = context.Pipe(duplex=False)
            process = context.Process(target=child, args=(index, writer))
            process.start()
            writer.close()
            children.append((process, reader))
        results = []
        for process, reader in children:
            results.append(reader.recv() if reader.poll(8) else None)
            process.join(8)
        held = uid._counter_lock.locked()
print(json.dumps({
    "children": results,
    "exitcodes": [process.exitcode for process, _ in children],
    "held_during_fork": held,
    "parent": [uid._last_ms, uid._last_counter, uid._counter_lock is inherited, uid._counter_lock.locked()],
}))
"""


@pytest.mark.skipif(
    not hasattr(os, "fork") or sys.platform == "win32", reason="POSIX fork context"
)
def test_fork_children_reseed_while_the_forking_thread_holds_a_saturated_counter(
    tmp_path: Path,
) -> None:
    completed = run_python(_FORK, cwd=tmp_path, timeout=30)
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["exitcodes"] == [0, 0, 0]
    assert observed["held_during_fork"] is True
    assert observed["parent"] == [1_700_000_000_000, 0xFFF, True, False]
    minted = []
    for index, child in zip((1, 2, 3), observed["children"], strict=True):
        assert child is not None, f"child {index} never answered"
        assert child["before"] == [None, 0, False]
        tail = int.from_bytes(bytes([index]) * 8) & ((1 << 62) - 1)
        expected = [
            1_700_000_000_000 << 80 | 7 << 76 | counter << 64 | 2 << 62 | tail
            for counter in (0xA00 | index, (0xA00 | index) + 1)
        ]
        text, hex_text = child["values"]
        assert [uuid.UUID(text).int, uuid.UUID(hex=hex_text).int] == expected
        minted.extend(expected)
    assert len(set(minted)) == 6

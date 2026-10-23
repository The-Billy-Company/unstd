"""Stream batching preserves incremental observations and bounded copies."""

from __future__ import annotations

import ctypes
import importlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import weakref

import pytest


blake3 = pytest.importorskip("blake3").blake3
digest = importlib.import_module("unstd.crypto.digest")
_REFERENCE = json.loads(
    Path(__file__).with_name("blake3_reference_vectors.json").read_text()
)


@pytest.fixture(params=[False, True], ids=["python", "native"], autouse=True)
def stream_implementation(request, monkeypatch):
    absorb = None
    if request.param:
        absorb = pytest.importorskip("unstd.crypto._stream_native").absorb
    monkeypatch.setattr(digest, "_native_absorb", absorb)


@pytest.fixture
def native_absorb():
    return pytest.importorskip("unstd.crypto._stream_native").absorb


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
@pytest.mark.parametrize("width", [1, 63, 1023])
@pytest.mark.parametrize(
    "case", _REFERENCE["cases"], ids=lambda case: f"len{case['input_len']}"
)
def test_batched_chunks_match_published_reference_vectors(container, width, case):
    length = case["input_len"]
    data = (bytes(range(251)) * (length // 251 + 1))[:length]
    chunks = container(data[at : at + width] for at in range(0, length, width))
    stream = digest.Stream(chunks)
    expected = case["hash"][:64]
    assert stream.hex() == expected
    assert stream.raw() == bytes.fromhex(expected)
    assert stream.hex(8) == expected[:16]
    assert stream.hex() == expected
    assert stream.update(b"suffix") is stream
    assert stream.raw() == blake3(data + b"suffix").digest()


class _HostileBytes(bytes):
    def __len__(self):
        msg = "buffer acquisition must not call a bytes subclass's __len__"
        raise AssertionError(msg)


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_mixed_buffers_and_bytes_subclasses_keep_backend_acceptance(container):
    parts = container(
        [
            b"prefix",
            bytearray(b"mutable"),
            b"",
            memoryview(b"view"),
            memoryview(b"12345678").cast("B", shape=(2, 4)),
            _HostileBytes(b"subclass"),
            b"suffix",
            b"",
            b"",
        ]
    )
    expected = blake3()
    for part in parts:
        expected.update(part)
    assert digest.Stream(parts).raw() == expected.digest()


class _CallbackBuffer:
    def __init__(self, data, callback):
        self.data = data
        self.callback = callback

    def __buffer__(self, flags):
        self.callback()
        return memoryview(self.data)


def _construction_outcome(target, parts, use_stream):
    """Keep the exact refusal and partial digest from either independent path."""
    error = None
    try:
        if use_stream:
            digest.Stream.__init__(target, parts)
        else:
            for part in parts:
                target.update(part)
    except Exception as caught:
        error = caught
    return error, target.raw() if use_stream else target.digest()


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_mutable_chunks_are_absorbed_before_later_buffer_callbacks(container):
    def run(use_stream):
        target = digest.Stream.__new__(digest.Stream) if use_stream else blake3()
        mutable = bytearray(b"first")
        observed = []

        def mutate():
            observed.append("buffer-enter")
            observed.append(target.raw() if use_stream else target.digest())
            mutable[:] = b"later"

        parts = container(
            [b"prefix", mutable, _CallbackBuffer(b"callback", mutate), b"tail"]
            + [b""] * 5
        )
        error, value = _construction_outcome(target, parts, use_stream)
        return (
            None if error is None else (type(error), str(error)),
            value,
            observed,
            bytes(mutable),
        )

    expected = run(False)
    assert run(True) == expected
    error, value, observed, mutable = expected
    if error is None:
        assert observed == ["buffer-enter", blake3(b"prefixfirst").digest()]
        assert value == blake3(b"prefixfirstcallbacktail").digest()
        assert mutable == b"later"
    else:
        # BLAKE3 1.0.0 retains its mutable Rust borrow during __buffer__.
        assert error == (RuntimeError, "Already mutably borrowed")
        assert observed
        assert observed == ["buffer-enter"] * len(observed)
        assert value == blake3(b"prefixfirst").digest()
        assert mutable == b"first"


@pytest.mark.parametrize("mutation", ["replace-and-append", "remove-prefix"])
def test_buffer_callbacks_observe_and_mutate_lists_like_normal_iteration(mutation):
    def run(use_stream):
        target = digest.Stream.__new__(digest.Stream) if use_stream else blake3()
        observed = []

        def mutate():
            observed.append("buffer-enter")
            observed.append(target.raw() if use_stream else target.digest())
            if mutation == "replace-and-append":
                parts[2] = b"replacement"
                parts.append(b"appended")
            else:
                del parts[0]

        parts = [b"prefix", _CallbackBuffer(b"callback", mutate), b"old", b"tail"] + [
            b""
        ] * 5
        error, value = _construction_outcome(target, parts, use_stream)
        return None if error is None else (type(error), str(error)), value, observed

    expected = run(False)
    assert run(True) == expected
    error, value, observed = expected
    if error is None:
        assert observed == ["buffer-enter", blake3(b"prefix").digest()]
        assert (
            value
            == blake3(
                b"prefixcallbackreplacementtailappended"
                if mutation == "replace-and-append"
                else b"prefixcallbacktail"
            ).digest()
        )
    else:
        assert error == (RuntimeError, "Already mutably borrowed")
        assert observed
        assert observed == ["buffer-enter"] * len(observed)
        assert value == blake3(b"prefix").digest()


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_buffer_callback_refusal_preserves_identity_history_and_partial_state(
    container,
):
    failure = LookupError("buffer callback refused")
    expected_history = None
    for use_stream in (False, True):
        target = digest.Stream.__new__(digest.Stream) if use_stream else blake3()
        observed = []

        def refuse(history=observed):
            history.append("buffer-enter")
            raise failure

        parts = container(
            [
                b"prefix",
                bytearray(b"first"),
                _CallbackBuffer(b"callback", refuse),
                b"tail",
            ]
            + [b""] * 5
        )
        caught, value = _construction_outcome(target, parts, use_stream)
        assert caught is failure
        if use_stream:
            assert observed == expected_history
        else:
            assert observed
            expected_history = observed
        assert value == blake3(b"prefixfirst").digest()


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
@pytest.mark.parametrize("invalid", [object(), "text", memoryview(b"strided")[::2]])
def test_invalid_chunk_leaves_preceding_chunks_absorbed(container, invalid):
    expected = blake3()
    expected.update(b"first")
    expected.update(b"second")
    with pytest.raises((TypeError, BufferError)) as backend_error:
        expected.update(invalid)

    stream = digest.Stream.__new__(digest.Stream)
    with pytest.raises(type(backend_error.value)) as stream_error:
        digest.Stream.__init__(
            stream, container([b"first", b"second", invalid, b"last"] + [b""] * 5)
        )
    assert str(stream_error.value) == str(backend_error.value)
    assert stream.raw() == expected.digest()
    assert stream.update(b"recovered").raw() == blake3(b"firstsecondrecovered").digest()


def test_generator_can_inspect_its_stream_before_resuming():
    stream = digest.Stream.__new__(digest.Stream)
    observed = []

    def parts():
        yield b"first"
        observed.append(stream.raw())
        yield b"second"
        observed.append(stream.hex())
        yield b"third"

    digest.Stream.__init__(stream, parts())
    assert observed == [blake3(b"first").digest(), blake3(b"firstsecond").hexdigest()]
    assert stream.raw() == blake3(b"firstsecondthird").digest()


def test_removed_chunk_finalizer_runs_after_the_next_list_item_is_fetched():
    stream = digest.Stream.__new__(digest.Stream)
    observed = []

    class RemovingBuffer:
        def __buffer__(self, flags):
            del parts[1]
            return memoryview(b"buffer")

        def __del__(self):
            observed.append(stream.raw())
            parts[2] = b"replacement"

    parts = [b"prefix", RemovingBuffer(), b"skipped", b"tail"] + [b""] * 5
    digest.Stream.__init__(stream, parts)
    assert observed == [blake3(b"prefixbuffer").digest()]
    assert stream.raw() == blake3(b"prefixbuffertail").digest()


@pytest.mark.parametrize("base", [tuple, list], ids=["tuple-subclass", "list-subclass"])
def test_container_subclasses_keep_their_eager_iteration_observations(base):
    stream = digest.Stream.__new__(digest.Stream)
    observed = []

    class ObservingContainer(base):
        def __iter__(self):
            yield b"first"
            observed.append(stream.raw())
            yield b"second"

    digest.Stream.__init__(stream, ObservingContainer())
    assert observed == [blake3(b"first").digest()]
    assert stream.raw() == blake3(b"firstsecond").digest()


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_temporary_copies_are_bounded_and_large_chunks_are_forwarded(
    container, monkeypatch
):
    updates = []

    class RecordingHasher:
        def __init__(self, data=b"", **options):
            self.hasher = blake3(data, **options)
            if data:
                updates.append(data)

        def update(self, chunk):
            updates.append(chunk)
            self.hasher.update(chunk)
            return self

        def digest(self, **options):
            return self.hasher.digest(**options)

    monkeypatch.setattr(digest, "blake3", RecordingHasher)
    chunk = bytes(range(256)) * 4
    large = bytes(range(251)) * 10000
    parts = container([chunk] * 4096 + [large] + [chunk] * 4096)
    stream = digest.Stream(parts)
    expected = blake3()
    for part in parts:
        expected.update(part)
    assert stream.raw() == expected.digest()
    assert any(update is large for update in updates)
    # The caller already owns large chunks. Only newly combined temporary data
    # needs a bound, independent of the total number of supplied chunks.
    assert all(len(update) <= 1024 * 1024 for update in updates if update is not large)


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_native_batch_storage_is_immutable_after_update_receives_it(
    native_absorb, container
):
    retained = []
    snapshots = []

    class RetainingHasher:
        def update(self, chunk):
            # A conforming backend need not retain its inputs. Python callbacks
            # may, and no later full or partial batch may mutate exposed bytes.
            assert retained == snapshots
            assert type(chunk) is bytes
            retained.append(chunk)
            snapshots.append(bytes(bytearray(chunk)))

    chunks = container(
        [b"a" * 2048] * 33
        + [b"barrier" * 400]
        + [b"b" * 1023] * 66
        + [b"another barrier" * 200]
        + [b"c"] * 9
    )
    assert native_absorb(RetainingHasher(), chunks) is None
    assert retained == snapshots
    assert b"".join(retained) == b"".join(chunks)
    assert all(len(chunk) <= 65536 for chunk in retained)


@pytest.mark.parametrize("container", [tuple, list], ids=["tuple", "list"])
def test_stream_does_not_retain_supplied_message_objects(container):
    class MutableMessage(bytearray):
        pass

    message = MutableMessage(b"mutable payload")
    view = memoryview(b"view payload")
    message_ref, view_ref = weakref.ref(message), weakref.ref(view)
    exact = bytes(range(251)) * 5
    references = sys.getrefcount(exact)
    stream = digest.Stream(
        container([b"prefix", exact, message, view, b"suffix"] + [b""] * 4)
    )
    assert sys.getrefcount(exact) == references
    del message, view
    assert message_ref() is None
    assert view_ref() is None
    expected = blake3(b"prefix" + exact + b"mutable payloadview payloadsuffix").digest()
    assert stream.raw() == expected
    assert stream.raw() == expected


def test_native_refusal_does_not_inspect_hasher_or_foreign_iterables(native_absorb):
    class Hasher:
        @property
        def update(self):
            msg = "refusal must precede hasher inspection"
            raise AssertionError(msg)

    class List(list):
        def __iter__(self):
            msg = "refusal must not run subclass iteration"
            raise AssertionError(msg)

    class Tuple(tuple):
        def __iter__(self):
            msg = "refusal must not run subclass iteration"
            raise AssertionError(msg)

    for chunks in (None, object(), b"input", iter([b"input"]), List(), Tuple()):
        assert native_absorb(Hasher(), chunks) is NotImplemented


@pytest.mark.parametrize("args", [(), (object(),), (object(), [], object())])
def test_native_argument_count_is_checked_before_dereferencing_arguments(
    native_absorb, args
):
    with pytest.raises(TypeError, match="takes exactly two arguments"):
        native_absorb(*args)


def test_native_rejects_keywords(native_absorb):
    with pytest.raises(TypeError, match="keyword"):
        native_absorb(hasher=blake3(), chunks=[])


def test_native_preserves_update_attribute_failure(native_absorb):
    failure = RuntimeError("cannot acquire update")

    class Hasher:
        @property
        def update(self):
            raise failure

    with pytest.raises(RuntimeError, match="cannot acquire update") as caught:
        native_absorb(Hasher(), [b"input"])
    assert caught.value is failure


@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError, KeyboardInterrupt])
def test_native_update_failures_release_owned_inputs_and_hasher(
    native_absorb, error_type
):
    class Message(bytearray):
        pass

    message = Message(b"failing input")
    failure = error_type("update refused")
    absorbed = blake3()

    class Hasher:
        def update(self, chunk):
            if isinstance(chunk, Message):
                raise failure
            absorbed.update(chunk)

    hasher = Hasher()
    hasher_ref, message_ref = weakref.ref(hasher), weakref.ref(message)
    chunks = [b"first", b"second", message, b"unreachable"]
    with pytest.raises(error_type, match="update refused") as caught:
        native_absorb(hasher, chunks)
    # Rewritten assertions can retain an ExceptionInfo temporary and its
    # traceback on supported CPython versions, obscuring C reference leaks.
    if caught.value is not failure:
        pytest.fail("the native helper replaced the callback's exception")
    assert absorbed.digest() == blake3(b"firstsecond").digest()
    # The Python callback's traceback legitimately owns its frame and arguments.
    # Clear that ownership before checking references held by the C helper.
    failure.__traceback__ = None
    del caught, hasher, chunks, message
    assert hasher_ref() is None
    assert message_ref() is None


def test_native_releases_the_bound_method_and_hasher_after_success(native_absorb):
    class Hasher:
        def __init__(self):
            self.result = blake3()

        def update(self, chunk):
            self.result.update(chunk)

    hasher = Hasher()
    reference = weakref.ref(hasher)
    assert native_absorb(hasher, [b"first", bytearray(b"second"), b"third"]) is None
    assert hasher.result.digest() == blake3(b"firstsecondthird").digest()
    del hasher
    assert reference() is None


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX SIGUSR1")
def test_pending_signals_are_handled_between_c_backend_updates(monkeypatch):
    # PyDLL retains the GIL and calls libc without executing Python bytecode.
    # This makes pending-signal delivery deterministic without timer deadlines.
    raise_signal = getattr(ctypes.PyDLL(None), "raise")
    raise_signal.argtypes = [ctypes.c_int]
    raise_signal.restype = ctypes.c_int

    class SignalHasher:
        update = staticmethod(raise_signal)

    monkeypatch.setattr(digest, "blake3", SignalHasher)
    observed = []
    previous = signal.signal(
        signal.SIGUSR1, lambda signum, _frame: observed.append(signum)
    )
    try:
        digest.Stream([signal.SIGUSR1] * 8)
        assert observed == [signal.SIGUSR1] * 8
    finally:
        signal.signal(signal.SIGUSR1, previous)


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="requires interval timers")
def test_native_empty_chunk_loop_remains_interruptible(native_absorb):
    script = """
import gc
import signal
import time
from blake3 import blake3
from unstd.crypto._stream_native import absorb

class Interrupted(Exception):
    pass

def interrupt(signum, frame):
    for reference in gc.get_referrers(chunks):
        if type(reference) is iterator_type:
            position = reference.__reduce__()[2]
            if 0 < position < len(chunks):
                raise Interrupted('interrupt empty chunk traversal')

chunks = [b''] * 1_000_000
iterator_type = type(iter(chunks))
hasher = blake3()
# Calibrate CPU work, excluding scheduler pauses. macOS can coalesce a 1 ms
# timer past a short traversal. Bound the pointer array to 256 MB and retries
# to three; the outer worker deadline bounds the entire experiment.
maximum = 32_000_000
while True:
    started = time.process_time()
    absorb(hasher, chunks)
    if time.process_time() - started >= 0.1 or len(chunks) == maximum:
        break
    chunks *= 2

signal.signal(signal.SIGALRM, interrupt)
for attempt in range(3):
    signal.setitimer(signal.ITIMER_REAL, 0.01)
    try:
        absorb(hasher, chunks)
    except Interrupted:
        print('INTERRUPTED_DURING_EMPTY_TRAVERSAL')
        break
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if len(chunks) < maximum:
        chunks *= 2
else:
    raise AssertionError('no signal handled inside the native empty-chunk traversal')
"""
    result = subprocess.run(  # noqa: S603  # explicit interpreter and fixture source
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "INTERRUPTED_DURING_EMPTY_TRAVERSAL\n"

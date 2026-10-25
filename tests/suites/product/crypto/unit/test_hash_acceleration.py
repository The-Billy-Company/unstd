"""Parallel hashing preserves serial BLAKE3 results and Python input contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
import inspect
import os
import subprocess
import sys
import textwrap

import pytest


blake3 = pytest.importorskip("blake3").blake3
digest = importlib.import_module("unstd.crypto.digest")
token = importlib.import_module("unstd.crypto.token")
_KEY = bytes(range(32))
_CONTEXT = "unstd parallel hashing contract v1"


def _input(length):
    return (bytes(range(251)) * (length // 251 + 1))[:length]


def _outcome(call):
    try:
        value = call()
    except Exception as error:  # observable backend validation is the contract
        return type(error), str(error)
    return type(value), value


@pytest.mark.parametrize(
    "length", [2**22 - 1, 2**22, 2**22 + 1, 2**23, 2**24, 2**24 + 1]
)
def test_large_inputs_match_the_serial_backend_in_every_mode(length):
    data = _input(length)
    plain = blake3(data, max_threads=1).digest()
    keyed = blake3(data, key=_KEY, max_threads=1).digest()
    derived = blake3(data, derive_key_context=_CONTEXT, max_threads=1).digest()
    assert digest.raw(data) == plain
    assert digest.hex(data) == plain.hex()
    for width in (1, 8, 16, 32):
        assert digest.hex(data, width) == plain[:width].hex()
    assert digest.derive_key(_CONTEXT, data) == derived
    assert token.mac_raw(_KEY, data) == keyed
    assert token.mac_hex(_KEY, data) == keyed.hex()
    assert token.verify_mac(_KEY, data, keyed)
    assert token.verify_mac(_KEY, data, keyed.hex())
    assert not token.verify_mac(_KEY, data, bytes(32))


class _BytesWithHostileLength(bytes):
    def __len__(self):
        msg = "hashing must not invoke a bytes subclass's __len__"
        raise AssertionError(msg)


@pytest.mark.parametrize(
    "data",
    [
        None,
        bytearray(b"mutable input"),
        memoryview(b"contiguous input"),
        memoryview(b"strided input")[::2],
        memoryview(bytes(16)).cast("B", shape=(4, 4)),
        _BytesWithHostileLength(b"subclass input"),
        "not a buffer",
        object(),
    ],
    ids=["none", "bytearray", "view", "strided", "2d", "subclass", "str", "object"],
)
def test_backend_buffer_acceptance_and_errors_are_unchanged(data):
    assert _outcome(lambda: digest.raw(data)) == _outcome(lambda: blake3(data).digest())
    assert _outcome(lambda: digest.hex(data)) == _outcome(
        lambda: blake3(data).hexdigest()
    )
    assert _outcome(lambda: digest.derive_key(_CONTEXT, data)) == _outcome(
        lambda: blake3(data, derive_key_context=_CONTEXT).digest()
    )
    assert _outcome(lambda: token.mac_raw(_KEY, data)) == _outcome(
        lambda: blake3(data, key=_KEY).digest()
    )
    assert _outcome(lambda: token.mac_hex(_KEY, data)) == _outcome(
        lambda: blake3(data, key=_KEY).hexdigest()
    )


@pytest.mark.parametrize("width", [0, -1, 33, 64])
def test_large_input_truncation_errors_keep_their_messages(width):
    with pytest.raises(
        ValueError,
        match=rf"^truncation width must be 1\.\.32 bytes, got {width}$",
    ):
        digest.hex(_input(2**22), width)


def test_truncation_retains_backend_index_conversion_and_validation_order():
    data = _input(2**22)
    assert digest.hex(data, True) == blake3(data).hexdigest(length=True)
    assert _outcome(lambda: digest.hex(data, 1.5)) == _outcome(
        lambda: blake3(data).hexdigest(length=1.5)
    )
    assert _outcome(lambda: digest.hex(object(), 0)) == _outcome(
        lambda: blake3(object()).hexdigest()
    )


@pytest.mark.parametrize("indexed_width", [8, 2**62])
def test_custom_width_keeps_backend_hex_conversion_and_overflow(indexed_width):
    class Width:
        def __ge__(self, other):
            return True

        def __le__(self, other):
            return True

        def __index__(self):
            return indexed_width

    width = Width()
    data = b"custom index conversion"
    expected = _outcome(lambda: blake3(data).hexdigest(length=width))
    assert _outcome(lambda: digest.hex(data, width)) == expected
    assert _outcome(lambda: digest.Stream([data]).hex(width)) == expected


def test_key_width_validation_precedes_any_data_inspection():
    calls = []

    class WrongKey(bytes):
        def __len__(self):
            calls.append("key length")
            return 31

    with pytest.raises(
        ValueError, match=r"^keyed BLAKE3 needs exactly 32 bytes of key, got 31$"
    ):
        token.mac_raw(WrongKey(bytes(32)), _BytesWithHostileLength(b"input"))
    assert calls == ["key length", "key length"]


def test_stream_consumes_each_chunk_before_the_generator_resumes():
    chunk = bytearray(b"first")
    observed = []

    def chunks():
        observed.append("first")
        yield chunk
        chunk[:] = b"later"
        observed.append("later")
        yield chunk

    stream = digest.Stream(chunks())
    assert observed == ["first", "later"]
    assert stream.raw() == blake3(b"firstlater").digest()
    first = stream.hex()
    with pytest.raises(BufferError):
        stream.update(memoryview(b"strided")[::2])
    assert stream.hex() == first
    assert stream.update(b"suffix") is stream
    assert stream.raw() == blake3(b"firstlatersuffix").digest()


def test_concurrent_hashes_do_not_share_message_key_or_context_state():
    data = _input(2**24 + 1)

    def hash_modes(index):
        message = data + bytes([index])
        key = bytes([index]) * 32
        context = f"{_CONTEXT} {index}"
        return (
            digest.raw(message),
            token.mac_raw(key, message),
            digest.derive_key(context, message),
        )

    expected = [
        (
            blake3(data + bytes([i])).digest(),
            blake3(data + bytes([i]), key=bytes([i]) * 32).digest(),
            blake3(data + bytes([i]), derive_key_context=f"{_CONTEXT} {i}").digest(),
        )
        for i in range(8)
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(hash_modes, range(8))) == expected


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.parametrize(
    "import_in_parent", [False, True], ids=["child-import", "inherited"]
)
def test_warmed_hashing_and_an_inherited_stream_remain_usable_after_fork(
    import_in_parent,
):
    # An inherited Rayon pool has no worker threads in the child. Warm it for
    # real before forking, then bound the child's execution so a regression
    # fails instead of hanging the test process.
    script = textwrap.dedent(
        """
        import os
        import signal
        import traceback
        from blake3 import blake3
        import_in_parent = IMPORT_IN_PARENT
        if import_in_parent:
            from unstd.crypto import digest, token

        data = bytes(range(256)) * 65536
        key = bytes(range(32))
        context = "unstd fork contract v1"
        expected = blake3(data).digest()
        keyed = blake3(data, key=key).digest()
        derived = blake3(data, derive_key_context=context).digest()
        assert blake3(data, max_threads=blake3.AUTO).digest() == expected
        if import_in_parent:
            assert digest.raw(data) == expected
            inherited = digest.Stream([b"prefix"])
        child = os.fork()
        if child == 0:
            signal.alarm(5)
            try:
                if not import_in_parent:
                    from unstd.crypto import digest, token
                    inherited = digest.Stream([b"prefix"])
                assert digest.raw(data) == expected
                assert digest.hex(data) == expected.hex()
                assert digest.derive_key(context, data) == derived
                assert token.mac_raw(key, data) == keyed
                assert token.mac_hex(key, data) == keyed.hex()
                assert inherited.update(data).raw() == blake3(b"prefix" + data).digest()
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0, status
        if import_in_parent:
            assert inherited.raw() == blake3(b"prefix").digest()
            assert digest.raw(data) == expected
        """
    ).replace("IMPORT_IN_PARENT", repr(import_in_parent))
    result = subprocess.run(  # noqa: S603  # explicit interpreter and fixture source
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_hashing_signatures_and_stream_shape_are_stable():
    signatures = {
        digest.raw: "(data: 'bytes') -> 'bytes'",
        digest.hex: "(data: 'bytes', n: 'int | None' = None) -> 'str'",
        digest.derive_key: "(context: 'str', material: 'bytes') -> 'bytes'",
        digest.legacy_blake2b: "(data: 'bytes', *, width: 'int') -> 'bytes'",
        digest.Stream: "(chunks: 'Iterable[bytes]' = ()) -> 'None'",
        digest.Stream.update: "(self, chunk: 'bytes') -> 'Self'",
        digest.Stream.raw: "(self) -> 'bytes'",
        digest.Stream.hex: "(self, n: 'int | None' = None) -> 'str'",
        token.mac_raw: "(key: 'bytes', data: 'bytes') -> 'bytes'",
        token.mac_hex: "(key: 'bytes', data: 'bytes') -> 'str'",
        token.verify_mac: "(key: 'bytes', data: 'bytes', tag: 'str | bytes') -> 'bool'",
    }
    for api, signature in signatures.items():
        assert str(inspect.signature(api)) == signature
    assert digest.Stream.__slots__ == ("_hasher",)
    assert digest.__all__ == [
        "DIGEST_BYTES",
        "Stream",
        "derive_key",
        "hex",
        "legacy_blake2b",
        "raw",
    ]


_POOL_PANIC = type("PanicException", (BaseException,), {"__module__": "pyo3_runtime"})
_WRONG_RUNTIME_PANIC = type(
    "PanicException", (BaseException,), {"__module__": "another_runtime"}
)
_WRONG_KIND_PANIC = type(
    "OtherException", (BaseException,), {"__module__": "pyo3_runtime"}
)


@pytest.fixture
def parallel_backend(monkeypatch):
    backend = importlib.import_module("unstd.crypto.hashing")
    # Failure injection does not need a large buffer; the real crossover is
    # independently exercised above. Force multiple workers even on one-CPU CI.
    monkeypatch.setattr(backend, "_PARALLEL_THREADS", 2)
    monkeypatch.setattr(digest, "_PARALLEL_MIN_BYTES", 0)
    monkeypatch.setattr(token, "_PARALLEL_MIN_BYTES", 0)
    return backend


def test_worker_refusal_retries_each_mode_without_retaining_keys(
    parallel_backend, monkeypatch
):
    import weakref

    calls = []

    def refusing_pool(data, *, max_threads=None, **options):
        # Snapshot values without retaining the mutable key object whose
        # lifetime we check below.
        snapshot = {
            name: bytes(value) if name == "key" else value
            for name, value in options.items()
        }
        calls.append((data, max_threads, snapshot))
        if max_threads is not None and max_threads > 1:
            msg = "called Result::unwrap() on an Err value: ThreadPoolBuildError { kind: IOError }"
            raise _POOL_PANIC(msg)
        return blake3(data, **options)

    monkeypatch.setattr(parallel_backend, "blake3", refusing_pool)
    data = b"first independently hashed message"
    other_data = b"second independently hashed message"

    class Key(bytearray):
        pass

    key = Key(range(32))
    other_key = Key(range(1, 33))
    key_ref, other_key_ref = weakref.ref(key), weakref.ref(other_key)
    other_context = _CONTEXT + " other"
    assert digest.raw(data) == blake3(data).digest()
    assert digest.hex(other_data) == blake3(other_data).hexdigest()
    assert (
        digest.derive_key(_CONTEXT, data)
        == blake3(data, derive_key_context=_CONTEXT).digest()
    )
    assert (
        digest.derive_key(other_context, other_data)
        == blake3(other_data, derive_key_context=other_context).digest()
    )
    assert token.mac_raw(key, data) == blake3(data, key=key).digest()
    assert (
        token.mac_hex(other_key, other_data)
        == blake3(other_data, key=other_key).hexdigest()
    )
    expected = [
        (data, {}),
        (other_data, {}),
        (data, {"derive_key_context": _CONTEXT}),
        (other_data, {"derive_key_context": other_context}),
        (data, {"key": bytes(key)}),
        (other_data, {"key": bytes(other_key)}),
    ]
    assert calls == [
        (message, threads, options)
        for message, options in expected
        for threads in (2, None)
    ]
    del key, other_key
    assert key_ref() is None
    assert other_key_ref() is None


@pytest.mark.parametrize(
    "error",
    [
        KeyboardInterrupt("cancel hashing"),
        SystemExit(19),
        ValueError("invalid backend option"),
        RuntimeError("ThreadPoolBuildError"),
        _POOL_PANIC("unrelated Rust panic"),
        _WRONG_RUNTIME_PANIC("ThreadPoolBuildError"),
        _WRONG_KIND_PANIC("ThreadPoolBuildError"),
    ],
    ids=["interrupt", "exit", "validation", "runtime", "other-panic", "module", "kind"],
)
def test_only_the_backend_worker_creation_panic_is_retried(
    parallel_backend, monkeypatch, error
):
    calls = []

    def refuse(data, *, max_threads=None, **options):
        calls.append(max_threads)
        raise error

    monkeypatch.setattr(parallel_backend, "blake3", refuse)
    with pytest.raises(type(error)) as caught:
        digest.raw(b"message")
    assert caught.value is error
    assert calls == [2]


def test_worker_refusal_does_not_hide_a_failed_serial_retry(
    parallel_backend, monkeypatch
):
    calls = []
    failure = ValueError("serial backend failure")

    def refuse(data, *, max_threads=None, **options):
        calls.append(max_threads)
        if max_threads is not None:
            msg = "ThreadPoolBuildError"
            raise _POOL_PANIC(msg)
        raise failure

    monkeypatch.setattr(parallel_backend, "blake3", refuse)
    with pytest.raises(ValueError, match=r"^serial backend failure$") as caught:
        digest.raw(b"message")
    assert caught.value is failure
    assert calls == [2, None]

"""Native inflater argument parsing and bounded retained-input contracts."""

from __future__ import annotations

import struct
import subprocess
import sys
import textwrap
import zlib

import pytest


@pytest.fixture
def native_module():
    return pytest.importorskip("unstd.compress._gzip_native")


@pytest.fixture
def raw_stream():
    payload = b"".join(struct.pack("<I", index) for index in range(10000))
    return payload, zlib.compress(payload, wbits=-15)


def _drain(inflater, size: int = 31) -> bytes:
    chunks = []
    while not inflater.eof:
        chunk = inflater.decompress(b"", max_length=size)
        assert len(chunk) <= size
        assert chunk or inflater.eof, "complete input stopped before DEFLATE EOF"
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize("style", ["positional", "mixed", "keywords", "reversed"])
def test_bounded_argument_shapes_preserve_all_output(native_module, raw_stream, style):
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    if style == "positional":
        first = inflater.decompress(packed, 17)
    elif style == "mixed":
        first = inflater.decompress(packed, max_length=17)
    elif style == "keywords":
        first = inflater.decompress(data=packed, max_length=17)
    else:
        first = inflater.decompress(max_length=17, data=packed)
    assert first == payload[:17]
    assert first + _drain(inflater) == payload


def test_max_length_uses_the_integer_index_protocol(native_module, raw_stream) -> None:
    payload, packed = raw_stream
    calls = []

    class Limit:
        def __index__(self):
            calls.append("index")
            return 17

    inflater = native_module.Inflater()
    first = inflater.decompress(data=packed, max_length=Limit())
    assert calls == ["index"]
    assert first == payload[:17]
    assert first + _drain(inflater) == payload


@pytest.mark.parametrize("maximum", [0, False])
def test_zero_output_limit_retains_input_for_later_drain(
    native_module, raw_stream, maximum
) -> None:
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    assert inflater.decompress(packed, max_length=maximum) == b""
    assert not inflater.eof
    assert not inflater.needs_input
    assert _drain(inflater) == payload


@pytest.mark.parametrize("maximum", [-1, -7])
def test_negative_output_limits_decode_complete_input(
    native_module, raw_stream, maximum: int
) -> None:
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    assert inflater.decompress(data=packed, max_length=maximum) == payload
    assert inflater.eof


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_inflater_keeps_eof_until_explicit_reinitialization(
    native_module, raw_stream, legacy: bool
) -> None:
    payload, packed = raw_stream
    inflater = native_module.Inflater(legacy=legacy)
    assert inflater.decompress(packed + b"following member") == payload
    assert inflater.eof
    assert not inflater.needs_input
    assert inflater.unused_data == b"following member"
    for data in (b"", packed):
        with pytest.raises(EOFError, match="End of stream already reached"):
            inflater.decompress(data)
    assert inflater.unused_data == b"following member"

    inflater.__init__(legacy=legacy)
    assert not inflater.eof
    assert inflater.needs_input
    assert inflater.unused_data == b""
    assert inflater.decompress(packed) == payload
    assert inflater.eof


@pytest.mark.parametrize(
    ("args", "keywords", "error"),
    [
        ((), {}, TypeError),
        ((), {"max_length": 17}, TypeError),
        ((b"", 17, 23), {}, TypeError),
        ((b"",), {"data": b""}, TypeError),
        ((b"", 17), {"max_length": 23}, TypeError),
        ((b"",), {"unknown": 17}, TypeError),
        ((b"",), {"max_length": 17.0}, TypeError),
        ((b"",), {"max_length": "17"}, TypeError),
        ((b"",), {"max_length": None}, TypeError),
        ((b"",), {"max_length": object()}, TypeError),
        ((b"",), {"max_length": sys.maxsize + 1}, OverflowError),
        ((b"",), {"max_length": -sys.maxsize - 2}, OverflowError),
        ((None,), {}, TypeError),
        (("compressed",), {}, TypeError),
        (([1, 2],), {}, TypeError),
        ((memoryview(b"abcdef")[::2],), {}, BufferError),
    ],
)
def test_invalid_arguments_leave_retained_input_usable(
    native_module, raw_stream, args, keywords, error
) -> None:
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    first = inflater.decompress(packed, 17)
    with pytest.raises(error):
        inflater.decompress(*args, **keywords)
    assert first + _drain(inflater) == payload


def test_index_callback_failure_keeps_its_identity_and_decoder_state(
    native_module, raw_stream
) -> None:
    payload, packed = raw_stream
    failure = RuntimeError("application index conversion failed")

    class Limit:
        def __index__(self):
            raise failure

    inflater = native_module.Inflater()
    first = inflater.decompress(packed, 17)
    with pytest.raises(RuntimeError) as caught:
        inflater.decompress(data=b"", max_length=Limit())
    assert caught.value is failure
    assert first + _drain(inflater) == payload


def test_index_callback_must_return_an_integer(native_module, raw_stream) -> None:
    payload, packed = raw_stream

    class Limit:
        def __index__(self):
            return 17.0

    inflater = native_module.Inflater()
    first = inflater.decompress(packed, 17)
    with pytest.raises(TypeError):
        inflater.decompress(b"", max_length=Limit())
    assert first + _drain(inflater) == payload


def test_new_input_joins_retained_bytes_and_mutable_buffers_are_copied(
    native_module, raw_stream
) -> None:
    payload, packed = raw_stream
    boundary = len(packed) // 2
    first_input = bytearray(packed[:boundary])
    second_input = bytearray(packed[boundary:] + b"following member")
    inflater = native_module.Inflater()
    first = inflater.decompress(first_input, max_length=17)
    assert not inflater.needs_input
    first_input[:] = b"\xff" * len(first_input)
    second = inflater.decompress(data=second_input, max_length=17)
    second_input[:] = b"\xff" * len(second_input)
    assert first + second + _drain(inflater) == payload
    assert inflater.unused_data == b"following member"


@pytest.mark.parametrize(
    ("hook", "operation"),
    [
        ("index", "reset"),
        ("acquire", "reset"),
        ("release", "reset"),
        ("acquire", "finish"),
        ("release", "finish"),
    ],
)
def test_reentrant_argument_callbacks_cannot_invalidate_native_decoder(
    native_module, hook: str, operation: str
) -> None:
    # A failed explicit reinitialization can clear the decoder while a Python
    # buffer exporter is being acquired or released. Isolate this native safety
    # regression so an unchecked pointer does not terminate the whole test run.
    script = f"HOOK = {hook!r}\nOPERATION = {operation!r}\n" + textwrap.dedent(
        """\
        import zlib
        from unstd.compress._gzip_native import Inflater

        payload = b"reentrant decoder callback" * 100
        packed = zlib.compress(payload, wbits=-15)
        decoder = Inflater()
        callbacks = []

        def mutate():
            callbacks.append(HOOK)
            if OPERATION == "reset":
                try:
                    decoder.__init__(wbits=-99)
                except ValueError:
                    pass
            else:
                assert decoder.decompress(packed) == payload

        class Source:
            def __buffer__(self, flags):
                if HOOK == "acquire":
                    mutate()
                return memoryview(packed)

            def __release_buffer__(self, view):
                if HOOK == "release":
                    mutate()

        class Limit:
            def __index__(self):
                mutate()
                return 17

        data = packed if HOOK == "index" else Source()
        limit = Limit() if HOOK == "index" else 17
        expected = ValueError if OPERATION == "reset" else EOFError
        try:
            decoder.decompress(data, max_length=limit)
        except expected:
            pass
        else:
            raise AssertionError("callback-invalidated decoder was accepted")
        assert callbacks == [HOOK]
        decoder.__init__()
        assert decoder.decompress(packed) == payload
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed bounded native regression script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_uninitialized_native_instance_is_safe_and_can_be_initialized(
    native_module, raw_stream
) -> None:
    payload, packed = raw_stream
    inflater = native_module.Inflater.__new__(native_module.Inflater)
    assert inflater.unused_data == b""
    assert inflater.unconsumed_tail == b""
    with pytest.raises(ValueError, match="uninitialized inflater"):
        inflater.decompress(packed)
    inflater.__init__()
    assert inflater.decompress(packed) == payload


@pytest.mark.parametrize("style", ["positional", "mixed", "keywords", "reversed"])
def test_direct_output_is_bounded_shared_storage_with_owned_lifetime(
    native_module, raw_stream, style
):
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    storage = bytearray(b"?" * 17)
    if style == "positional":
        result = inflater.decompress_into(packed, storage)
    elif style == "mixed":
        result = inflater.decompress_into(packed, buffer=storage)
    elif style == "keywords":
        result = inflater.decompress_into(data=packed, buffer=storage)
    else:
        result = inflater.decompress_into(buffer=storage, data=packed)
    assert type(result) is memoryview
    assert result.format == "B"
    assert result.obj is storage
    assert bytes(result) == payload[:17]
    assert bytes(result) + _drain(inflater) == payload
    result[0] ^= 0xFF
    assert storage[0] == payload[0] ^ 0xFF
    with pytest.raises(BufferError):
        storage.extend(b"pinned")
    result.release()
    storage.extend(b"released")


@pytest.mark.parametrize("legacy", [False, True])
def test_direct_output_drains_bounded_input_and_preserves_following_bytes(
    native_module, raw_stream, legacy
):
    payload, packed = raw_stream
    inflater = native_module.Inflater(legacy=legacy)
    storage = bytearray(31)
    incoming = packed + b"following member"
    chunks = []
    while not inflater.eof:
        result = inflater.decompress_into(incoming, storage)
        assert 0 < len(result) <= len(storage)
        chunks.append(bytes(result))
        result.release()
        incoming = inflater.unconsumed_tail if legacy else b""
    assert b"".join(chunks) == payload
    assert inflater.unused_data == b"following member"
    assert not inflater.needs_input
    with pytest.raises(EOFError, match="End of stream already reached"):
        inflater.decompress_into(b"", storage)
    inflater.__init__(legacy=legacy)
    assert inflater.decompress(packed) == payload


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_direct_output_retains_input_without_decoding(
    native_module, raw_stream, legacy
):
    payload, packed = raw_stream
    inflater = native_module.Inflater(legacy=legacy)
    result = inflater.decompress_into(packed, bytearray())
    assert bytes(result) == b""
    assert not inflater.eof
    assert not inflater.needs_input
    incoming = inflater.unconsumed_tail if legacy else b""
    assert inflater.decompress(incoming) == payload


def test_direct_output_accepts_writable_contiguous_nonbyte_storage(
    native_module, raw_stream
):
    import array

    payload, packed = raw_stream
    inflater = native_module.Inflater()
    storage = array.array("I", [0] * 4)
    result = inflater.decompress_into(packed, storage)
    assert result.format == "B"
    assert len(result) == 16
    assert bytes(result) == payload[:16]
    assert bytes(result) + _drain(inflater) == payload


@pytest.mark.parametrize(
    ("buffer", "error"),
    [
        (b"readonly", BufferError),
        (memoryview(b"readonly"), BufferError),
        (memoryview(bytearray(32))[::2], BufferError),
        (None, TypeError),
        ("text", TypeError),
        ([0] * 32, TypeError),
    ],
)
def test_invalid_direct_output_keeps_retained_input_usable(
    native_module, raw_stream, buffer, error
):
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    first = inflater.decompress(packed, 17)
    with pytest.raises(error):
        inflater.decompress_into(b"", buffer)
    assert first + _drain(inflater) == payload


@pytest.mark.parametrize(
    ("args", "keywords"),
    [
        ((), {}),
        ((b"",), {}),
        ((), {"buffer": bytearray(17)}),
        ((b"", bytearray(17), 23), {}),
        ((b"", bytearray(17)), {"data": b""}),
        ((b"", bytearray(17)), {"buffer": bytearray(17)}),
        ((b"", bytearray(17)), {"max_length": 17}),
    ],
)
def test_invalid_direct_argument_shapes_keep_retained_input_usable(
    native_module, raw_stream, args, keywords
):
    payload, packed = raw_stream
    inflater = native_module.Inflater()
    first = inflater.decompress(packed, 17)
    with pytest.raises(TypeError):
        inflater.decompress_into(*args, **keywords)
    assert first + _drain(inflater) == payload


def test_direct_output_copies_mutable_input_before_overwriting_it(
    native_module, raw_stream
):
    payload, packed = raw_stream
    storage = bytearray(packed)
    inflater = native_module.Inflater()
    result = inflater.decompress_into(storage, storage)
    first = bytes(result)
    assert first == payload[: len(storage)]
    assert first + _drain(inflater) == payload


def test_direct_output_error_can_write_a_prefix_and_reinitialization_recovers(
    native_module,
):
    payload = b"partial output" * 100
    encoder = zlib.compressobj(wbits=-15)
    packed = encoder.compress(payload) + encoder.flush(zlib.Z_SYNC_FLUSH) + b"\x07"
    storage = bytearray(b"?" * (len(payload) + 128))
    inflater = native_module.Inflater()
    with pytest.raises(zlib.error):
        inflater.decompress_into(packed, storage)
    assert storage[: len(payload)] == payload
    assert storage[len(payload) :] == b"?" * 128
    inflater.__init__()
    assert (
        bytes(inflater.decompress_into(zlib.compress(payload, wbits=-15), storage))
        == payload
    )


@pytest.mark.parametrize("exporter", ["source", "target"])
@pytest.mark.parametrize("operation", ["reset", "finish"])
def test_direct_output_export_callbacks_cannot_invalidate_the_decoder(
    native_module, exporter, operation
):
    script = f"EXPORTER = {exporter!r}\nOPERATION = {operation!r}\n" + textwrap.dedent(
        """\
        import zlib
        from unstd.compress._gzip_native import Inflater

        payload = b"reentrant direct output" * 100
        packed = zlib.compress(payload, wbits=-15)
        decoder = Inflater()
        storage = bytearray(b"?" * 17)
        callbacks = []

        class Exporter:
            def __buffer__(self, flags):
                callbacks.append("acquire")
                if OPERATION == "reset":
                    try:
                        decoder.__init__(wbits=-99)
                    except ValueError:
                        pass
                else:
                    assert decoder.decompress(packed) == payload
                return memoryview(packed if EXPORTER == "source" else storage)

        data = Exporter() if EXPORTER == "source" else packed
        target = Exporter() if EXPORTER == "target" else storage
        expected = ValueError if OPERATION == "reset" else EOFError
        try:
            decoder.decompress_into(data, target)
        except expected:
            pass
        else:
            raise AssertionError("callback-invalidated decoder was accepted")
        assert callbacks == ["acquire"]
        assert storage == b"?" * 17
        decoder.__init__()
        assert decoder.decompress(packed) == payload
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed bounded native regression script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_output_view_keeps_export_alive_until_released(native_module):
    payload = b"owned output export" * 100
    packed = zlib.compress(payload, wbits=-15)
    storage = bytearray(17)
    callbacks = []

    class Exporter:
        def __buffer__(self, flags):
            callbacks.append("acquire")
            return memoryview(storage)

        def __release_buffer__(self, view):
            callbacks.append("release")

    inflater = native_module.Inflater()
    result = inflater.decompress_into(packed, Exporter())
    assert callbacks == ["acquire"]
    assert bytes(result) == payload[:17]
    result.release()
    assert callbacks == ["acquire", "release"]

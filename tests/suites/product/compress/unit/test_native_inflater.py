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

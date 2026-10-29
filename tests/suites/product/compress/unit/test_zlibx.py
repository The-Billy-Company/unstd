"""zlib-compatible wire, bounded state, checksum and optional-backend contracts."""

from __future__ import annotations

import array
import copy
import importlib
import inspect
import runpy
import subprocess
import sys
from types import SimpleNamespace
import zlib

import pytest

from unstd.compress import zlibx


_RAW = b"binary\x00\xff evidence" * 300 + bytes(range(256))
_CALLABLES = (
    "compress",
    "decompress",
    "compressobj",
    "decompressobj",
    "crc32",
    "adler32",
)


def _outcome(call):
    try:
        result = call()
    except Exception as exc:
        return type(exc)
    return type(result)


def _drain(decoder, *, limit=31):
    chunks = []
    while not decoder.eof:
        part = decoder.decompress(decoder.unconsumed_tail, max_length=limit)
        assert 0 < len(part) <= limit
        chunks.append(part)
    return b"".join(chunks)


def test_public_surface_follows_the_running_stdlib():
    assert set(zlibx.__all__) == {
        name for name in dir(zlib) if not name.startswith("_")
    }
    assert zlibx.error is zlib.error
    for name in zlibx.__all__:
        actual, expected = getattr(zlibx, name), getattr(zlib, name)
        assert callable(actual) == callable(expected)
        if not callable(expected) and name not in {
            "ZLIB_VERSION",
            "ZLIB_RUNTIME_VERSION",
        }:
            assert actual == expected
    assert isinstance(zlibx.ZLIB_VERSION, str)
    assert isinstance(zlibx.ZLIB_RUNTIME_VERSION, str)


@pytest.mark.parametrize("function", ["compress", "decompress", "crc32", "adler32"])
def test_function_parameters_keep_names_kinds_and_defaults(function):
    expected = inspect.signature(getattr(zlib, function))
    actual = inspect.signature(getattr(zlibx, function))
    assert [(p.name, p.kind, p.default) for p in actual.parameters.values()] == [
        (p.name, p.kind, p.default) for p in expected.parameters.values()
    ]


@pytest.mark.parametrize("unavailable", ["missing", "incomplete"])
def test_base_or_incomplete_provider_keeps_exact_stdlib_bindings(
    monkeypatch, unavailable
):
    original = importlib.import_module

    def imported(name):
        if name != "zlib_ng.zlib_ng":
            return original(name)
        if unavailable == "missing":
            raise ModuleNotFoundError(name)
        return SimpleNamespace(error=RuntimeError, compress=lambda _data: b"wrong")

    monkeypatch.setattr(importlib, "import_module", imported)
    loaded = runpy.run_path(zlibx.__file__)
    assert loaded["_backend"] is None
    for name in zlibx.__all__:
        assert loaded[name] is getattr(zlib, name)


@pytest.mark.parametrize(
    "name", ["crc32_combine", "adler32_combine", "future_codec_api"]
)
def test_unqualified_new_interpreter_api_is_an_exact_stdlib_binding(monkeypatch, name):
    def combine(crc1, crc2, length):
        return crc1 ^ crc2 ^ length

    monkeypatch.setattr(zlib, name, combine, raising=False)
    loaded = runpy.run_path(zlibx.__file__)
    assert loaded[name] is combine
    assert name in loaded["__all__"]


@pytest.mark.parametrize("wbits", [-15, 15, 31])
@pytest.mark.parametrize("level", [-1, 0, 1, 6, 9])
def test_complete_streams_interoperate_with_independent_stdlib_decoding(wbits, level):
    encoded = zlibx.compress(_RAW, level=level, wbits=wbits)
    assert type(encoded) is bytes
    assert zlib.decompress(encoded, wbits) == _RAW
    assert zlibx.decompress(zlib.compress(_RAW, level, wbits), wbits) == _RAW


def test_omitted_level_retains_the_level_six_default():
    assert zlibx.compress(_RAW) == zlibx.compress(_RAW, level=6)
    compressor = zlibx.compressobj()
    explicit = zlibx.compressobj(level=6)
    assert compressor.compress(_RAW) + compressor.flush() == (
        explicit.compress(_RAW) + explicit.flush()
    )


@pytest.mark.parametrize("wbits", [-15, 15, 31])
@pytest.mark.parametrize("copier", [lambda obj: obj.copy(), copy.copy, copy.deepcopy])
def test_compressor_copy_branches_without_sharing_mutable_state(wbits, copier):
    compressor = zlibx.compressobj(level=6, wbits=wbits)
    prefix = compressor.compress(_RAW) + compressor.flush(zlib.Z_SYNC_FLUSH)
    twin = copier(compressor)
    left = prefix + compressor.compress(b"left") + compressor.flush()
    right = prefix + twin.compress(b"right") + twin.flush()
    assert zlib.decompress(left, wbits) == _RAW + b"left"
    assert zlib.decompress(right, wbits) == _RAW + b"right"


@pytest.mark.skipif(sys.version_info < (3, 15), reason="CPython 3.15 mutex semantics")
def test_python315_compressor_state_is_the_exact_stdlib_factory():
    assert zlibx.compressobj is zlib.compressobj


def test_finished_compressor_copy_protocols_reject_without_terminating_python():
    # A provider's invalid unlock aborts CPython 3.15 before an exception can
    # reach pytest. Keep that regression contained in one deadline-owned child.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import copy
import zlib
from unstd.compress import zlibx

for copier in (lambda value: value.copy(), copy.copy, copy.deepcopy):
    state = zlibx.compressobj()
    wire = state.compress(b'finished stream') + state.flush()
    assert zlib.decompress(wire) == b'finished stream'
    try:
        copier(state)
    except ValueError:
        pass
    else:
        raise AssertionError('finished compressor was copied')
""",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("wbits", [-15, 15, 31])
@pytest.mark.parametrize("copier", [lambda obj: obj.copy(), copy.copy, copy.deepcopy])
def test_decompressor_copy_preserves_budget_tail_and_independent_progress(
    wbits, copier
):
    encoded = zlib.compress(_RAW, wbits=wbits) + b"following bytes"
    decoder = zlibx.decompressobj(wbits=wbits)
    first = decoder.decompress(encoded, max_length=17)
    assert first == _RAW[:17]
    assert not decoder.eof
    assert decoder.unconsumed_tail
    twin = copier(decoder)
    assert first + _drain(decoder) == _RAW
    assert not twin.eof
    assert first + _drain(twin, limit=127) == _RAW
    for instance in (decoder, twin):
        assert instance.unused_data == b"following bytes"
        assert instance.flush() == b""
        assert instance.eof


@pytest.mark.parametrize("wbits", [-15, 15, 31])
def test_zero_decompression_budget_is_unlimited_and_eof_keeps_appending_unused(wbits):
    encoded = zlib.compress(_RAW, wbits=wbits)
    for module in (zlib, zlibx):
        decoder = module.decompressobj(wbits)
        assert decoder.decompress(encoded + b"first tail", max_length=0) == _RAW
        assert decoder.eof
        assert decoder.decompress(b" second tail") == b""
        assert decoder.unused_data == b"first tail second tail"


def test_incremental_decompression_uses_the_interpreters_exact_c_state():
    assert zlibx.decompressobj is zlib.decompressobj


@pytest.mark.parametrize("entry", ["direct", "bounded", "early-flush"])
def test_eof_flush_copy_and_error_state_transitions_follow_running_stdlib(entry):
    encoded = zlib.compress(b"a" * 100) + b"tail"

    def trace(module):
        decoder = module.decompressobj()
        events = []

        def record(operation):
            try:
                result = operation()
            except Exception as exc:
                result = type(exc)
            events.append(
                (result, decoder.eof, decoder.unconsumed_tail, decoder.unused_data)
            )

        limit = 0 if entry == "direct" else 1 if entry == "bounded" else 17
        record(lambda: decoder.decompress(encoded, limit))
        if entry == "bounded":
            while not decoder.eof:
                record(lambda: decoder.decompress(decoder.unconsumed_tail, 1))
        if entry == "early-flush":
            record(lambda: decoder.flush(1))
        else:
            for copier in (lambda value: value.copy(), copy.copy, copy.deepcopy):
                twin = copier(decoder)
                assert twin.eof
                assert twin.unconsumed_tail == decoder.unconsumed_tail
                assert twin.unused_data == decoder.unused_data
        record(lambda: decoder.decompress(b"more"))
        record(decoder.flush)
        record(decoder.flush)
        record(lambda: decoder.decompress(b"last"))
        record(decoder.copy)
        record(lambda: decoder.decompress("invalid buffer"))
        record(lambda: decoder.decompress(b"invalid limit", None))
        return events

    assert trace(zlibx) == trace(zlib)


@pytest.mark.parametrize("length", [1, 127, zlib.DEF_BUF_SIZE])
def test_flush_finishes_buffered_output_and_retains_following_bytes(length):
    encoded = zlib.compress(_RAW) + b"following bytes"
    decoder = zlibx.decompressobj()
    first = decoder.decompress(encoded, 17)
    assert first + decoder.flush(length) == _RAW
    assert decoder.eof
    assert decoder.unused_data == b"following bytes"


@pytest.mark.parametrize("length", [0, -1, None, 1.5, sys.maxsize + 1])
def test_invalid_flush_length_preserves_retained_decoder_state(length):
    encoded = zlib.compress(_RAW)
    expected = _outcome(lambda: zlib.decompressobj().flush(length))
    decoder = zlibx.decompressobj()
    first = decoder.decompress(encoded, 17)
    with pytest.raises(expected):
        decoder.flush(length)
    assert first + _drain(decoder) == _RAW


@pytest.mark.parametrize("wbits", [-15, 15])
def test_predefined_dictionaries_interoperate_in_both_directions(wbits):
    dictionary = b"shared prefix and repeated binary evidence"
    raw = dictionary * 100 + _RAW
    for encoder_module, decoder_module in ((zlib, zlibx), (zlibx, zlib)):
        encoder = encoder_module.compressobj(wbits=wbits, zdict=dictionary)
        packed = encoder.compress(raw) + encoder.flush()
        decoder = decoder_module.decompressobj(wbits=wbits, zdict=dictionary)
        assert decoder.decompress(packed) + decoder.flush() == raw


def test_omitted_dictionary_is_distinct_from_explicit_empty_dictionary_or_none():
    compressor = zlibx.compressobj(wbits=31)
    assert zlib.decompress(compressor.compress(_RAW) + compressor.flush(), 31) == _RAW
    for dictionary in (b"", None):
        expected = _outcome(
            lambda dictionary=dictionary: zlib.compressobj(wbits=31, zdict=dictionary)
        )
        assert (
            _outcome(
                lambda dictionary=dictionary: zlibx.compressobj(
                    wbits=31, zdict=dictionary
                )
            )
            is expected
        )


@pytest.mark.parametrize("method", ["copy", "compress", "flush"])
def test_terminal_compressor_errors_keep_the_exact_stdlib_family(method):
    def operation(module):
        compressor = module.compressobj()
        compressor.compress(_RAW)
        compressor.flush()
        return getattr(compressor, method)(*([b"more"] if method == "compress" else []))

    assert _outcome(lambda: operation(zlibx)) is _outcome(lambda: operation(zlib))


@pytest.mark.parametrize(
    "mode",
    [
        2**31,
        2**32 + zlib.Z_FINISH,
        2**40,
        2**64 + zlib.Z_FINISH,
        -(2**31) - 1,
        -(2**32) + zlib.Z_FINISH,
        -(2**40),
    ],
)
def test_overflowing_flush_modes_cannot_finish_or_consume_a_live_compressor(mode):
    encoder = zlibx.compressobj()
    prefix = encoder.compress(_RAW[:17])
    with pytest.raises(OverflowError):
        encoder.flush(mode)
    encoded = prefix + encoder.compress(_RAW[17:]) + encoder.flush()
    assert zlib.decompress(encoded) == _RAW


@pytest.mark.parametrize(
    "mode", [zlib.Z_SYNC_FLUSH, zlib.Z_FINISH, 2**32 + zlib.Z_FINISH]
)
def test_flush_mode_index_is_called_once_and_overflow_keeps_live_state(mode):
    calls = []

    class Mode:
        def __index__(self):
            calls.append("index")
            return mode

    encoder = zlibx.compressobj()
    prefix = encoder.compress(_RAW)
    if mode > 2**31 - 1:
        with pytest.raises(OverflowError):
            encoder.flush(Mode())
        encoded = prefix + encoder.compress(b"after failure") + encoder.flush()
        expected = _RAW + b"after failure"
    else:
        encoded = prefix + encoder.flush(Mode())
        if mode != zlib.Z_FINISH:
            encoded += encoder.flush()
        expected = _RAW
    assert calls == ["index"]
    assert zlib.decompress(encoded) == expected


@pytest.mark.parametrize(("name", "default"), [("crc32", 0), ("adler32", 1)])
@pytest.mark.parametrize("seed", [0, 1, -1, -(2**400), 2**400, sys.maxsize])
def test_checksums_keep_unsigned_seed_masking_and_exact_integer_results(
    name, default, seed
):
    native, reference = getattr(zlibx, name), getattr(zlib, name)
    assert type(native(_RAW, seed)) is int
    assert native(_RAW, seed) == reference(_RAW, seed)
    assert native(_RAW) == reference(_RAW, default)


@pytest.mark.parametrize("name", ["crc32", "adler32"])
def test_checksum_seed_index_protocol_is_called_once(name):
    calls = []

    class Seed:
        def __index__(self):
            calls.append("index")
            return -1

    assert getattr(zlibx, name)(_RAW, Seed()) == getattr(zlib, name)(_RAW, -1)
    assert calls == ["index"]


def test_compressor_numeric_index_coercion_occurs_once_in_stdlib_order():
    def construct(module):
        events = []

        class Index:
            def __init__(self, name, value):
                self.name, self.value = name, value

            def __index__(self):
                events.append(self.name)
                return self.value

        compressor = module.compressobj(
            level=Index("level", 6),
            method=Index("method", zlib.DEFLATED),
            wbits=Index("wbits", 15),
            memLevel=Index("memLevel", 8),
            strategy=Index("strategy", zlib.Z_DEFAULT_STRATEGY),
        )
        assert zlib.decompress(compressor.compress(_RAW) + compressor.flush()) == _RAW
        return events

    assert (
        construct(zlibx)
        == construct(zlib)
        == [
            "level",
            "method",
            "wbits",
            "memLevel",
            "strategy",
        ]
    )


@pytest.mark.parametrize(
    "data",
    [
        bytearray(_RAW),
        memoryview(_RAW),
        memoryview(_RAW).cast("B", shape=[4, len(_RAW) // 4]),
        array.array("B", _RAW),
        array.array("I", range(100)),
    ],
)
def test_contiguous_buffer_inputs_preserve_wire_bytes_and_checksums(data):
    raw = bytes(memoryview(data).cast("B"))
    assert zlib.decompress(zlibx.compress(data)) == raw
    for name in ("crc32", "adler32"):
        assert getattr(zlibx, name)(data) == getattr(zlib, name)(data)


@pytest.mark.parametrize("data", [None, "text", [1, 2], memoryview(b"abcdef")[::2]])
@pytest.mark.parametrize("name", ["compress", "decompress", "crc32", "adler32"])
def test_invalid_buffers_keep_stdlib_exception_families(name, data):
    assert _outcome(lambda: getattr(zlibx, name)(data)) is _outcome(
        lambda: getattr(zlib, name)(data)
    )


@pytest.mark.parametrize("data", [None, "text", [1, 2], memoryview(b"abcdef")[::2]])
@pytest.mark.parametrize(
    ("factory", "method"),
    [("compressobj", "compress"), ("decompressobj", "decompress")],
)
def test_stateful_buffer_rejections_leave_the_stream_usable(factory, method, data):
    expected = _outcome(lambda: getattr(getattr(zlib, factory)(), method)(data))
    stream = getattr(zlibx, factory)()
    encoded = zlib.compress(_RAW)
    initial = _RAW[:17] if factory == "compressobj" else encoded[:4]
    first = getattr(stream, method)(initial)
    with pytest.raises(expected):
        getattr(stream, method)(data)
    if factory == "compressobj":
        assert (
            zlib.decompress(first + stream.compress(_RAW[17:]) + stream.flush()) == _RAW
        )
    else:
        assert first + stream.decompress(encoded[4:]) + stream.flush() == _RAW


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        ("compress", (_RAW,), {"level": 10}),
        ("compress", (_RAW,), {"level": -2}),
        ("compress", (_RAW,), {"wbits": 99}),
        ("decompress", (b"invalid compressed bytes",), {}),
        ("decompress", (b"",), {"bufsize": -1}),
        ("compressobj", (), {"wbits": 99}),
        ("compressobj", (), {"memLevel": 0}),
        ("compressobj", (), {"strategy": 99}),
        ("compressobj", (), {"zdict": None}),
        ("decompressobj", (), {"wbits": 99}),
        ("decompressobj", (), {"zdict": None}),
        ("crc32", (_RAW, 1.5), {}),
        ("adler32", (_RAW, 1.5), {}),
        ("crc32", (_RAW,), {"value": 1}),
        ("compress", (), {"data": _RAW}),
    ],
)
def test_scalar_argument_validation_preserves_the_stdlib_error_class(
    name, args, kwargs
):
    assert _outcome(lambda: getattr(zlibx, name)(*args, **kwargs)) is _outcome(
        lambda: getattr(zlib, name)(*args, **kwargs)
    )


@pytest.mark.parametrize("maximum", [-1, 1.5, None, sys.maxsize + 1])
def test_bad_output_limits_do_not_consume_retained_stream_state(maximum):
    encoded = zlib.compress(_RAW)
    expected = _outcome(lambda: zlib.decompressobj().decompress(encoded, maximum))
    decoder = zlibx.decompressobj()
    first = decoder.decompress(encoded, 17)
    with pytest.raises(expected):
        decoder.decompress(decoder.unconsumed_tail, max_length=maximum)
    assert first + _drain(decoder) == _RAW


def test_native_codec_error_is_translated_once_without_a_stateless_replay(monkeypatch):
    if zlibx._backend is None:
        pytest.skip("native codec is not installed")
    calls = []
    failure = zlibx._backend.error("native decode failed")

    def decode(*args):
        calls.append(args)
        raise failure

    monkeypatch.setattr(
        zlibx, "_native", SimpleNamespace(error=type(failure), decompress=decode)
    )
    with pytest.raises(zlib.error, match="native decode failed") as caught:
        zlibx.decompress(b"payload")
    assert type(caught.value) is zlib.error
    assert caught.value.__cause__ is failure
    assert len(calls) == 1


def test_native_state_error_is_translated_once_without_a_replay(monkeypatch):
    if zlibx._backend is None:
        pytest.skip("native codec is not installed")
    calls = []
    failure = zlibx._backend.error("native state failed")

    class State:
        def compress(self, data):
            calls.append(data)
            raise failure

    wrapped = zlibx._Compress(State())
    with pytest.raises(zlib.error, match="native state failed") as caught:
        wrapped.compress(b"payload")
    assert type(caught.value) is zlib.error
    assert caught.value.__cause__ is failure
    assert calls == [b"payload"]

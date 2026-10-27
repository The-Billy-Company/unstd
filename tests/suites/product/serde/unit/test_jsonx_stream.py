"""JSON array streams retain stdlib values while reading one bounded window.

The oracle is independently encoded and decoded stdlib JSON. Tiny chunks force
every token, escape and binary code point across input boundaries. The source
histories assert laziness, bounded reads, caller ownership and partial delivery;
no whole-document error offset is required from a bounded-window reader.
"""

from __future__ import annotations

from decimal import Decimal
import gzip
import io
import json
import math
import sys
import tracemalloc

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from tests.support.environment.process import run_python
from unstd.serde import jsonx


_ENCODINGS = [
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "utf-32",
    "utf-32-le",
    "utf-32-be",
]
_PRESENT = jsonx._HAVE_MSGSPEC


@pytest.fixture(params=[False, True], ids=["stdlib", "accelerated"], autouse=True)
def _backend(request, monkeypatch):
    if request.param and not _PRESENT:
        pytest.skip("msgspec (`serde` extra) not installed")
    monkeypatch.setattr(jsonx, "_HAVE_MSGSPEC", request.param)


class _Reader:
    def __init__(self, document, *, short_read=None, bytearray_result=False):
        self.document = document
        self.position = 0
        self.requests = []
        self.short_read = short_read
        self.bytearray_result = bytearray_result
        self.closed = False

    def read(self, size):
        assert size > 0, "streaming requires a positive, sized read"
        self.requests.append(size)
        if self.short_read is not None:
            size = min(size, self.short_read)
        data = self.document[self.position : self.position + size]
        self.position += len(data)
        return bytearray(data) if self.bytearray_result else data

    def close(self):
        self.closed = True


def _same(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(expected, float):
        if math.isnan(expected):
            assert math.isnan(actual)
        else:
            assert actual.hex() == expected.hex()
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    elif isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            _same(actual[key], expected[key])
    else:
        assert actual == expected


def test_base_install_streams_without_backends_or_native_extension(tmp_path):
    code = """
import importlib.abc
import io
import sys

class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'orjson', 'msgspec', 'numpy'} or fullname == 'unstd.serde._json_native':
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, Absent())
from unstd.serde import jsonx
assert not jsonx._HAVE_MSGSPEC and not jsonx._HAVE_ORJSON and not jsonx._HAVE_NUMPY
assert jsonx._native is None
document = '[{"text":"café😀", "nested":[1,null]}, true, 123456789012345678901234567890]'
expected = [{"text":"café😀", "nested":[1,None]}, True, 123456789012345678901234567890]
for encoding in ('utf-8', 'utf-16', 'utf-32'):
    assert list(jsonx.iterload(io.BytesIO(document.encode(encoding)), chunk_size=1)) == expected
assert list(jsonx.iterload(io.StringIO(document), chunk_size=1)) == expected
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("chunk_size", range(1, 18))
@pytest.mark.parametrize("encoding", [None, *_ENCODINGS])
def test_every_small_chunk_preserves_stdlib_values(chunk_size, encoding):
    rows = [
        None,
        True,
        False,
        0,
        -1,
        2**160,
        -0.0,
        1.2345678901234567e-200,
        float("nan"),
        float("inf"),
        -float("inf"),
        'quotes: " backslash: \\ comma: , bracket: ] brace: }',
        "é界😀\u0085\u2028\u2029",
        {"a": [1, {"escaped": '\\\\\\"'}], "b": {}},
        [],
    ]
    text = " \r\n\t" + json.dumps(rows, ensure_ascii=False, indent=2) + "\t\r\n "
    document = text if encoding is None else text.encode(encoding)
    reader = _Reader(document)
    _same(list(jsonx.iterload(reader, chunk_size=chunk_size)), json.loads(document))
    assert reader.position == len(document)
    assert not reader.closed
    allowance = chunk_size if encoding is None else max(4, chunk_size)
    assert all(size <= allowance for size in reader.requests)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 7, 31])
@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_short_binary_reads_and_bytearray_results(chunk_size, encoding):
    document = '["😀", {"界": [0, -0.0]}, true]'.encode(encoding)
    reader = _Reader(document, short_read=1, bytearray_result=True)
    _same(list(jsonx.iterload(reader, chunk_size=chunk_size)), json.loads(document))
    assert not reader.closed


@pytest.mark.parametrize("document", ["[]", "[ ]", "\r\n\t [] \r\n\t"])
@pytest.mark.parametrize("binary", [False, True])
def test_empty_arrays(document, binary):
    reader = _Reader(document.encode() if binary else document, short_read=1)
    assert list(jsonx.iterload(reader, chunk_size=3)) == []
    assert reader.position == len(reader.document)


@pytest.mark.parametrize(
    "document",
    [
        "",
        " ",
        "null",
        "0",
        "{}",
        '"array"',
        "[",
        "[ ",
        "[,1]",
        "[1,]",
        "[1,,2]",
        "[1 2]",
        "[true false]",
        "[01]",
        "[1e]",
        "[1e+]",
        "[--1]",
        "[NaNfoo]",
        "[tru]",
        '["unterminated]',
        '["escape\\"]',
        '["bad\\x20"]',
        '["bad\\u12"]',
        '["control\x00"]',
        "[{}",
        "[{]",
        "[[}]",
        "[{}{}]",
        "[1}\n",
        "[1]x",
        "[1][2]",
        "[] null",
        "\ufeff[]",
        "\v[]",
        "[\v1]",
        "[1\u00a0]",
        "[]\u2028",
    ],
)
@pytest.mark.parametrize("chunk_size", [1, 3, 64])
def test_malformed_framing_or_items_raise_json_decode_error(document, chunk_size):
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(_Reader(document), chunk_size=chunk_size))


@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_binary_bom_and_trailing_corruption_follow_document_framing(encoding):
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(io.BytesIO("[1] false".encode(encoding)), chunk_size=1))


@pytest.mark.parametrize("document", [b'["\xff"]', b"[\xff]", b"[1]\xff"])
def test_invalid_binary_encoding_raises_unicode_error(document):
    with pytest.raises(UnicodeDecodeError):
        list(jsonx.iterload(_Reader(document), chunk_size=1))


@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
@pytest.mark.parametrize(
    "document",
    [
        b"\xff[1]",
        b"[\xff]",
        b"[1,\xff]",
        b'[{"text":"\xff"}]',
        b"[1]\xff",
        b"[]\xff",
        b'["\x80"]',
        b'["\xc0\xaf"]',
        b'["\xf4\x90\x80\x80"]',
        b"[1]\xc2",
        b"[1]\xe2\x82",
        b"[]\xf0\x9f\x98",
    ],
)
def test_invalid_utf8_in_every_array_state_keeps_unicode_decode_errors(
    document, chunk_size
):
    with pytest.raises(UnicodeDecodeError):
        list(jsonx.iterload(_Reader(document), chunk_size=chunk_size))


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 65536])
@pytest.mark.parametrize(
    "document",
    [
        "é[1]".encode(),
        "[é]".encode(),
        "[1]é😀".encode(),
        "[]\ud800".encode(errors="surrogatepass"),
    ],
)
def test_valid_utf8_with_non_json_text_still_raises_json_decode_error(
    document, chunk_size
):
    with pytest.raises(json.JSONDecodeError):
        json.loads(document)
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(_Reader(document), chunk_size=chunk_size))


@pytest.mark.parametrize("split", [1, 2, 3])
def test_default_binary_window_can_split_an_astral_utf8_code_point(split):
    prefix = b'[{"text":"'
    padding = b"a" * (65536 - split - len(prefix))
    document = prefix + padding + "😀".encode() + b'"},2]'
    assert list(jsonx.iterload(_Reader(document))) == json.loads(document)


@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_raw_utf8_surrogates_still_use_stdlib_compatible_fallback(chunk_size):
    document = '[{"value":"\ud800"},"\udc00","\ud83d\ude00"]'.encode(
        errors="surrogatepass"
    )
    _same(
        list(jsonx.iterload(_Reader(document), chunk_size=chunk_size)),
        json.loads(document),
    )


@pytest.mark.parametrize("encoding", [None, *_ENCODINGS])
def test_raw_surrogate_text_and_binary_preserve_stdlib_behavior(encoding):
    text = '["\ud800", "\udc00", "\ud83d\ude00"]'
    document = (
        text if encoding is None else text.encode(encoding, errors="surrogatepass")
    )
    actual = list(jsonx.iterload(_Reader(document), chunk_size=1))
    _same(actual, json.loads(document))


@pytest.mark.parametrize("document", [b"", b" ", b"[", b"[1,", b"[tru]", b"[1,]"])
def test_binary_truncation_and_malformed_items_raise_json_decode_error(document):
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(_Reader(document), chunk_size=1))


def test_creation_is_lazy_and_first_item_does_not_drain_the_stream():
    reader = _Reader('[{"first":1},' + '"tail"' + ",0" * 100_000 + "]")
    rows = jsonx.iterload(reader, chunk_size=31)
    assert reader.requests == []
    assert next(rows) == {"first": 1}
    assert reader.position <= 31
    rows.close()
    assert not reader.closed
    assert reader.position <= 31


@pytest.mark.parametrize("tail", [",broken]", "] trailing", "", ","])
def test_valid_prefix_may_be_delivered_before_later_corruption(tail):
    rows = jsonx.iterload(_Reader('[{"ok":1}' + tail), chunk_size=1)
    # An item needs a framing delimiter before it can be delivered; a truncated
    # array can therefore fail before or after delivering its last complete item.
    if tail in ("", ","):
        with pytest.raises(json.JSONDecodeError):
            list(rows)
        return
    assert next(rows) == {"ok": 1}
    with pytest.raises(json.JSONDecodeError):
        list(rows)


def test_error_after_last_item_is_discovered_only_on_exhaustion():
    reader = _Reader("[1]garbage", short_read=1)
    rows = jsonx.iterload(reader, chunk_size=1)
    assert next(rows) == 1
    assert reader.position <= 4
    with pytest.raises(json.JSONDecodeError):
        next(rows)
    assert not reader.closed


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("chunk_size", [1, 7, 31])
def test_real_gzip_streams_match_stdlib_and_keep_caller_ownership(
    tmp_path, binary, chunk_size
):
    rows = [{"text": "café 界 😀", "nested": [1, None]}, "tail", -0.0]
    raw = json.dumps(rows, ensure_ascii=False).encode()
    path = tmp_path / "records.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    options = {} if binary else {"encoding": "utf-8"}
    with gzip.open(path, "rb" if binary else "rt", **options) as reader:
        actual = list(jsonx.iterload(reader, chunk_size=chunk_size))
        _same(actual, json.loads(raw))
        assert not reader.closed
        assert reader.read(1) == (b"" if binary else "")
    with gzip.open(path, "rb" if binary else "rt", **options) as reader:
        stream = jsonx.iterload(reader, chunk_size=chunk_size)
        assert next(stream) == rows[0]
        stream.close()
        assert not reader.closed


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("corruption", ["crc", "truncated"])
def test_real_gzip_corrupt_trailers_propagate_on_full_exhaustion(
    tmp_path, binary, corruption
):
    packed = bytearray(gzip.compress(b'[{"ok":1},2]', mtime=0))
    if corruption == "crc":
        packed[-8] ^= 1
    else:
        del packed[-5:]
    path = tmp_path / "corrupt.json.gz"
    path.write_bytes(packed)
    options = {} if binary else {"encoding": "utf-8"}
    with gzip.open(path, "rb" if binary else "rt", **options) as reader:
        # A decompressor may return an earlier row before checking its trailer,
        # or validate its buffered bytes before returning the first text chunk.
        with pytest.raises((gzip.BadGzipFile, EOFError)):
            list(jsonx.iterload(reader, chunk_size=1))
        assert not reader.closed


def test_hooks_run_once_per_value_and_keep_order_and_types():
    numbers, floats, constants, pairs = [], [], [], []

    def integer(text):
        numbers.append(text)
        return ("int", text)

    def real(text):
        floats.append(text)
        return Decimal(text)

    def constant(text):
        constants.append(text)
        return ("constant", text)

    def object_pairs(values):
        pairs.append(values)
        return ("pairs", values)

    document = '[{"b":12,"b":13,"a":{"c":1.25}},-0,NaN,Infinity,-Infinity]'
    actual = list(
        jsonx.iterload(
            _Reader(document),
            chunk_size=1,
            parse_int=integer,
            parse_float=real,
            parse_constant=constant,
            object_pairs_hook=object_pairs,
        )
    )
    assert numbers == ["12", "13", "-0"]
    assert floats == ["1.25"]
    assert constants == ["NaN", "Infinity", "-Infinity"]
    assert pairs == [
        [("c", Decimal("1.25"))],
        [
            ("b", ("int", "12")),
            ("b", ("int", "13")),
            ("a", ("pairs", [("c", Decimal("1.25"))])),
        ],
    ]
    assert actual[1:] == [
        ("int", "-0"),
        ("constant", "NaN"),
        ("constant", "Infinity"),
        ("constant", "-Infinity"),
    ]


def test_custom_decoder_and_object_hook_are_forwarded_per_item():
    decoded, hooked = [], []

    class Decoder(json.JSONDecoder):
        def decode(self, text, **kwargs):
            decoded.append(text)
            return super().decode(text, **kwargs)

    def hook(value):
        hooked.append(value)
        return ("object", value)

    document = '[{"a":1},[{}, {"b":2}],"end"]'
    actual = list(
        jsonx.iterload(_Reader(document), chunk_size=2, cls=Decoder, object_hook=hook)
    )
    assert actual == [
        ("object", {"a": 1}),
        [("object", {}), ("object", {"b": 2})],
        "end",
    ]
    assert len(decoded) == 3
    assert hooked == [{"a": 1}, {}, {"b": 2}]


def test_non_strict_control_characters_and_surrogates_match_stdlib():
    document = '["control\x00", "\\ud800", "\\ud83d\\ude00", "\\udc00"]'
    actual = list(jsonx.iterload(_Reader(document), chunk_size=1, strict=False))
    _same(actual, json.loads(document, strict=False))


def test_hook_failure_is_not_wrapped_retried_or_suppressed():
    failure = RuntimeError("conversion failed")
    calls = []

    def hook(value):
        calls.append(value)
        raise failure

    with pytest.raises(RuntimeError) as caught:
        list(jsonx.iterload(_Reader('[{"a":1},{}]'), chunk_size=1, object_hook=hook))
    assert caught.value is failure
    assert calls == [{"a": 1}]


def test_later_invalid_item_does_not_replay_earlier_hooks():
    calls = []

    def hook(value):
        calls.append(value)
        return value

    rows = jsonx.iterload(
        _Reader('[{"ok":1}, {"bad": }]'), chunk_size=2, object_hook=hook
    )
    assert next(rows) == {"ok": 1}
    with pytest.raises(json.JSONDecodeError):
        list(rows)
    assert calls == [{"ok": 1}]


@pytest.mark.parametrize(
    "suffix",
    ["broken]", '{"missing":}]', "1e+]", "01]", '"bad\\x"]', "[}]", ",2]"],
)
def test_invalid_later_item_in_one_chunk_preserves_the_valid_prefix(suffix):
    reader = _Reader('[{"ok":1},2,' + suffix)
    rows = jsonx.iterload(reader, chunk_size=65536)
    assert next(rows) == {"ok": 1}
    assert next(rows) == 2
    with pytest.raises(json.JSONDecodeError):
        next(rows)
    assert not reader.closed


def test_one_chunk_trailing_junk_is_rejected_after_all_valid_items():
    reader = _Reader('[{"ok":1},2] trailing')
    rows = jsonx.iterload(reader, chunk_size=65536)
    assert next(rows) == {"ok": 1}
    assert next(rows) == 2
    with pytest.raises(json.JSONDecodeError):
        next(rows)
    assert not reader.closed


def test_hooks_are_lazy_even_when_later_items_are_already_buffered():
    calls = []

    def hook(value):
        calls.append(value)
        return value

    reader = _Reader('[{"id":0},{"id":1},{"id":2}]')
    rows = jsonx.iterload(reader, chunk_size=65536, object_hook=hook)
    assert calls == []
    assert next(rows) == {"id": 0}
    assert calls == [{"id": 0}]
    assert next(rows) == {"id": 1}
    assert calls == [{"id": 0}, {"id": 1}]
    rows.close()
    assert calls == [{"id": 0}, {"id": 1}]
    assert not reader.closed


def test_custom_decoder_is_called_lazily_for_each_buffered_item():
    calls = []

    class Decoder(json.JSONDecoder):
        def decode(self, text, **kwargs):
            calls.append(text)
            return super().decode(text, **kwargs)

    rows = jsonx.iterload(
        _Reader('[{"id":0},{"id":1},{"id":2}]'), chunk_size=65536, cls=Decoder
    )
    assert next(rows) == {"id": 0}
    assert calls == ['{"id":0}']
    assert next(rows) == {"id": 1}
    assert calls == ['{"id":0}', '{"id":1}']
    rows.close()
    assert len(calls) == 2


def test_one_chunk_hook_failure_does_not_decode_or_replay_other_items():
    calls = []
    failure = RuntimeError("stop at second item")

    def hook(value):
        calls.append(value)
        if value["id"] == 1:
            raise failure
        return value

    rows = jsonx.iterload(
        _Reader('[{"id":0},{"id":1},{"id":2}]'),
        chunk_size=65536,
        object_hook=hook,
    )
    assert next(rows) == {"id": 0}
    with pytest.raises(RuntimeError) as caught:
        next(rows)
    assert caught.value is failure
    assert calls == [{"id": 0}, {"id": 1}]


def test_replaced_public_decoder_is_not_bypassed_by_buffered_batching(monkeypatch):
    original = jsonx.loads
    calls = []

    def decode(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(jsonx, "loads", decode)
    rows = jsonx.iterload(_Reader('[{"id":0},{"id":1}]'), chunk_size=65536)
    assert next(rows) == {"id": 0}
    assert calls == ['{"id":0}']
    assert next(rows) == {"id": 1}
    assert calls == ['{"id":0}', '{"id":1}']
    with pytest.raises(StopIteration):
        next(rows)


def test_replacing_decoder_with_native_bound_encoder_retains_per_item_behavior(
    monkeypatch,
):
    encode = jsonx.dumpb
    expected = [encode("1"), encode("2"), encode("3")]
    monkeypatch.setattr(jsonx, "loads", encode)
    assert list(jsonx.iterload(_Reader("[1,2,3]"), chunk_size=65536)) == expected


@pytest.mark.parametrize("encoding", [None, *_ENCODINGS])
def test_many_buffered_edge_values_match_stdlib(encoding):
    values = [
        2**53 + 1,
        -(2**64) - 1,
        2**200,
        1.2345678901234567e-200,
        -0.0,
        float("nan"),
        float("inf"),
        -float("inf"),
        "é界😀\u2028\u2029",
        {"empty": [], "escaped": '\\"[,]{}', "deep": [{"value": 1}]},
    ] * 25
    text = json.dumps(values, ensure_ascii=False)
    document = text if encoding is None else text.encode(encoding)
    _same(
        list(jsonx.iterload(_Reader(document), chunk_size=65536)),
        json.loads(document),
    )


def test_buffered_items_do_not_trigger_another_source_read():
    reader = _Reader("[0,1,2," + "3," * 1000 + "4]", short_read=16)
    rows = jsonx.iterload(reader, chunk_size=65536)
    assert next(rows) == 0
    requests = reader.requests.copy()
    assert next(rows) == 1
    assert next(rows) == 2
    assert reader.requests == requests
    rows.close()
    assert not reader.closed


def test_integer_digit_limit_failure_does_not_hide_a_valid_buffered_prefix():
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        too_large = "1" * 641
        with pytest.raises(ValueError, match="640") as expected:
            json.loads(too_large)
        rows = jsonx.iterload(_Reader("[1," + too_large + "]"), chunk_size=65536)
        assert next(rows) == 1
        with pytest.raises(type(expected.value)) as actual:
            next(rows)
        assert str(actual.value) == str(expected.value)
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("document", ['[{"a":1}', "[1", "[1.25", "[NaN"])
def test_unframed_final_item_does_not_invoke_decode_hooks(document):
    calls = []

    def hook(value):
        calls.append(value)
        return value

    with pytest.raises(json.JSONDecodeError):
        list(
            jsonx.iterload(
                _Reader(document),
                chunk_size=1,
                object_hook=hook,
                parse_int=hook,
                parse_float=hook,
                parse_constant=hook,
            )
        )
    assert calls == []


def test_source_failure_propagates_and_caller_keeps_ownership():
    failure = OSError("read failed")

    class FailingReader(_Reader):
        def read(self, size):
            if self.position >= 3:
                raise failure
            return super().read(size)

    reader = FailingReader("[1,2]", short_read=1)
    rows = jsonx.iterload(reader, chunk_size=1)
    assert next(rows) == 1
    with pytest.raises(OSError, match="read failed") as caught:
        next(rows)
    assert caught.value is failure
    assert not reader.closed


@pytest.mark.parametrize("chunk_size", [0, -1, -100])
def test_nonpositive_chunk_sizes_are_rejected(chunk_size):
    reader = _Reader("[]")
    with pytest.raises(ValueError, match=r"."):
        list(jsonx.iterload(reader, chunk_size=chunk_size))
    assert reader.requests == []


@pytest.mark.parametrize("chunk_size", [1.0, "1", None, object()])
def test_chunk_size_requires_the_integer_index_protocol(chunk_size):
    reader = _Reader("[]")
    with pytest.raises(TypeError):
        list(jsonx.iterload(reader, chunk_size=chunk_size))
    assert reader.requests == []


def test_chunk_size_accepts_index_objects():
    class Size:
        def __index__(self):
            return 3

    reader = _Reader("[1,2]")
    assert list(jsonx.iterload(reader, chunk_size=Size())) == [1, 2]
    assert all(size <= 3 for size in reader.requests)


@pytest.mark.parametrize("result", [None, 0, [], memoryview(b"[]")])
def test_invalid_read_results_are_rejected(result):
    class Reader:
        def read(self, size):
            return result

    with pytest.raises(TypeError):
        list(jsonx.iterload(Reader(), chunk_size=2))


@pytest.mark.parametrize(
    "pieces",
    [["[", b"]", b""], [b"[", "]", ""], ["[]", b""], [b"[]", ""]],
)
def test_a_stream_cannot_change_between_text_and_binary(pieces):
    source = iter(pieces)

    class Reader:
        def read(self, size):
            return next(source)

    with pytest.raises(TypeError):
        list(jsonx.iterload(Reader(), chunk_size=1))


@pytest.mark.parametrize(
    ("pieces", "expected"),
    [
        ([(False, "[1,"), (True, "]")], None),
        ([(False, "[1]"), (True, "]")], None),
        ([(False, "[1]"), (True, " \t\r\n")], [1]),
        ([(True, '[{"value":1},2]')], [{"value": 1}, 2]),
        ([(False, "[1,"), (True, ",2]")], None),
        ([(False, "[1,"), (True, "2]")], [1, 2]),
        ([(False, "["), (True, "]")], []),
        ([(False, "["), (True, "1]")], [1]),
        ([(False, " \t"), (True, "[1]")], [1]),
        ([(False, "[1,"), (True, " "), (False, "]")], None),
        ([(False, "[1,"), (True, " "), (True, "]")], None),
    ],
)
def test_string_subclass_chunks_preserve_all_array_framing_states(pieces, expected):
    class Text(str):
        pass

    chunks = iter(Text(text) if subclass else text for subclass, text in pieces)

    class Reader:
        def read(self, size):
            assert size > 0
            return next(chunks, "")

    if expected is None:
        with pytest.raises(json.JSONDecodeError):
            list(jsonx.iterload(Reader(), chunk_size=65536))
    else:
        assert list(jsonx.iterload(Reader(), chunk_size=65536)) == expected


@pytest.mark.parametrize("base", [bytes, bytearray])
def test_binary_subclass_read_chunks_use_the_buffer_without_bytes_coercion(base):
    calls = []

    class Chunk(base):
        def __bytes__(self):
            calls.append("bytes")
            return b"999]"

    # An exact initial read completes encoding detection. Later chunks should
    # retain buffer semantics, as the incremental decoder does, rather than
    # invoking an arbitrary __bytes__ conversion and changing the JSON input.
    chunks = iter((b"[1, ", Chunk(b"2]"), b""))

    class Reader:
        def read(self, size):
            assert size > 0
            return next(chunks, b"")

    assert list(jsonx.iterload(Reader())) == [1, 2]
    assert calls == []


def test_large_record_is_decoded_once_after_all_its_chunks():
    calls = []

    class Decoder(json.JSONDecoder):
        def decode(self, text, **kwargs):
            calls.append(len(text))
            return super().decode(text, **kwargs)

    record = {"value": 'escaped \\" [,] 😀 ' * 20_000}
    text = json.dumps([record, 2], ensure_ascii=False)
    reader = _Reader(text, short_read=19)
    assert list(jsonx.iterload(reader, chunk_size=31, cls=Decoder)) == [record, 2]
    assert len(calls) == 2
    assert sum(reader.requests) <= 2 * len(text)


def test_long_whitespace_does_not_accumulate_the_document():
    class WhitespaceReader:
        def __init__(self):
            self.left = 300_000
            self.stage = 0

        def read(self, size):
            assert 0 < size <= 127
            if self.stage == 0:
                self.stage = 1
                return "["
            if self.left:
                count = min(size, self.left)
                self.left -= count
                return " " * count
            if self.stage == 1:
                self.stage = 2
                return "]"
            return ""

    tracemalloc.start()
    try:
        assert list(jsonx.iterload(WhitespaceReader(), chunk_size=127)) == []
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 150_000


def test_long_inter_item_whitespace_preserves_the_required_separator():
    padding = " " * 250_000
    valid = "[1" + padding + ",2]"
    assert list(jsonx.iterload(_Reader(valid), chunk_size=127)) == json.loads(valid)
    invalid = "[1" + padding + "2]"
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(_Reader(invalid), chunk_size=127))


_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**128), max_value=2**128)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=20,
)


@settings(max_examples=60)
@given(
    rows=st.lists(_JSON, max_size=8),
    chunk_size=st.integers(min_value=1, max_value=49),
    encoding=st.sampled_from([None, *_ENCODINGS]),
)
def test_generated_arrays_match_independent_stdlib_oracle(rows, chunk_size, encoding):
    text = json.dumps(rows, ensure_ascii=True)
    document = text if encoding is None else text.encode(encoding)
    actual = list(jsonx.iterload(_Reader(document), chunk_size=chunk_size))
    _same(actual, json.loads(document))


_LEXEMES = st.sampled_from(
    [
        "[",
        "]",
        "{",
        "}",
        ",",
        ":",
        '"',
        "\\",
        '"text"',
        '"\\u1234"',
        '"\\u12"',
        "0",
        "-0",
        "01",
        "1.25",
        "1e-3",
        "1e+",
        "true",
        "false",
        "null",
        "NaN",
        "Infinity",
        "-Infinity",
        " ",
        "\t",
        "\r\n",
        "\u00a0",
        "\x00",
        "é",
        "😀",
    ]
)


@settings(max_examples=200)
@given(
    lexemes=st.lists(_LEXEMES, max_size=18),
    wrapped=st.booleans(),
    chunk_size=st.integers(min_value=1, max_value=17),
)
def test_generated_lexemes_are_accepted_exactly_when_stdlib_finds_an_array(
    lexemes, wrapped, chunk_size
):
    text = "".join(lexemes)
    if wrapped:
        text = "[" + text + "]"
    try:
        expected = json.loads(text)
    except json.JSONDecodeError:
        expected = None
    if not isinstance(expected, list):
        with pytest.raises(json.JSONDecodeError):
            list(jsonx.iterload(_Reader(text), chunk_size=chunk_size))
    else:
        _same(list(jsonx.iterload(_Reader(text), chunk_size=chunk_size)), expected)

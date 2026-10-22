"""The complete stdlib JSON decoder contract, including its extensions.

The oracle is the running interpreter's ``json`` module. Cases cover the
documented behavior in https://docs.python.org/3.12/library/json.html and the
encoding/error/limit cases in CPython's ``Lib/test/test_json``. In particular,
these tests exercise accepted inputs outside orjson's domain, silent numeric
conversion, exception metadata, hooks, and file-like objects. Both optional
backend states must provide the same contract.
"""

from __future__ import annotations

import codecs
from decimal import Decimal
import inspect
import io
import json
import math
import sys
from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx


_BACKEND_PRESENT = jsonx._HAVE_MSGSPEC


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


@pytest.fixture(params=[False, True], ids=["base-install", "serde-extra"])
def backend_state(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codec availability must never change the public decoding contract."""
    if request.param and not _BACKEND_PRESENT:
        pytest.skip("msgspec (`serde` extra) not installed")
    monkeypatch.setattr(jsonx, "_HAVE_MSGSPEC", request.param)


pytestmark = pytest.mark.usefixtures("backend_state")


def _assert_same_value(actual: Any, expected: Any) -> None:
    """Compare types, order and floating-point bits, including NaN and -0.0."""
    assert type(actual) is type(expected)
    if isinstance(expected, float):
        if math.isnan(expected):
            assert math.isnan(actual)
        else:
            assert actual.hex() == expected.hex()
    elif isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key, value in expected.items():
            _assert_same_value(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _assert_same_value(actual_item, expected_item)
    else:
        assert actual == expected


def _assert_same_exception(
    actual_call: Callable[[], object],
    expected_call: Callable[[], object],
    error_type: type[Exception],
) -> None:
    """Exception class, args and JSON source locations match the stdlib."""
    with pytest.raises(error_type) as expected:
        expected_call()
    with pytest.raises(error_type) as actual:
        actual_call()
    assert type(actual.value) is type(expected.value)
    assert actual.value.args == expected.value.args
    assert str(actual.value) == str(expected.value)
    if isinstance(expected.value, json.JSONDecodeError):
        for attribute in ("msg", "doc", "pos", "lineno", "colno"):
            assert getattr(actual.value, attribute) == getattr(
                expected.value, attribute
            )


def test_public_decoder_signatures_match_the_running_stdlib() -> None:
    assert inspect.signature(jsonx.loads) == inspect.signature(json.loads)
    assert inspect.signature(jsonx.load) == inspect.signature(json.load)


@pytest.mark.parametrize(
    "document",
    [
        "null",
        "true",
        "false",
        "0",
        "-0",
        "-0.0",
        "-0e0",
        "NaN",
        "Infinity",
        "-Infinity",
        "1e400",
        "-1e400",
        "1e-4000",
        "-1e-4000",
        "4.9406564584124654e-324",
        "2.4703282292062327e-324",
        "1.7976931348623157e308",
        "1.00000000000000011102230246251565404236316680908203125",
        "1.00000000000000011102230246251565404236316680908203126",
        '""',
        '"é☃😀"',
        r'"\"\\\/\b\f\n\r\t"',
        r'"\ud800"',
        r'"\udfff"',
        r'"\ud800x\udfff"',
        r'"\ud83d\ude00"',
        f'"{chr(0xD800)}"',
        f'"{chr(0xDFFF)}"',
        f'"{chr(0xD83D)}{chr(0xDE00)}"',
        '"\ufeff"',
        '"\u2028\u2029\u0085"',
        '"\ufffe\uffff"',
        "[]",
        "{}",
        '{"x": 1, "y": 2, "x": 3}',
        '{"NaN": [NaN, Infinity, -Infinity, 1e400]}',
        ' \t\r\n { "a": [true, false, null, {"b": -0.0}] } \t\r\n',
    ],
)
def test_accepted_documents_preserve_values_and_types(document: str) -> None:
    _assert_same_value(jsonx.loads(document), json.loads(document))


@pytest.mark.parametrize(
    "value", [2**64, 2**64 + 1, -(2**63) - 1, 2**1000, -(10**1000)]
)
def test_integers_outside_the_backend_range_stay_exact(value: int) -> None:
    document = f'{{"id": {value}, "nested": [{value}]}}'
    decoded = jsonx.loads(document)
    _assert_same_value(decoded, json.loads(document))
    assert decoded["id"] == value
    assert type(decoded["id"]) is int


@given(
    document=st.from_regex(
        r"-?(?:0|[1-9][0-9]{0,100})(?:\.[0-9]{1,100})?(?:[eE][+-]?[0-9]{1,4})?",
        fullmatch=True,
    )
)
def test_number_grammar_matches_stdlib_precision(document: str) -> None:
    _assert_same_value(jsonx.loads(document), json.loads(document))


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-8", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
    ],
)
@pytest.mark.parametrize("with_bom", [False, True])
@pytest.mark.parametrize("container", [bytes, bytearray])
@pytest.mark.parametrize("document", ['["é☃😀"]', '"☃"', "7"])
def test_binary_encoding_detection_matches_stdlib(
    encoding: str,
    bom: bytes,
    with_bom: bool,
    container: type[bytes | bytearray],
    document: str,
) -> None:
    encoded = document.encode(encoding)
    data = container(bom + encoded if with_bom else encoded)
    _assert_same_value(jsonx.loads(data), json.loads(data))
    _assert_same_value(jsonx.load(io.BytesIO(data)), json.load(io.BytesIO(data)))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-32-be"])
def test_binary_lone_surrogates_use_surrogatepass(encoding: str) -> None:
    document = '"before\ud800after"'.encode(encoding, errors="surrogatepass")
    _assert_same_value(jsonx.loads(document), json.loads(document))


@pytest.mark.parametrize(
    "document",
    [
        b'"\xff"',
        b'"\x80"',
        b'"\xe2\x82"',
        codecs.BOM_UTF16_LE + b'"\x00\x00',
        codecs.BOM_UTF32_BE + b"\x00\x00\x00",
        codecs.BOM_UTF32_BE + b"\x00\x11\x00\x00",
    ],
)
def test_invalid_binary_encoding_raises_the_same_unicode_error(document: bytes) -> None:
    _assert_same_exception(
        lambda: jsonx.loads(document), lambda: json.loads(document), UnicodeDecodeError
    )


@pytest.mark.parametrize(
    "document",
    [
        "",
        " \t\r\n ",
        "[",
        "[1",
        "[1,",
        "[1,]",
        "[,1]",
        "{",
        '{"x"',
        '{"x":',
        '{"x": 1,}',
        "{x:1}",
        '"unterminated',
        r'"\x20"',
        r'"\u12"',
        r'"\uzzzz"',
        '"a\x00b"',
        "00",
        "-01",
        "+1",
        ".5",
        "1.",
        "1e",
        "1e+",
        "0x10",
        "1_0",
        "nan",
        "NAN",
        "infinity",
        "True",
        "undefined",
        "[] []",
        "{} trailing",
        "[true]junk",
        "\ufeff{}",
        " \ufeff{}",
        "\u00a0[]",
        "\v[]",
        "// comment\n[]",
        '{"é": [1,\n 2, }',
        '\n\n{"😀": "é", "x": }',
        "1\uff10",
        "0.\uff10",
        "0e\uff10",
    ],
)
@pytest.mark.parametrize("binary", [False, True])
def test_invalid_json_preserves_error_class_message_and_location(
    document: str, binary: bool
) -> None:
    data = document.encode() if binary else document
    # A binary UTF-8 BOM is accepted; the corresponding text BOM is rejected.
    if binary and document.startswith("\ufeff"):
        _assert_same_value(jsonx.loads(data), json.loads(data))
        return
    _assert_same_exception(
        lambda: jsonx.loads(data), lambda: json.loads(data), json.JSONDecodeError
    )


@pytest.mark.parametrize("value", [None, 1, 1.5, [], {}, memoryview(b"{}")])
def test_unsupported_input_types_raise_stdlib_type_error(value: Any) -> None:
    _assert_same_exception(
        lambda: jsonx.loads(value), lambda: json.loads(value), TypeError
    )


@pytest.mark.parametrize("control", range(32))
def test_strict_false_accepts_every_unescaped_control_character(control: int) -> None:
    document = f'"a{chr(control)}b"'
    _assert_same_exception(
        lambda: jsonx.loads(document),
        lambda: json.loads(document),
        json.JSONDecodeError,
    )
    assert jsonx.loads(document, strict=False) == json.loads(document, strict=False)


def test_hook_order_duplicate_pairs_and_arbitrary_return_values() -> None:
    document = '{"outer": {"x": 1, "x": 2}, "empty": {}}'
    sentinel = object()
    calls: list[dict[str, object]] = []

    def hook(value: dict[str, object]) -> object:
        calls.append(value)
        return sentinel

    assert jsonx.loads(document, object_hook=hook) is sentinel
    assert calls == [{"x": 2}, {}, {"outer": sentinel, "empty": sentinel}]

    pair_calls: list[list[tuple[str, object]]] = []

    def pairs_hook(pairs: list[tuple[str, object]]) -> list[tuple[str, object]]:
        pair_calls.append(pairs)
        return pairs

    calls.clear()
    decoded = jsonx.loads(document, object_hook=hook, object_pairs_hook=pairs_hook)
    assert decoded == [("outer", [("x", 1), ("x", 2)]), ("empty", [])]
    assert pair_calls == [
        [("x", 1), ("x", 2)],
        [],
        [("outer", [("x", 1), ("x", 2)]), ("empty", [])],
    ]
    assert calls == []


def test_number_hooks_receive_original_lexemes_and_exclude_other_literals() -> None:
    calls: list[tuple[str, str]] = []

    def hook(kind: str) -> Callable[[str], tuple[str, str]]:
        def record(value: str) -> tuple[str, str]:
            calls.append((kind, value))
            return kind, value

        return record

    document = "[-0, 18446744073709551617, 1.2300, 1E+02, NaN, Infinity, -Infinity, null, true, false]"
    decoded = jsonx.loads(
        document,
        parse_int=hook("int"),
        parse_float=hook("float"),
        parse_constant=hook("constant"),
    )
    assert calls == [
        ("int", "-0"),
        ("int", "18446744073709551617"),
        ("float", "1.2300"),
        ("float", "1E+02"),
        ("constant", "NaN"),
        ("constant", "Infinity"),
        ("constant", "-Infinity"),
    ]
    assert decoded == [*calls, None, True, False]
    assert jsonx.loads("1.2300", parse_float=Decimal) == Decimal("1.2300")


@pytest.mark.parametrize(
    ("document", "keyword"),
    [
        ("{}", "object_hook"),
        ("{}", "object_pairs_hook"),
        ("1", "parse_int"),
        ("1.5", "parse_float"),
        ("NaN", "parse_constant"),
    ],
)
def test_hook_exception_propagates_without_wrapping(
    document: str, keyword: str
) -> None:
    failure = RuntimeError("caller-owned failure")

    def hook(value: object) -> object:
        raise failure

    with pytest.raises(RuntimeError, match="caller-owned failure") as caught:
        jsonx.loads(document, **{keyword: hook})
    assert caught.value is failure


def test_custom_decoder_accepts_additional_keywords_and_arbitrary_results() -> None:
    sentinel = object()
    calls: list[object] = []

    class Decoder(json.JSONDecoder):
        def __init__(self, *, marker: object, **kwargs: Any) -> None:
            calls.append((marker, kwargs))

        def decode(self, document: str) -> object:
            calls.append(document)
            return sentinel

    assert jsonx.loads(b"{}", cls=Decoder, marker=sentinel, strict=False) is sentinel
    assert calls == [(sentinel, {"strict": False}), "{}"]
    calls.clear()
    assert jsonx.load(io.StringIO("{}"), cls=Decoder, marker=sentinel) is sentinel
    assert calls == [(sentinel, {}), "{}"]


@pytest.mark.parametrize("keyword", ["encoding", "unsupported_keyword"])
def test_unknown_keywords_raise_the_same_constructor_error(keyword: str) -> None:
    kwargs = {keyword: "utf-8"}
    _assert_same_exception(
        lambda: jsonx.loads("{}", **kwargs),
        lambda: json.loads("{}", **kwargs),
        TypeError,
    )


def test_integer_digit_limit_and_custom_parse_int_match_the_interpreter() -> None:
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        accepted, rejected = "1" * 640, "1" * 641
        _assert_same_value(jsonx.loads(accepted), json.loads(accepted))
        _assert_same_exception(
            lambda: jsonx.loads(rejected), lambda: json.loads(rejected), ValueError
        )
        assert jsonx.loads(rejected, parse_int=str) == rejected
        sys.set_int_max_str_digits(0)
        _assert_same_value(jsonx.loads(rejected), json.loads(rejected))
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("depth", [100, 1200, 5000])
def test_nesting_uses_the_interpreters_limit_without_a_codec_cap(depth: int) -> None:
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(previous, 3000))
        document = "[" * depth + "0" + "]" * depth
        # C-stack and Python recursion guards differ across interpreter versions;
        # the running stdlib decides which depths are supported.
        try:
            expected = json.loads(document)
        except RecursionError:
            _assert_same_exception(
                lambda: jsonx.loads(document),
                lambda: json.loads(document),
                RecursionError,
            )
            return
        # Avoid recursively comparing the decoded values in the test itself.
        decoded = jsonx.loads(document)
        for _ in range(depth):
            assert type(decoded) is type(expected) is list
            assert len(decoded) == len(expected) == 1
            decoded, expected = decoded[0], expected[0]
        assert decoded == expected == 0
    finally:
        sys.setrecursionlimit(previous)


@pytest.mark.parametrize("binary", [False, True])
def test_load_reads_once_without_closing_the_stream(binary: bool) -> None:
    document = '{"id": 18446744073709551617, "text": "\\ud800"}'

    class Reader:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def read(self, *args: object) -> str | bytes:
            self.calls.append(args)
            return document.encode("utf-16") if binary else document

    reader = Reader()
    _assert_same_value(jsonx.load(reader), json.loads(document))
    assert reader.calls == [()]
    stream = io.StringIO(document)
    _assert_same_value(jsonx.load(stream), json.loads(document))
    assert not stream.closed
    assert stream.tell() == len(document)


def test_load_propagates_read_errors_without_wrapping() -> None:
    failure = OSError("stream failure")

    class Reader:
        def read(self) -> str:
            raise failure

    with pytest.raises(OSError, match="stream failure") as caught:
        jsonx.load(Reader())
    assert caught.value is failure

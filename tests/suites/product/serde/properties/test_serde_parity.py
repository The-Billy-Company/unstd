"""Differential JSON contracts against the running interpreter's stdlib ``json``.

Encoding must produce identical strings for stdlib-supported values and options,
including non-finite floats, integers beyond 64 bits, surrogate code points,
non-string keys, and tuples. Decoding must preserve Python types, float signs,
object order, and stdlib error diagnostics for the same text or binary input.

The authority is ``json``, not an encode/decode round trip through ``jsonx``.
A round trip alone would miss matching encoder/decoder bugs, lossy large numbers,
``True == 1``, negative zero, and normalization of escaped surrogate pairs.
Bounds limit test cost; they are not restrictions on the public codec contract.
"""

from __future__ import annotations

import json
import math

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx


# Explicitly include surrogates: Hypothesis's default text alphabet omits them.
_CHARACTER = st.characters() | st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF)
_TEXT = st.text(_CHARACTER, max_size=40)
_KEY = st.text(_CHARACTER, max_size=20)
_INT = st.integers(min_value=-(2**512), max_value=2**512)
_FLOAT = st.floats(allow_nan=True, allow_infinity=True)
_SCALAR = st.none() | st.booleans() | _INT | _FLOAT | _TEXT
_DICT_KEY = st.none() | st.booleans() | _INT | _FLOAT | _KEY
_JSON = st.recursive(
    _SCALAR,
    lambda children: (
        st.lists(children, max_size=6)
        | st.lists(children, max_size=6).map(tuple)
        | st.dictionaries(_DICT_KEY, children, max_size=6)
    ),
    max_leaves=25,
)
_CONTAINER = st.lists(_JSON, max_size=5) | st.dictionaries(_DICT_KEY, _JSON, max_size=5)
_IDENTITY_JSON = st.recursive(
    st.none()
    | st.booleans()
    | _INT
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=6)
        | st.dictionaries(st.text(max_size=20), children, max_size=6)
    ),
    max_leaves=25,
)
_FORMAT = st.fixed_dictionaries(
    {
        "ensure_ascii": st.booleans(),
        "sort_keys": st.booleans(),
        "allow_nan": st.booleans(),
        "skipkeys": st.booleans(),
        "check_circular": st.booleans(),
        "indent": st.sampled_from([None, -2, 0, 1, 2, 4, "", "\t", "--"]),
        "separators": st.sampled_from([None, (",", ":"), (", ", ": "), (" | ", " = ")]),
    }
)
_ENCODING = st.sampled_from(
    [
        "utf-8",
        "utf-8-sig",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
    ]
)


def _assert_same_value(actual: object, expected: object) -> None:
    """Compare parsed values without hiding NaN, numeric type, order, or sign bugs."""
    assert type(actual) is type(expected)
    if isinstance(expected, float):
        assert isinstance(actual, float)
        if math.isnan(expected):
            assert math.isnan(actual)
        else:
            assert actual == expected
            assert math.copysign(1.0, actual) == math.copysign(1.0, expected)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _assert_same_value(left, right)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert list(actual) == list(expected)
        for key, value in expected.items():
            _assert_same_value(actual[key], value)
    else:
        assert actual == expected


def _assert_same_error(actual: BaseException, expected: BaseException) -> None:
    """Exception classes, messages, and JSON source locations follow stdlib."""
    assert type(actual) is type(expected)
    assert actual.args == expected.args
    if isinstance(expected, json.JSONDecodeError):
        assert isinstance(actual, json.JSONDecodeError)
        for field in ("msg", "doc", "pos", "lineno", "colno"):
            assert getattr(actual, field) == getattr(expected, field)


def _assert_decode_matches(document: str | bytes | bytearray) -> None:
    """Compare success values or the complete stdlib failure for one document."""
    try:
        expected = json.loads(document)
    except (ValueError, TypeError, RecursionError) as expected_error:
        with pytest.raises(type(expected_error)) as caught:
            jsonx.loads(document)
        _assert_same_error(caught.value, expected_error)
    else:
        _assert_same_value(jsonx.loads(document), expected)


@given(value=_JSON)
@example(value={})
@example(value=[])
@example(value={"k": [1, 2.5, "é", None, True]})
@example(value=[math.nan, math.inf, -math.inf, -0.0])
@example(value=[2**64, -(2**64), 2**200])
@example(value={"\ud800": "\udfff", "pair": "\ud800\udc00"})
@example(value={1e-5: (1e-5, 1e20, 5e-324)})
def test_jsonx_interoperates_with_stdlib_json_both_directions(value: object) -> None:
    """Default encoding matches exact stdlib text; decoding preserves parsed values."""
    stdlib_text = json.dumps(value)
    jsonx_text = jsonx.dumps(value)
    assert type(jsonx_text) is str
    assert jsonx_text == stdlib_text
    expected = json.loads(stdlib_text)
    _assert_same_value(json.loads(jsonx_text), expected)
    _assert_same_value(jsonx.loads(stdlib_text), expected)
    _assert_same_value(jsonx.loads(jsonx_text), expected)


@given(value=_IDENTITY_JSON)
@example(value={"é": [2**200, -0.0, 5e-324, 1e20]})
def test_strict_identity_matches_the_defined_json_format(value: object) -> None:
    expected = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    assert jsonx.canonical_strict(value) == expected


@given(value=_JSON, options=_FORMAT)
@example(value={2: "two", 10: "ten"}, options={"sort_keys": True})
@example(value={"2": "string", 2: "integer"}, options={"sort_keys": True})
@example(value=[math.nan], options={"allow_nan": False})
@example(value={("unsupported",): 0, "kept": 1}, options={"skipkeys": True})
@example(value={("unsupported",): 0}, options={})
@example(value={"é": [0, -0.0]}, options={"indent": 0, "ensure_ascii": False})
def test_jsonx_format_options_match_stdlib(
    value: object, options: dict[str, object]
) -> None:
    """Formatting and option-dependent failures follow stdlib, not backend rules."""
    try:
        expected = json.dumps(value, **options)
    except (ValueError, TypeError) as expected_error:
        with pytest.raises(type(expected_error)) as caught:
            jsonx.dumps(value, **options)
        _assert_same_error(caught.value, expected_error)
    else:
        assert jsonx.dumps(value, **options) == expected


@given(value=_JSON, sort_keys=st.booleans())
@example(value=[-0.0, 1e-5, 5e-324, math.nan, math.inf], sort_keys=False)
@example(value={"text": "\ud800", "large": 2**100}, sort_keys=False)
def test_compact_bytes_are_exact_stdlib_utf8(value: object, sort_keys: bool) -> None:
    """Byte transport also preserves spelling, values and UTF-8 encoding errors."""
    try:
        expected = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=sort_keys,
        ).encode()
    except (ValueError, TypeError) as expected_error:
        with pytest.raises(type(expected_error)) as caught:
            jsonx.dumpb(value, sort_keys=sort_keys)
        _assert_same_error(caught.value, expected_error)
    else:
        assert jsonx.dumpb(value, sort_keys=sort_keys) == expected


@given(value=_JSON, encoding=_ENCODING, mutable=st.booleans())
@example(value={"é": "\ud800"}, encoding="utf-16-le", mutable=False)
@example(value=[2**100, math.inf, -0.0], encoding="utf-8-sig", mutable=True)
def test_jsonx_decodes_stdlib_binary_documents(
    value: object, encoding: str, mutable: bool
) -> None:
    """All stdlib-supported byte encodings work, with or without a BOM."""
    # ASCII escaping keeps arbitrary surrogate strings representable in every
    # encoding; the decoder must reconstruct them as stdlib does.
    document = json.dumps(value).encode(encoding)
    _assert_decode_matches(bytearray(document) if mutable else document)


@given(container=_CONTAINER)
@example(container={})
@example(container=[])
@example(container={"é": [math.nan, 2**100, "\ud800"]})
def test_jsonx_rejects_truncated_documents_like_stdlib(container: object) -> None:
    """A missing closing bracket preserves the stdlib error and its location."""
    complete = json.dumps(container)
    _assert_same_value(jsonx.loads(complete), json.loads(complete))
    truncated = complete[:-1]
    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)
    _assert_decode_matches(truncated)


@given(document=_TEXT)
@example(document='"\\ud800"')
@example(document='"\\ud800\\udc00"')
@example(document="[NaN, Infinity, -Infinity, 1e400, -1e400]")
@example(document='{"x": 1, "x": 2, "other": -0.0}')
@example(document="\ufeff{}")
def test_jsonx_text_acceptance_and_errors_match_stdlib(document: str) -> None:
    """Arbitrary text has the same acceptance, values, and diagnostics as stdlib."""
    _assert_decode_matches(document)


@given(document=st.binary(max_size=80))
@example(document=b'"\xed\xa0\x80"')
@example(document=b"\xff\xfe{\x00}\x00")
@example(document=b"\xff")
@example(document=b"\x00\x00\xfe\xff\x00\x00\x00{\x00\x00\x00}")
def test_jsonx_binary_acceptance_and_errors_match_stdlib(document: bytes) -> None:
    """Invalid byte sequences retain Unicode errors instead of becoming JSON errors."""
    _assert_decode_matches(document)
    _assert_decode_matches(bytearray(document))

"""Nested malformed and schema-mismatched input for typed ``Codec`` decoding.

Schema failures carry msgspec's exact ``$``-path, and all refusals match a raw
``msgspec.json.Decoder`` for the same schema. ``decode_or`` maps decode and
Unicode errors to its default while recursion errors propagate. The same codec
must decode the valid control afterwards.
"""

from __future__ import annotations

import pytest

from tests.support.environment.backends import require_backend


msgspec = require_backend("msgspec")
from unstd.serde import structs  # noqa: E402


class Leaf(msgspec.Struct):
    x: int
    tags: list[str] = []  # noqa: RUF012 — msgspec supplies a fresh mutable field default.


class Node(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    leaves: list[Leaf]
    index: dict[str, Leaf] = {}  # noqa: RUF012 — msgspec supplies a fresh mutable field default.


VALID = b'{"name":"n","leaves":[{"x":1,"tags":["a"]},{"x":2}],"index":{"k":{"x":3}}}'
EXPECTED = Node("n", [Leaf(1, ["a"]), Leaf(2)], {"k": Leaf(3)})

SHAPE = [
    (
        b'{"name":"n","leaves":[{"x":1},{"x":"2"}]}',
        "Expected `int`, got `str` - at `$.leaves[1].x`",
    ),
    (
        b'{"name":"n","leaves":[{"x":1,"tags":[1]}]}',
        "Expected `str`, got `int` - at `$.leaves[0].tags[0]`",
    ),
    (
        b'{"name":"n","leaves":[],"index":{"k":{"x":null}}}',
        "Expected `int`, got `null` - at `$.index[...].x`",
    ),
    (
        b'{"name":"n","leaves":[{}]}',
        "Object missing required field `x` - at `$.leaves[0]`",
    ),
    (
        b'{"name":"n","leaves":{"x":1}}',
        "Expected `array`, got `object` - at `$.leaves`",
    ),
    (b'{"leaves":[]}', "Object missing required field `name`"),
    (b'{"name":"n","leaves":[],"extra":1}', "Object contains unknown field `extra`"),
    (
        b'{"name":"n","leaves":[],"index":[]}',
        "Expected `object`, got `array` - at `$.index`",
    ),
    (b"[]", "Expected `object`, got `array`"),
    (
        b'{"name":"n","leaves":[{"x":1.5}]}',
        "Expected `int`, got `float` - at `$.leaves[0].x`",
    ),
]

SYNTAX = [
    b"",
    b"\xff",
    b'{"name":"n"',
    b'{"name":"n","leaves":[{"x":1}]} trailing',
    b'{"name":"n","leaves":[{"x":01}]}',
    b'{"name":"n","leaves":[{"x":1,}]}',
    b"{'name':'n'}",
    b'{"name":"n","leaves":[{"x":NaN}]}',
]

# Byte text fails UTF-8 decoding; surrogate-containing str fails UTF-8 encoding.
NOT_UTF8 = [
    (b'{"name":"\xc3("}', UnicodeDecodeError),
    (b'{"name":"n\xff","leaves":[]}', UnicodeDecodeError),
    (b'{"\xed\xa0\x80":1}', UnicodeDecodeError),
    ('"\ud800"', UnicodeEncodeError),
]


@pytest.fixture
def codec() -> structs.Codec[Node]:
    return structs.Codec(Node)


def test_valid_nested_control(codec: structs.Codec[Node]) -> None:
    assert codec.decode(VALID) == EXPECTED
    assert codec.decode(VALID.decode()) == EXPECTED


@pytest.mark.parametrize(("raw", "message"), SHAPE)
def test_schema_mismatch_reports_exact_nested_path(
    codec: structs.Codec[Node], raw: bytes, message: str
) -> None:
    with pytest.raises(msgspec.ValidationError) as caught:
        codec.decode(raw)
    assert str(caught.value) == message
    with pytest.raises(msgspec.ValidationError) as raw_caught:
        msgspec.json.Decoder(Node).decode(raw)
    assert str(raw_caught.value) == message
    assert codec.decode_or(raw) is None
    assert codec.decode(VALID) == EXPECTED


@pytest.mark.parametrize("raw", SYNTAX)
def test_malformed_json_is_a_decode_error_not_validation(
    codec: structs.Codec[Node], raw: bytes
) -> None:
    with pytest.raises(msgspec.DecodeError) as caught:
        codec.decode(raw)
    assert type(caught.value) is msgspec.DecodeError
    with pytest.raises(msgspec.DecodeError) as raw_caught:
        msgspec.json.Decoder(Node).decode(raw)
    assert str(caught.value) == str(raw_caught.value)
    assert codec.decode_or(raw, EXPECTED) is EXPECTED
    assert codec.decode(VALID) == EXPECTED


def test_every_strict_prefix_of_valid_input_is_refused(
    codec: structs.Codec[Node],
) -> None:
    for end in range(len(VALID)):
        with pytest.raises(msgspec.DecodeError):
            codec.decode(VALID[:end])
        assert codec.decode_or(VALID[:end]) is None
    assert codec.decode(VALID) == EXPECTED


@pytest.mark.parametrize(("raw", "error"), NOT_UTF8)
def test_non_utf8_text_is_refused_and_a_cache_miss(
    codec: structs.Codec[Node], raw: bytes | str, error: type[UnicodeError]
) -> None:
    with pytest.raises(error) as raw_caught:
        msgspec.json.Decoder(Node).decode(raw)
    with pytest.raises(error) as caught:
        codec.decode(raw)
    assert type(raw_caught.value) is type(caught.value) is error
    assert caught.value.args == raw_caught.value.args
    assert codec.decode_or(raw, EXPECTED) is EXPECTED
    assert codec.decode(VALID) == EXPECTED


def test_deep_nesting_is_a_catchable_recursion_refusal() -> None:
    # Valid but hostile JSON: msgspec's bounded refusal, not a malformed payload.
    deep = structs.Codec(object)
    decoder = msgspec.json.Decoder()
    payload = b"[" * 100_000 + b"]" * 100_000
    with pytest.raises(RecursionError) as raw_caught:
        decoder.decode(payload)
    with pytest.raises(RecursionError) as caught:
        deep.decode(payload)
    with pytest.raises(RecursionError) as default_caught:
        deep.decode_or(payload)
    assert type(raw_caught.value) is type(caught.value) is RecursionError
    assert type(default_caught.value) is RecursionError
    assert str(caught.value) == str(default_caught.value) == str(raw_caught.value)
    assert decoder.decode(b"[[[]]]") == [[[]]]
    assert deep.decode(b"[[[]]]") == [[[]]]


def test_non_bytes_input_is_a_type_error_and_not_swallowed(
    codec: structs.Codec[Node],
) -> None:
    for bad in (None, 1, ["x"]):
        with pytest.raises(TypeError):
            codec.decode(bad)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            codec.decode_or(bad)  # type: ignore[arg-type]
    assert codec.decode(memoryview(VALID)) == EXPECTED  # type: ignore[arg-type]


def test_lax_decoding_still_refuses_unconvertible_nested_values() -> None:
    lax = structs.Codec(Node, strict=False)
    assert lax.decode(b'{"name":"n","leaves":[{"x":"2"}]}') == Node("n", [Leaf(2)])
    with pytest.raises(msgspec.ValidationError, match=r"at `\$\.leaves\[0\]\.x`$"):
        lax.decode(b'{"name":"n","leaves":[{"x":"two"}]}')

"""Decimal/UUID/float hooks and codec reuse after every hook failure.

Wire bytes are hand-written from the documented formats (RFC 9562 canonical and
32-digit hex UUIDs, Decimal as JSON string or number); raw msgspec with the same
options is the wrapper oracle. Each failing hook is followed by a successful
call on the same codec, proving one bad value never poisons the reused codec.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from tests.support.environment.backends import require_backend


msgspec = require_backend("msgspec")
from unstd.serde import structs  # noqa: E402


U = UUID("01234567-89ab-cdef-0123-456789abcdef")
CANONICAL = b'"01234567-89ab-cdef-0123-456789abcdef"'
HEX = b'"0123456789abcdef0123456789abcdef"'


class Money(msgspec.Struct):
    id: UUID
    amount: Decimal


class Point:
    def __init__(self, x: int) -> None:
        self.x = x

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Point) and other.x == self.x

    __hash__ = None  # type: ignore[assignment]


class Holder(msgspec.Struct):
    p: Point


# ── UUID ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("fmt", "wire"), [("canonical", CANONICAL), ("hex", HEX)])
def test_uuid_format_exact_bytes(fmt: str, wire: bytes) -> None:
    codec = structs.Codec(UUID, uuid_format=fmt)  # type: ignore[arg-type]
    assert codec.encode(U) == wire
    assert codec.encode(U) == msgspec.json.Encoder(uuid_format=fmt).encode(U)


@pytest.mark.parametrize(
    "wire",
    [CANONICAL, HEX, CANONICAL.upper(), HEX.upper()],
    ids=["canon", "hex", "CANON", "HEX"],
)
def test_uuid_decodes_from_either_spelling(wire: bytes) -> None:
    decoded = structs.Codec(UUID).decode(wire)
    assert type(decoded) is UUID
    assert decoded == U


@pytest.mark.parametrize(
    "wire",
    [
        b'"0123"',
        b'"01234567-89ab-cdef-0123-456789abcdeg"',
        b"1",
        b'"{01234567-89ab-cdef-0123-456789abcdef}"',
    ],
)
def test_invalid_uuid_is_a_validation_error(wire: bytes) -> None:
    codec = structs.Codec(UUID)
    with pytest.raises(msgspec.ValidationError):
        codec.decode(wire)
    assert codec.decode_or(wire, U) is U


# ── Decimal ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "string", "number"),
    [
        ("1.10", b'"1.10"', b"1.10"),
        ("-0.00", b'"-0.00"', b"-0.00"),
        ("1E+3", b'"1E+3"', b"1E+3"),
        (
            "12345678901234567890.123456789",
            b'"12345678901234567890.123456789"',
            b"12345678901234567890.123456789",
        ),
    ],
)
def test_decimal_formats_keep_exact_digits(
    amount: str, string: bytes, number: bytes
) -> None:
    value = Decimal(amount)
    assert structs.Codec(Decimal).encode(value) == string
    assert structs.Codec(Decimal, decimal_format="number").encode(value) == number
    for wire in (string, number):
        decoded = structs.Codec(Decimal).decode(wire)
        assert type(decoded) is Decimal
        assert decoded.as_tuple() == value.as_tuple()


def test_decimal_number_format_matches_raw_msgspec_for_non_finite() -> None:
    # Bare NaN/Infinity are not JSON; the thin wrapper inherits msgspec's spelling.
    for text in ("NaN", "Infinity", "-Infinity"):
        value = Decimal(text)
        raw = msgspec.json.Encoder(decimal_format="number").encode(value)
        assert structs.Codec(Decimal, decimal_format="number").encode(value) == raw
        assert structs.Codec(Decimal).encode(value) == f'"{text}"'.encode()


@pytest.mark.parametrize("wire", [b'"x"', b"true", b'"1.2.3"', b"[]"])
def test_invalid_decimal_is_a_validation_error_at_its_path(wire: bytes) -> None:
    codec = structs.Codec(Money)
    raw = b'{"id":' + CANONICAL + b',"amount":' + wire + b"}"
    with pytest.raises(msgspec.ValidationError, match=r"at `\$\.amount`$"):
        codec.decode(raw)
    good = b'{"id":' + HEX + b',"amount":"2.50"}'
    assert codec.decode(good) == Money(U, Decimal("2.50"))


# ── float hooks ──────────────────────────────────────────────────────────────


def test_float_hook_sees_exact_float_text_and_skips_integers() -> None:
    seen: list[str] = []

    def keep(text: str) -> Decimal:
        seen.append(text)
        return Decimal(text)

    codec = structs.Codec(object, float_hook=keep)
    assert codec.decode(b"[0.1, 2, -0.0, 1e400]") == [
        Decimal("0.1"),
        2,
        Decimal("-0.0"),
        Decimal("1e400"),
    ]
    assert seen == ["0.1", "-0.0", "1e400"]


def test_float_hook_failure_is_located_and_codec_stays_usable() -> None:
    def refuse(text: str) -> float:
        if text == "2.5":
            msg = "refused 2.5"
            raise ValueError(msg)
        return float(text)

    codec = structs.Codec(object, float_hook=refuse)
    with pytest.raises(msgspec.ValidationError, match=r"^refused 2\.5 - at `\$\[1\]`$"):
        codec.decode(b"[1.5, 2.5]")
    assert codec.decode_or(b"[2.5]", ["miss"]) == ["miss"]
    assert codec.decode(b"[1.5, 3.5]") == [1.5, 3.5]


def test_float_enc_hook_coerces_numpy_scalars_to_python_floats() -> None:
    np = require_backend("numpy")
    codec = structs.Codec(list, enc_hook=structs.float_enc_hook)
    # float32(0.1) widens exactly to 0.100000001490116119384765625.
    assert codec.encode([np.float32(0.1), np.float64(0.1), np.float32(1.5)]) == (
        b"[0.10000000149011612,0.1,1.5]"
    )
    value = structs.float_enc_hook(np.float32(0.1))
    assert type(value) is float
    assert value == 0.100000001490116119384765625


def test_float_enc_hook_error_names_the_type_and_codec_stays_usable() -> None:
    codec = structs.Codec(list, enc_hook=structs.float_enc_hook)
    with pytest.raises(TypeError, match=r"^unencodable value of type Point$"):
        codec.encode([Point(1)])
    assert codec.encode([1.5]) == b"[1.5]"


# ── enc_hook / dec_hook failures ─────────────────────────────────────────────


def _dec_point(kind: type, obj: object) -> object:
    assert kind is Point
    if obj == "boom":
        msg = "unexpected"
        raise RuntimeError(msg)
    if obj == "bad":
        msg = "not a point"
        raise ValueError(msg)
    return Point(int(obj))  # type: ignore[call-overload]


def test_dec_hook_runtime_error_propagates_through_decode_and_decode_or() -> None:
    codec = structs.Codec(Holder, dec_hook=_dec_point)
    for call in (codec.decode, codec.decode_or):
        with pytest.raises(RuntimeError, match=r"^unexpected$"):
            call(b'{"p":"boom"}')
    assert codec.decode(b'{"p":"7"}') == Holder(Point(7))


def test_dec_hook_value_error_becomes_located_validation_error() -> None:
    codec = structs.Codec(Holder, dec_hook=_dec_point)
    with pytest.raises(msgspec.ValidationError, match=r"^not a point - at `\$\.p`$"):
        codec.decode(b'{"p":"bad"}')
    sentinel = Holder(Point(0))
    assert codec.decode_or(b'{"p":"bad"}', sentinel) is sentinel
    assert codec.decode(b'{"p":"3"}') == Holder(Point(3))


def test_enc_hook_failure_propagates_and_codec_stays_usable() -> None:
    def enc(obj: object) -> object:
        if isinstance(obj, Point):
            if obj.x < 0:
                msg = "negative"
                raise OverflowError(msg)
            return obj.x
        raise NotImplementedError

    codec = structs.Codec(Holder, enc_hook=enc, dec_hook=_dec_point)
    with pytest.raises(OverflowError, match=r"^negative$"):
        codec.encode(Holder(Point(-1)))
    assert codec.encode(Holder(Point(4))) == b'{"p":4}'
    assert codec.encode(Holder(Point(4))) == msgspec.json.Encoder(enc_hook=enc).encode(
        Holder(Point(4))
    )


def test_missing_enc_hook_is_a_type_error_naming_the_type() -> None:
    with pytest.raises(
        TypeError, match="Encoding objects of type Point is unsupported"
    ):
        structs.Codec(Holder).encode(Holder(Point(1)))

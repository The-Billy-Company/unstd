"""Typed codec v1: selector modulo four chooses fixed reviewed JSON schemas.

Schemas are a strict bounded Row, coercing Row, tagged Put/Delete union, and a
list of at most eight Rows. Row bounds are index 0..2**31-1, label <=128 chars,
at most 16 weights, and at most 16 small tags; no recursive schemas exist.
The remaining bytes are passed unchanged to both Codec and raw msgspec codecs.
Selector bits also choose deterministic/sorted/default encoding. Valid generated
rows derive at most 128 payload bytes, exercising encode, decode and decode_or;
all raw rejection classes and diagnostics must equal msgspec's own. DecodeError
becomes a default; malformed UTF-8 can raise UnicodeDecodeError and must escape
decode_or unchanged in type and diagnostics. A reviewed
custom-type hook additionally proves decode_or preserves non-DecodeError caller
exceptions with exact identity. No arbitrary schema or hook code is evaluated.
"""

# Fuzz entry points run without pytest; inspect exact rejection objects directly.
# ruff: noqa: PT017

from __future__ import annotations

from typing import Annotated

import msgspec

from unstd.serde import structs

from .json import same


_Index = Annotated[int, msgspec.Meta(ge=0, le=2**31 - 1)]
_Label = Annotated[str, msgspec.Meta(max_length=128)]
_Weights = Annotated[list[float], msgspec.Meta(max_length=16)]
_Tags = Annotated[dict[_Label, _Index], msgspec.Meta(max_length=16)]


class Row(msgspec.Struct, forbid_unknown_fields=True):
    index: _Index
    label: _Label
    weights: _Weights = msgspec.field(default_factory=list)
    tags: _Tags = msgspec.field(default_factory=dict)


class Put(msgspec.Struct, tag="put", forbid_unknown_fields=True):
    row: Row


class Delete(msgspec.Struct, tag="delete", forbid_unknown_fields=True):
    index: _Index


_SPECS = (Row, Row, Put | Delete, Annotated[list[Row], msgspec.Meta(max_length=8)])
_ORDERS = (None, "deterministic", "sorted")
_CODECS = {
    (schema, order): structs.Codec(spec, strict=schema != 1, order=order)
    for schema, spec in enumerate(_SPECS)
    for order in _ORDERS
}
_DECODERS = [
    msgspec.json.Decoder(spec, strict=index != 1) for index, spec in enumerate(_SPECS)
]
_ENCODERS = {order: msgspec.json.Encoder(order=order) for order in _ORDERS}


class _Opaque:
    def __init__(self, value: object) -> None:
        self.value = value


class _HookError(RuntimeError):
    pass


_HOOK_FAILURE = _HookError("reviewed fuzz conversion hook")


def _hook(kind: type, value: object) -> _Opaque:
    assert kind is _Opaque
    if value == "raise":
        raise _HOOK_FAILURE
    return _Opaque(value)


_HOOK_CODEC = structs.Codec(_Opaque, dec_hook=_hook)


def _decode(
    codec: structs.Codec,
    decoder: msgspec.json.Decoder,
    encoder: msgspec.json.Encoder,
    raw: bytes,
) -> None:
    default = object()
    try:
        expected = decoder.decode(raw)
    except (msgspec.DecodeError, UnicodeDecodeError) as expected_error:
        try:
            codec.decode(raw)
        except (msgspec.DecodeError, UnicodeDecodeError) as actual_error:
            assert type(actual_error) is type(expected_error)
            assert actual_error.args == expected_error.args
        else:
            msg = "Codec accepted payload rejected by raw msgspec"
            raise AssertionError(msg)
        if isinstance(expected_error, msgspec.DecodeError):
            assert codec.decode_or(raw, default) is default
            assert codec.decode_or(raw) is None
        else:
            try:
                codec.decode_or(raw, default)
            except UnicodeDecodeError as actual_error:
                assert actual_error.args == expected_error.args
            else:
                msg = "Codec.decode_or swallowed a non-DecodeError UTF-8 failure"
                raise AssertionError(msg)
    else:
        actual = codec.decode(raw)
        assert type(actual) is type(expected)
        same(msgspec.to_builtins(actual), msgspec.to_builtins(expected))
        same(
            msgspec.to_builtins(codec.decode_or(raw, default)),
            msgspec.to_builtins(expected),
        )
        assert codec.encode(actual) == encoder.encode(expected)


def fuzz(data: bytes) -> None:
    """Check raw validation, codec option forwarding and cache-miss semantics."""
    selector, raw = (data[0], data[1:]) if data else (0, b"")
    schema, order = selector % 4, _ORDERS[(selector >> 2) % 3]
    codec, decoder, encoder = (
        _CODECS[schema, order],
        _DECODERS[schema],
        _ENCODERS[order],
    )
    _decode(codec, decoder, encoder, raw)
    number = int.from_bytes(raw[:4], "big") % (2**31)
    row = Row(
        number,
        raw[:64].hex(),
        [int(value) / 16 for value in raw[:16]],
        {"z": number, "a": number // 2},
    )
    generated = (row, row, Put(row) if selector & 16 else Delete(number), [row])[schema]
    expected = encoder.encode(generated)
    assert codec.encode(generated) == expected
    assert len(expected) <= 4096
    _decode(codec, decoder, encoder, expected)
    # DecodeError is the entire suppression contract. A caller hook failure must
    # escape unchanged rather than becoming a cache miss or a validation error.
    try:
        _HOOK_CODEC.decode_or(b'"raise"', object())
    except _HookError as error:
        assert error is _HOOK_FAILURE
    else:
        msg = "Codec.decode_or swallowed a conversion-hook failure"
        raise AssertionError(msg)

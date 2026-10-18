"""Base64 v1: selector byte modulo four, then payload; empty is mode zero.

0 compares raw bytes decode APIs, 1 encodes byte-derived buffers, 2 checks the
lenient unpadded URL helper, 3 decodes arbitrary UTF-8/surrogate text. Selector
bits vary strictness, buffer kind, padding, alternative alphabets and text error
policy. Raw inputs are never pre-sanitized for stdlib-compatible functions.
The URL oracle separately translates alphabets, filters ignored characters, then
pads the retained alphabet, as required by the convenience contract. Caller
bounds input to 65536 bytes, hence output is at most 87384 Base64 bytes or 65536
text characters. Only each operation's documented input errors are compared.
"""

from __future__ import annotations

import base64 as reference
import binascii
import io
import json as stdjson

from unstd.serde import b64

from .json import parity, text


_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)


def _url(raw: bytes | str | bytearray | memoryview) -> bytes:
    if isinstance(raw, str):
        if not raw.isascii():
            # Ask stdlib for its documented ASCII-input error and exact message.
            reference.b64decode(raw)
        raw = raw.encode("ascii")
    translated = bytes(raw).replace(b"-", b"+").replace(b"_", b"/")
    retained = bytes(char for char in translated if char in _ALPHABET)
    return reference.b64decode(retained + b"=" * (-len(retained) % 4))


def _buffer(raw: bytes, selector: int) -> bytes | bytearray | memoryview:
    return (raw, bytearray(raw), memoryview(raw))[selector % 3]


def _decode(source: bytes | str | bytearray | memoryview, selector: int) -> None:
    for strict in (False, True):
        parity(
            lambda strict=strict: b64.b64decode(source, validate=strict),
            lambda strict=strict: reference.b64decode(source, validate=strict),
        )
    altchars = (None, b"-_", b"!?", b"aa", b"\xff\xfe", "-_")[selector % 6]
    parity(
        lambda: b64.b64decode(source, altchars=altchars, validate=bool(selector & 4)),
        lambda: reference.b64decode(
            source, altchars=altchars, validate=bool(selector & 4)
        ),
    )
    parity(
        lambda: b64.standard_b64decode(source),
        lambda: reference.standard_b64decode(source),
    )
    parity(
        lambda: b64.urlsafe_b64decode(source),
        lambda: reference.urlsafe_b64decode(source),
    )
    parity(
        lambda: b64.decode(source), lambda: reference.b64decode(source, validate=True)
    )
    parity(lambda: b64.decode(source, url=True), lambda: _url(source))
    policy = ("strict", "replace", "ignore")[selector % 3]
    parity(
        lambda: b64.decode_text(source, url=bool(selector & 8), errors=policy),
        lambda: (
            _url(source) if selector & 8 else reference.b64decode(source, validate=True)
        ).decode("utf-8", policy),
    )
    parity(
        lambda: b64.decode_json(source),
        lambda: stdjson.loads(reference.b64decode(source, validate=True)),
    )
    if not isinstance(source, str):
        parity(lambda: b64.decodebytes(source), lambda: reference.decodebytes(source))
        got, want = io.BytesIO(), io.BytesIO()
        parity(
            lambda: b64.decode_file(io.BytesIO(bytes(source)), got),
            lambda: reference.decode(io.BytesIO(bytes(source)), want),
        )
        assert got.getvalue() == want.getvalue()


def fuzz(data: bytes) -> None:
    """Compare the full stdlib-like and convenience Base64 wire surfaces."""
    selector, raw = (data[0], data[1:]) if data else (0, b"")
    mode = selector % 4
    source = _buffer(raw, selector >> 2)
    if mode == 0:
        _decode(source, selector)
    elif mode == 1:
        pairs = (
            (b64.b64encode, reference.b64encode),
            (b64.standard_b64encode, reference.standard_b64encode),
            (b64.urlsafe_b64encode, reference.urlsafe_b64encode),
            (b64.encodebytes, reference.encodebytes),
        )
        for actual, oracle in pairs:
            parity(
                lambda actual=actual: actual(source),
                lambda oracle=oracle: oracle(source),
            )
        altchars = raw[: (selector >> 2) % 4]
        parity(
            lambda: b64.b64encode(source, altchars=altchars),
            lambda: reference.b64encode(source, altchars=altchars),
            (AssertionError, ValueError, binascii.Error),
        )
        for url in (False, True):
            for pad in (False, True):
                expected = (
                    reference.urlsafe_b64encode(source)
                    if url
                    else reference.b64encode(source)
                ).decode("ascii")
                if not pad:
                    expected = expected.rstrip("=")
                encoded = b64.encode(source, url=url, pad=pad)
                assert encoded == expected
                if url or pad:
                    assert b64.decode(encoded, url=url) == bytes(source)
        got, want = io.BytesIO(), io.BytesIO()
        b64.encode_file(io.BytesIO(raw), got)
        reference.encode(io.BytesIO(raw), want)
        assert got.getvalue() == want.getvalue()
        obj = {"raw": raw[:1024].hex(), "length": len(raw)}
        expected_json = stdjson.dumps(
            obj, ensure_ascii=False, separators=(",", ":")
        ).encode()
        encoded_json = b64.encode_json(obj)
        assert encoded_json == reference.b64encode(expected_json).decode("ascii")
        assert b64.decode_json(encoded_json) == obj
    elif mode == 2:
        parity(lambda: b64.decode(source, url=True), lambda: _url(source))
        # MIME garbage and alternate alphabets remain an independent raw path.
        _decode(source, selector)
    else:
        _decode(text(raw), selector)

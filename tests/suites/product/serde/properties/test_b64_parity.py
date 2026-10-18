"""Generated Base64 contracts against the running interpreter's stdlib codec.

The stdlib supplies the oracle, independently of the selected implementation.
Stdlib-named functions match their original entry points, including arbitrary
alternative alphabets with colliding characters. Convenience standard decoding
follows ``b64decode(validate=True)`` exactly. Convenience URL decoding retains
everything accepted by ``urlsafe_b64decode`` and additionally repairs missing
terminal padding after ignored bytes have been removed. Padding already inside
the input is preserved: stdlib may stop decoding at that point.

Both optional native and forced stdlib implementations must preserve result
types, bytes, exception classes, and exception arguments. Generation bounds
control test cost; they do not restrict the public codec contract.
"""

from __future__ import annotations

import base64
from binascii import Error
from contextlib import contextmanager
from typing import TYPE_CHECKING

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.serde import b64


if TYPE_CHECKING:
    from collections.abc import Iterator


try:
    import pybase64
except ImportError:
    pybase64 = None


_BACKENDS = pytest.mark.parametrize(
    "backend",
    [
        "stdlib",
        pytest.param(
            "pybase64",
            marks=pytest.mark.skipif(
                pybase64 is None, reason="pybase64 (`serde` extra) not installed"
            ),
        ),
    ],
)
_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/-_="
_IGNORED = bytes(value for value in range(256) if value not in _ALPHABET)
_BINARY = st.binary(max_size=96)
_PAYLOAD = st.binary(max_size=256)
_CHARACTER = st.characters() | st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF)
_TEXT = st.text(_CHARACTER, max_size=48)
_NOISE = st.lists(st.sampled_from(_IGNORED), max_size=8).map(bytes)
_ALTCHARS = st.binary(min_size=2, max_size=2)
_ENCODE_ALTCHARS = st.none() | _ALTCHARS
_DECODE_ALTCHARS = _ENCODE_ALTCHARS | st.text(
    st.characters(max_codepoint=127), min_size=2, max_size=2
)


@contextmanager
def _using_backend(name: str) -> Iterator[None]:
    """Select a backend per example without a function-scoped fixture."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(b64, "_b64", base64 if name == "stdlib" else pybase64)
        patch.setattr(b64, "_HAVE_PYBASE64", name == "pybase64")
        yield


def _url_decode_reference(document: str | bytes | bytearray | memoryview) -> bytes:
    """Extend the public stdlib decoder only when padding needs repair."""
    try:
        return base64.urlsafe_b64decode(document)
    except Error:
        # Reaching here proves stdlib accepted the input type and ASCII text.
        # Its own conversion also copies noncontiguous memoryviews to bytes.
        raw = (
            document.encode("ascii")
            if isinstance(document, str)
            else memoryview(document).tobytes()
        )
        meaningful = raw.translate(None, _IGNORED)
        return base64.urlsafe_b64decode(meaningful + b"=" * (-len(meaningful) % 4))


def _assert_decode_matches(
    document: str | bytes | bytearray | memoryview, *, url: bool
) -> None:
    """Compare successful bytes or the exact stdlib failure for this input."""
    try:
        expected = (
            _url_decode_reference(document)
            if url
            else base64.b64decode(document, validate=True)
        )
    except (ValueError, TypeError, BufferError) as expected_error:
        with pytest.raises(type(expected_error)) as caught:
            b64.decode(document, url=url)
        _assert_same_error(caught.value, expected_error)
    else:
        actual = b64.decode(document, url=url)
        assert type(actual) is bytes
        assert actual == expected


def _assert_same_error(actual: BaseException, expected: BaseException) -> None:
    """Exception subclasses and diagnostics follow the interpreter's stdlib."""
    assert type(actual) is type(expected)
    assert actual.args == expected.args


def _assert_public_decode_matches(
    document: str | bytes, *, altchars: str | bytes | None, validate: bool
) -> None:
    """Compare the complete modern stdlib signature without helper extensions."""
    try:
        expected = base64.b64decode(document, altchars=altchars, validate=validate)
    except (ValueError, TypeError, BufferError) as expected_error:
        with pytest.raises(type(expected_error)) as caught:
            b64.b64decode(document, altchars=altchars, validate=validate)
        _assert_same_error(caught.value, expected_error)
    else:
        actual = b64.b64decode(document, altchars=altchars, validate=validate)
        assert type(actual) is bytes
        assert actual == expected


@st.composite
def _mutated_encoding(draw: st.DrawFn) -> bytes:
    """Mix valid encodings with truncation, inserted noise, and broken padding."""
    payload = draw(st.binary(max_size=64))
    encoded = (
        base64.urlsafe_b64encode(payload)
        if draw(st.booleans())
        else base64.b64encode(payload)
    )
    if draw(st.booleans()):
        encoded = encoded.rstrip(b"=")
    position = draw(st.integers(min_value=0, max_value=len(encoded)))
    operation = draw(st.sampled_from(["insert", "delete", "replace", "padding"]))
    if operation == "delete":
        size = draw(st.integers(min_value=0, max_value=5))
        return encoded[:position] + encoded[position + size :]
    if operation == "padding":
        addition = b"=" * draw(st.integers(min_value=1, max_value=6))
    else:
        addition = draw(st.binary(min_size=1, max_size=5))
    end = position + 1 if operation == "replace" else position
    return encoded[:position] + addition + encoded[end:]


@_BACKENDS
@given(data=_PAYLOAD, url=st.booleans(), pad=st.booleans())
@example(data=bytes(range(256)), url=False, pad=True)
@example(data=b"\xfb\xff\xfe", url=True, pad=False)
@example(data=b"", url=True, pad=False)
def test_encode_matches_stdlib(backend: str, data: bytes, url: bool, pad: bool) -> None:
    expected = (
        base64.urlsafe_b64encode(data) if url else base64.b64encode(data)
    ).decode("ascii")
    if not pad:
        expected = expected.rstrip("=")
    with _using_backend(backend):
        actual = b64.encode(data, url=url, pad=pad)
    assert type(actual) is str
    assert actual == expected


@_BACKENDS
@given(document=_BINARY)
@example(document=b"AA==AAAA")
@example(document=b"AAAA=")
@example(document=b"A===")
@example(document=b"\x00\xffAA==\r\n")
def test_standard_decode_arbitrary_bytes_match_stdlib(
    backend: str, document: bytes
) -> None:
    with _using_backend(backend):
        _assert_decode_matches(document, url=False)


@_BACKENDS
@given(document=_mutated_encoding())
@example(document=b"AA==")
@example(document=b"AA=")
@example(document=b"AAA=A")
@example(document=b"=AAAA")
@example(document=b"AAAA==")
@example(document=b"Zh==")
@example(document=b"Zm9=")
def test_standard_decode_mutated_encodings_match_stdlib(
    backend: str, document: bytes
) -> None:
    with _using_backend(backend):
        _assert_decode_matches(document, url=False)


@_BACKENDS
@given(document=_TEXT, url=st.booleans())
@example(document="\ud800", url=False)
@example(document="\udfff", url=True)
@example(document="AA==é", url=False)
@example(document="AA==é", url=True)
@example(document="AA!", url=True)
def test_decode_text_inputs_preserve_ascii_validation(
    backend: str, document: str, url: bool
) -> None:
    with _using_backend(backend):
        _assert_decode_matches(document, url=url)


@_BACKENDS
@given(document=_BINARY)
@example(document=b"AA!")
@example(document=b"AA\xff")
@example(document=b"AAA=A")
@example(document=b"AA==AAAA")
@example(document=b"AAAA=")
@example(document=b"=AA=")
def test_url_decode_arbitrary_bytes_match_stdlib_with_optional_padding(
    backend: str, document: bytes
) -> None:
    with _using_backend(backend):
        _assert_decode_matches(document, url=True)


@_BACKENDS
@given(document=_mutated_encoding())
@example(document=b"A")
@example(document=b"AA")
@example(document=b"AAA")
@example(document=b"AA===AAAA")
@example(document=b"AAA=AAAA")
@example(document=b"AA!AA")
@example(document=b"-_/+")
def test_url_decode_mutated_encodings_preserve_stdlib_padding_rules(
    backend: str, document: bytes
) -> None:
    with _using_backend(backend):
        _assert_decode_matches(document, url=True)


@_BACKENDS
@given(
    payload=_PAYLOAD,
    noise=_NOISE,
    position=st.integers(min_value=0, max_value=344),
    standard=st.booleans(),
)
@example(payload=b"\x00", noise=b"!", position=2, standard=False)
@example(payload=b"\xfb\xff", noise=b"\x00\xff\r\n", position=1, standard=True)
def test_url_decode_preserves_accepted_stdlib_inputs(
    backend: str, payload: bytes, noise: bytes, position: int, standard: bool
) -> None:
    encoded = (
        base64.b64encode(payload) if standard else base64.urlsafe_b64encode(payload)
    )
    offset = position % (len(encoded) + 1)
    document = encoded[:offset] + noise + encoded[offset:]
    expected = base64.urlsafe_b64decode(document)
    with _using_backend(backend):
        actual = b64.decode(document, url=True)
    assert type(actual) is bytes
    assert actual == expected


@_BACKENDS
@given(document=_BINARY, url=st.booleans())
@example(document=b"AA==", url=False)
@example(document=b"AA!", url=True)
@example(document=b"AA==AAAA", url=True)
@example(document=b"AA\xff", url=True)
def test_decode_bytes_like_inputs_match_stdlib(
    backend: str, document: bytes, url: bool
) -> None:
    # Every other byte holds the input, making a noncontiguous buffer that
    # stdlib's decoder accepts by copying through memoryview(...).tobytes().
    interleaved = bytearray(2 * len(document))
    interleaved[::2] = document
    buffers = [bytearray(document), memoryview(document), memoryview(interleaved)[::2]]
    with _using_backend(backend):
        for buffer in buffers:
            _assert_decode_matches(buffer, url=url)


@_BACKENDS
@given(data=_PAYLOAD, altchars=_ENCODE_ALTCHARS)
@example(data=bytes(range(256)), altchars=None)
@example(data=b"\xfb\xff", altchars=b"\x00\xff")
@example(data=b"\xfb\xff", altchars=b"==")
@example(data=b"\xfb\xff", altchars=b"AA")
@example(data=b"\xfb\xff", altchars=b"/+")
def test_b64encode_arbitrary_alternative_alphabets_match_stdlib(
    backend: str, data: bytes, altchars: bytes | None
) -> None:
    expected = base64.b64encode(data, altchars=altchars)
    with _using_backend(backend):
        actual = b64.b64encode(data, altchars=altchars)
    assert type(actual) is bytes
    assert actual == expected


@_BACKENDS
@given(document=_BINARY | _TEXT, altchars=_DECODE_ALTCHARS, validate=st.booleans())
@example(document=b"AA==", altchars=None, validate=False)
@example(document=b"AA==", altchars=b"==", validate=True)
@example(document=b"A!A=", altchars="!=", validate=False)
@example(document=b"\x00\xff==", altchars=b"\x00\xff", validate=True)
@example(document="\ud800", altchars="-_", validate=False)
@example(document="AA==é", altchars=None, validate=True)
@example(document="-_/+", altchars="-_", validate=True)
def test_b64decode_arbitrary_inputs_and_alphabets_match_stdlib(
    backend: str,
    document: str | bytes,
    altchars: str | bytes | None,
    validate: bool,
) -> None:
    with _using_backend(backend):
        _assert_public_decode_matches(document, altchars=altchars, validate=validate)


@_BACKENDS
@given(
    document=_mutated_encoding(),
    altchars=_DECODE_ALTCHARS,
    validate=st.booleans(),
    text_input=st.booleans(),
)
@example(document=b"AA==AAAA", altchars=None, validate=False, text_input=False)
@example(document=b"AAAA=", altchars=None, validate=True, text_input=True)
@example(document=b"AAA=A", altchars=b"AA", validate=False, text_input=False)
@example(document=b"AA\xff==", altchars=b"\xff=", validate=True, text_input=True)
def test_b64decode_mutated_encodings_and_alphabets_match_stdlib(
    backend: str,
    document: bytes,
    altchars: str | bytes | None,
    validate: bool,
    text_input: bool,
) -> None:
    # Latin-1 also generates non-ASCII text from byte mutations, preserving
    # stdlib's rule that text is ASCII even when altchars contains high bytes.
    value = document.decode("latin-1") if text_input else document
    with _using_backend(backend):
        _assert_public_decode_matches(value, altchars=altchars, validate=validate)


@_BACKENDS
@given(
    data=_PAYLOAD,
    altchars=_ENCODE_ALTCHARS,
    validate=st.booleans(),
    text_input=st.booleans(),
    text_altchars=st.booleans(),
)
@example(
    data=b"\xfb\xff",
    altchars=b"==",
    validate=True,
    text_input=False,
    text_altchars=False,
)
@example(
    data=b"\x00\x00\xfb\xff",
    altchars=b"AA",
    validate=False,
    text_input=True,
    text_altchars=True,
)
@example(
    data=b"\xfb\xff",
    altchars=b"\x00\xff",
    validate=True,
    text_input=False,
    text_altchars=False,
)
def test_b64decode_stdlib_encoded_arbitrary_alphabets_match_stdlib(
    backend: str,
    data: bytes,
    altchars: bytes | None,
    validate: bool,
    text_input: bool,
    text_altchars: bool,
) -> None:
    encoded = base64.b64encode(data, altchars=altchars)
    document = encoded.decode("latin-1") if text_input else encoded
    alphabet = (
        altchars.decode("ascii")
        if text_altchars and altchars is not None and altchars.isascii()
        else altchars
    )
    # Arbitrary alphabets can collide with data characters or '='. The oracle
    # is stdlib's actual decoded value or error, not the original payload.
    with _using_backend(backend):
        _assert_public_decode_matches(document, altchars=alphabet, validate=validate)

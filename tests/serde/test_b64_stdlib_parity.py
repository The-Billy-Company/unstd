"""Base64 behavior follows the running stdlib, including malformed inputs.

RFC 4648 section 10 supplies independent successful vectors. The runtime
``base64`` module supplies the oracle for padding, coercion, alternate
alphabets and diagnostics, which differ across supported Python versions.
Sources: https://www.rfc-editor.org/rfc/rfc4648#section-10 and
https://docs.python.org/3/library/base64.html.
"""

from __future__ import annotations

from array import array
import base64
import binascii
import inspect
from io import BytesIO
from itertools import product
from typing import TYPE_CHECKING
import warnings

import pytest

from unstd.serde import b64


if TYPE_CHECKING:
    from collections.abc import Callable


_NATIVE_BACKEND = b64._b64 if b64._HAVE_PYBASE64 else None
_ENCODE_APIS = ["b64encode", "standard_b64encode", "urlsafe_b64encode"]
_DECODE_APIS = ["b64decode", "standard_b64decode", "urlsafe_b64decode"]
_VECTORS = [
    (b"", b""),
    (b"f", b"Zg=="),
    (b"fo", b"Zm8="),
    (b"foo", b"Zm9v"),
    (b"foob", b"Zm9vYg=="),
    (b"fooba", b"Zm9vYmE="),
    (b"foobar", b"Zm9vYmFy"),
]
_PADDING_CASES = [
    b"",
    b"=",
    b"==",
    b"====",
    b"A",
    b"AA",
    b"AAA",
    b"AAAA",
    b"A=",
    b"A===",
    b"AA=",
    b"AA==",
    b"AA===",
    b"AAA=",
    b"AAA==",
    b"AAAA=",
    b"AAAA==",
    b"=AAAA",
    b"A=AA",
    b"AA=A",
    b"AAA=A",
    b"AA==A",
    b"AA==A=",
    b"AA==AA",
    b"AA==AAA",
    b"AA==AAAA",
    b"AAA=AA",
    b"AAA=AAAA",
    b"AA=!=A",
    b"AA==!",
    b"AA!!",
    b"AA!",
    b"!AA",
    b"AA!A",
    b"A\x00AAA",
    b"AA\xff==",
    b"\r\nYQ==\t ",
    b"YQ==\x00Yg==",
    b"YQ==\nYg==",
    b"+/8=",
    b"-_8=",
    b"+_8=",
    b"-/8=",
]


@pytest.fixture(params=["native", "stdlib"], autouse=True)
def _backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "native":
        if _NATIVE_BACKEND is None:
            pytest.skip("pybase64 (`serde` extra) not installed")
        monkeypatch.setattr(b64, "_b64", _NATIVE_BACKEND)
        monkeypatch.setattr(b64, "_HAVE_PYBASE64", True)
    else:
        monkeypatch.setattr(b64, "_b64", base64)
        monkeypatch.setattr(b64, "_HAVE_PYBASE64", False)


def _same_call(
    actual_call: Callable[..., object],
    expected_call: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> None:
    error: Exception | None = None
    try:
        expected = expected_call(*args, **kwargs)
    except Exception as expected_error:
        error = expected_error
    if error is not None:
        with pytest.raises(type(error)) as actual_error:
            actual_call(*args, **kwargs)
        assert type(actual_error.value) is type(error)
        assert actual_error.value.args == error.args
    else:
        actual = actual_call(*args, **kwargs)
        assert type(actual) is type(expected)
        assert actual == expected


def test_public_base64_names_and_parameter_contracts() -> None:
    aliases = {"encode_file": "encode", "decode_file": "decode"}
    for name in [*_ENCODE_APIS, *_DECODE_APIS, "encodebytes", "decodebytes", *aliases]:
        assert name in b64.__all__
        expected = getattr(base64, aliases.get(name, name))
        actual_parameters = inspect.signature(getattr(b64, name)).parameters.values()
        expected_parameters = inspect.signature(expected).parameters.values()
        assert [(p.name, p.kind, p.default) for p in actual_parameters] == [
            (p.name, p.kind, p.default) for p in expected_parameters
        ]
    assert b64.Error is binascii.Error


@pytest.mark.parametrize(("raw", "encoded"), _VECTORS)
def test_rfc_4648_vectors(raw: bytes, encoded: bytes) -> None:
    assert b64.b64encode(raw) == encoded
    assert type(b64.b64encode(raw)) is bytes
    assert b64.b64decode(encoded) == raw
    assert b64.b64decode(encoded, validate=True) == raw
    assert b64.standard_b64encode(raw) == encoded
    assert b64.standard_b64decode(encoded) == raw
    assert b64.urlsafe_b64encode(raw) == encoded
    assert b64.urlsafe_b64decode(encoded) == raw
    assert b64.encode(raw) == encoded.decode("ascii")
    assert b64.decode(encoded) == raw


@pytest.mark.parametrize(
    "size",
    [
        0,
        1,
        2,
        3,
        15,
        16,
        17,
        31,
        32,
        33,
        47,
        48,
        49,
        63,
        64,
        65,
        127,
        128,
        129,
        1023,
        1024,
        1025,
    ],
)
def test_native_block_boundaries_and_all_byte_values(size: int) -> None:
    raw = (bytes(range(256)) * (size // 256 + 1))[:size]
    encoded = base64.b64encode(raw)
    assert b64.b64encode(raw) == encoded
    assert b64.b64decode(encoded, validate=True) == raw
    assert b64.b64decode(encoded.decode("ascii"), validate=True) == raw
    url_encoded = base64.urlsafe_b64encode(raw)
    assert b64.standard_b64encode(raw) == encoded
    assert b64.urlsafe_b64encode(raw) == url_encoded
    assert b64.urlsafe_b64decode(url_encoded) == raw
    assert b64.encode(raw, url=True) == url_encoded.decode("ascii")


@pytest.mark.parametrize("encoded", _PADDING_CASES)
@pytest.mark.parametrize("validate", [False, True])
@pytest.mark.parametrize("text", [False, True])
def test_padding_alphabet_and_diagnostics_follow_the_interpreter(
    encoded: bytes, validate: bool, text: bool
) -> None:
    value = encoded.decode("latin1") if text else encoded
    _same_call(b64.b64decode, base64.b64decode, value, validate=validate)
    if not validate:
        _same_call(b64.standard_b64decode, base64.standard_b64decode, value)
        _same_call(b64.urlsafe_b64decode, base64.urlsafe_b64decode, value)
    else:
        _same_call(b64.decode, lambda s: base64.b64decode(s, validate=True), value)


def test_exhaustive_short_padding_shapes() -> None:
    for size in range(8):
        for characters in product(b"A=!", repeat=size):
            encoded = bytes(characters)
            for validate in (False, True):
                _same_call(b64.b64decode, base64.b64decode, encoded, validate=validate)


@pytest.mark.parametrize("encoded", [b"Zh==", b"Zv==", b"Zm9=", b"Zm/="])
def test_validate_true_does_not_require_canonical_zero_padding_bits(
    encoded: bytes,
) -> None:
    # Strict alphabet/padding validation does not enforce canonical pad bits.
    expected = base64.b64decode(encoded, validate=True)
    assert b64.b64decode(encoded, validate=True) == expected
    assert b64.decode(encoded) == expected
    assert base64.b64encode(expected) != encoded


class _BytesOnly:
    def __bytes__(self) -> bytes:
        return b"YQ=="


class _BufferOwner:
    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)

    def __buffer__(self, flags: int) -> memoryview:
        return memoryview(self.data)


def _word_array(data: bytes) -> array[int]:
    words = array("H")
    words.frombytes(data)
    return words


_BUFFER_FACTORIES: list[Callable[[bytes], object]] = [
    bytes,
    bytearray,
    memoryview,
    lambda data: memoryview(bytearray(data)),
    lambda data: memoryview(b"".join(bytes([item, 0]) for item in data))[::2],
    lambda data: memoryview(data[::-1])[::-1],
    lambda data: memoryview(bytearray(data)).cast("B", (2, 2)),
    lambda data: memoryview(bytearray(data)).cast("H"),
    _word_array,
    _BufferOwner,
]


@pytest.mark.parametrize("factory", _BUFFER_FACTORIES)
@pytest.mark.parametrize("api_name", [*_ENCODE_APIS, *_DECODE_APIS])
def test_bytes_like_objects_keep_stdlib_layout_and_format_rules(
    factory: Callable[[bytes], object], api_name: str
) -> None:
    value = factory(b"YQ==")
    _same_call(getattr(b64, api_name), getattr(base64, api_name), value)
    if api_name == "b64decode":
        _same_call(b64.b64decode, base64.b64decode, value, validate=True)
    if api_name == "b64encode":
        _same_call(
            b64.encode, lambda data: base64.b64encode(data).decode("ascii"), value
        )
    if api_name == "b64decode":
        _same_call(
            b64.decode, lambda data: base64.b64decode(data, validate=True), value
        )


@pytest.mark.parametrize("api_name", [*_ENCODE_APIS, *_DECODE_APIS])
def test_released_memoryview_preserves_the_stdlib_failure(api_name: str) -> None:
    value = memoryview(b"YQ==")
    value.release()
    _same_call(getattr(b64, api_name), getattr(base64, api_name), value)


@pytest.mark.parametrize(
    "value", [None, True, 1, 1.5, [], (), {}, object(), _BytesOnly()]
)
@pytest.mark.parametrize("api_name", [*_ENCODE_APIS, *_DECODE_APIS])
def test_invalid_types_are_not_coerced(value: object, api_name: str) -> None:
    _same_call(getattr(b64, api_name), getattr(base64, api_name), value)
    if api_name == "b64decode":
        _same_call(b64.b64decode, base64.b64decode, value, validate=True)
        _same_call(
            b64.decode, lambda data: base64.b64decode(data, validate=True), value
        )
        _same_call(
            lambda data: b64.decode(data, url=True), base64.urlsafe_b64decode, value
        )


@pytest.mark.parametrize("value", ["YQ==", "", "é", "\ud800"])
@pytest.mark.parametrize("api_name", _ENCODE_APIS)
def test_encoders_require_binary_input(value: str, api_name: str) -> None:
    _same_call(getattr(b64, api_name), getattr(base64, api_name), value)


@pytest.mark.parametrize(
    "value",
    [
        "é",
        "\x80",
        "ÿ",
        "Ā",
        "😀",
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udfff", id="low-surrogate"),
        "YQ==é",
    ],
)
@pytest.mark.parametrize("validate", [False, True])
def test_non_ascii_text_and_surrogates_preserve_exact_value_error(
    value: str, validate: bool
) -> None:
    _same_call(b64.b64decode, base64.b64decode, value, validate=validate)
    _same_call(b64.decode, lambda data: base64.b64decode(data, validate=True), value)
    _same_call(b64.urlsafe_b64decode, base64.urlsafe_b64decode, value)
    _same_call(lambda data: b64.decode(data, url=True), base64.urlsafe_b64decode, value)


_ALTCHARS = [
    None,
    b"-_",
    b"+/",
    b"/+",
    b"AA",
    b"==",
    b"A=",
    b"\x00\xff",
    b"\x80\xfe",
    b"\n\t",
    bytearray(b"-_"),
    memoryview(b"-_"),
    memoryview(b"-x_x")[::2],
    memoryview(b"-_").cast("H"),
    b"",
    b"_",
    b"abc",
    "-_",
    "é_",
    "☃_",
    1,
    [],
    {},
]


@pytest.mark.parametrize("altchars", _ALTCHARS)
def test_alternate_encoder_alphabets_include_collisions_and_invalid_values(
    altchars: object,
) -> None:
    for raw in (b"", b"\xfb\xef\xff\x01"):
        _same_call(b64.b64encode, base64.b64encode, raw, altchars)


@pytest.mark.parametrize("altchars", _ALTCHARS)
@pytest.mark.parametrize("validate", [False, True])
def test_alternate_decoder_alphabets_preserve_translation_and_errors(
    altchars: object, validate: bool
) -> None:
    for encoded in (b"", b"++//AQ==", b"--__AQ==", b"AA==AAAA", b"\x80\xfe8="):
        _same_call(b64.b64decode, base64.b64decode, encoded, altchars, validate)


@pytest.mark.parametrize("validate", [None, False, True, 0, 1, "", "yes", [], [1]])
def test_validate_argument_keeps_stdlib_truth_conversion(validate: object) -> None:
    _same_call(b64.b64decode, base64.b64decode, b"YQ==!", validate=validate)


class _EncodedString(str):
    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        return b"Yg=="


class _TranslatedBytes(bytes):
    def translate(self, table: bytes | None, delete: bytes = b"") -> bytes:
        return b"Yw=="


class _TranslatedBytearray(bytearray):
    def translate(self, table: bytes | None, delete: bytes = b"") -> bytes:
        return b"ZA=="


class _LengthGuardBytes(_TranslatedBytes):
    def __len__(self) -> int:
        message = "subclass length must not replace buffer length"
        raise AssertionError(message)


@pytest.mark.parametrize(
    "value",
    [
        _EncodedString("YQ=="),
        _EncodedString("é"),
        _TranslatedBytes(b"YQ=="),
        _TranslatedBytearray(b"YQ=="),
        _LengthGuardBytes(b"YQ=="),
    ],
)
def test_subclass_conversion_and_translation_are_observable(value: object) -> None:
    _same_call(b64.b64decode, base64.b64decode, value, validate=True)
    _same_call(b64.b64decode, base64.b64decode, value, altchars=b"-_")
    _same_call(b64.urlsafe_b64decode, base64.urlsafe_b64decode, value)
    _same_call(b64.decode, lambda data: base64.b64decode(data, validate=True), value)
    _same_call(lambda data: b64.decode(data, url=True), base64.urlsafe_b64decode, value)


@pytest.mark.parametrize(
    "encoded", [b"+/8=", b"-_8=", b"+_8=", b"-/8=", b"AA==AAAA", b"AAA=A"]
)
def test_url_decoders_preserve_stdlib_padded_values_without_backend_warnings(
    encoded: bytes,
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _same_call(b64.urlsafe_b64decode, base64.urlsafe_b64decode, encoded)
        _same_call(
            lambda data: b64.decode(data, url=True), base64.urlsafe_b64decode, encoded
        )


@pytest.mark.parametrize(
    "encoded",
    [
        b"YQ",
        b"YQ!",
        b"!YQ",
        b"YQ!!",
        b"Y!Q",
        b"YQ=",
        b"YQ=!",
        b"YQ\xff",
        b"YWI",
        b"Y!WI",
    ],
)
def test_optional_url_padding_is_added_after_ignored_characters(encoded: bytes) -> None:
    # Independent examples pin the unpadded extension, which stdlib lacks.
    expected = b"ab" if b"W" in encoded else b"a"
    assert b64.decode(encoded, url=True) == expected
    if b"\xff" not in encoded:
        assert b64.decode(encoded.decode("ascii"), url=True) == expected


def test_url_helper_exhaustive_short_alphabets_and_padding() -> None:
    # The standard decoder owns interior padding and termination. Repair only
    # omitted terminal padding after removing the ignored byte in this corpus.
    def reference(encoded: bytes) -> bytes:
        meaningful = encoded.replace(b"!", b"")
        return base64.urlsafe_b64decode(meaningful + b"=" * (-len(meaningful) % 4))

    for prefix in (b"", b"AAAA" * 32):
        for size in range(7):
            for characters in product(b"A-_=!", repeat=size):
                _same_call(
                    lambda data: b64.decode(data, url=True),
                    reference,
                    prefix + bytes(characters),
                )


@pytest.mark.parametrize("size", [1023, 1024, 1025])
@pytest.mark.parametrize("alphabet", [b"-_", b"+/", b"+_", b"-/"])
def test_url_helper_large_mixed_alphabets_and_ignored_bytes(
    size: int, alphabet: bytes
) -> None:
    payload = (bytes(range(256)) * 5)[:size]
    encoded = base64.b64encode(payload, altchars=alphabet)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for document in [encoded, encoded.rstrip(b"="), b"!\n" + encoded + b"\xff"]:
            assert b64.decode(document, url=True) == payload


@pytest.mark.parametrize("size", [0, 1, 56, 57, 58, 114, 115, 512])
def test_mime_byte_apis_keep_76_column_wrapping_and_terminal_newlines(
    size: int,
) -> None:
    raw = (bytes(range(256)) * 3)[:size]
    encoded = base64.encodebytes(raw)
    assert b64.encodebytes(raw) == encoded
    assert b64.decodebytes(encoded) == raw
    if raw:
        assert encoded.endswith(b"\n")
        assert all(len(line) <= 76 for line in encoded.splitlines())


@pytest.mark.parametrize("factory", _BUFFER_FACTORIES)
@pytest.mark.parametrize("api_name", ["encodebytes", "decodebytes"])
def test_mime_apis_preserve_stricter_buffer_requirements(
    factory: Callable[[bytes], object], api_name: str
) -> None:
    _same_call(getattr(b64, api_name), getattr(base64, api_name), factory(b"YQ=="))


class _ShortReader(BytesIO):
    def read(self, size: int = -1) -> bytes:
        return super().read(min(size, 3) if size >= 0 else 3)


@pytest.mark.parametrize("size", [0, 1, 56, 57, 58, 512])
@pytest.mark.parametrize("short_reads", [False, True])
def test_legacy_binary_file_round_trips_and_stream_ownership(
    size: int, short_reads: bool
) -> None:
    raw = (bytes(range(256)) * 3)[:size]
    reader = _ShortReader if short_reads else BytesIO
    expected_input, actual_input = reader(raw), reader(raw)
    expected_output, actual_output = BytesIO(), BytesIO()
    assert base64.encode(expected_input, expected_output) is None
    assert b64.encode_file(actual_input, actual_output) is None
    assert actual_output.getvalue() == expected_output.getvalue()
    assert actual_input.tell() == expected_input.tell()
    assert not actual_input.closed
    assert not actual_output.closed
    decoded = BytesIO()
    actual_output.seek(0)
    assert b64.decode_file(actual_output, decoded) is None
    assert decoded.getvalue() == raw
    assert not actual_output.closed
    assert not decoded.closed


def test_legacy_file_decode_keeps_output_before_a_malformed_line() -> None:
    encoded = b"YQ==\nYg\n"
    expected_output, actual_output = BytesIO(), BytesIO()
    with pytest.raises(binascii.Error) as expected_error:
        base64.decode(BytesIO(encoded), expected_output)
    with pytest.raises(binascii.Error) as actual_error:
        b64.decode_file(BytesIO(encoded), actual_output)
    assert type(actual_error.value) is type(expected_error.value)
    assert actual_error.value.args == expected_error.value.args
    assert actual_output.getvalue() == expected_output.getvalue() == b"a"


@pytest.mark.parametrize("api_name", ["encode_file", "decode_file"])
def test_legacy_file_writer_failure_propagates_unchanged(api_name: str) -> None:
    failure = OSError("caller-owned write failure")

    class Writer:
        def write(self, data: bytes) -> None:
            raise failure

    with pytest.raises(OSError, match="caller-owned write failure") as caught:
        getattr(b64, api_name)(BytesIO(b"YWJj\n"), Writer())
    assert caught.value is failure


class _SelfTranslatingBytes(bytes):
    def __init__(self, value: bytes) -> None:
        self.calls: list[tuple[bytes | None, bytes]] = []

    def translate(self, table: bytes | None, delete: bytes = b"") -> bytes:
        self.calls.append((table, delete))
        return b"YWI=" if delete else self


class _EncodingProbe(str):
    def __init__(self, value: str) -> None:
        self.calls: list[tuple[str, str]] = []

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        self.calls.append((encoding, errors))
        return b"YQ=="


class _TextTranslatingBytes(bytes):
    def __init__(self, value: bytes) -> None:
        self.calls: list[tuple[bytes | None, bytes]] = []
        self.text = _EncodingProbe("Yg==")

    def translate(self, table: bytes | None, delete: bytes = b"") -> str:
        self.calls.append((table, delete))
        return self.text


def test_url_helper_calls_subclass_translation_once_before_filtering() -> None:
    expected, actual = _SelfTranslatingBytes(b"YQ=="), _SelfTranslatingBytes(b"YQ==")
    assert b64.decode(actual, url=True) == base64.urlsafe_b64decode(expected) == b"a"
    assert actual.calls == expected.calls
    assert len(actual.calls) == 1


def test_url_helper_normalizes_a_translation_returning_text_once() -> None:
    expected, actual = _TextTranslatingBytes(b"Yw=="), _TextTranslatingBytes(b"Yw==")
    assert b64.decode(actual, url=True) == base64.urlsafe_b64decode(expected) == b"a"
    assert actual.calls == expected.calls
    assert len(actual.calls) == 1
    assert actual.text.calls == expected.text.calls
    assert len(actual.text.calls) == 1

"""Fuzz harnesses reject corrupt implementations and replay arbitrary bytes.

These tests exercise selectors and bounds, independently pin the models' tricky
semantics, and deliberately break each public boundary. A harness that merely
calls a parser, swallows its failures, or compares a wrapper with itself cannot
pass the mutation canaries. Optional-only targets are tested when their declared
backend exists; required replay and campaigns enforce backend availability.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json as stdjson
import math
import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.tooling.fuzz.targets import base64 as base64_target
from tests.tooling.fuzz.targets import json as json_target
from tests.tooling.fuzz.targets import ndjson as ndjson_target


_NAMES = ("json", "base64", "ndjson", "structs", "tokens")


def _target(name):
    if name in {"structs", "tokens"}:
        pytest.importorskip("msgspec" if name == "structs" else "blake3")
    return importlib.import_module(f"tests.tooling.fuzz.targets.{name}")


@pytest.mark.parametrize("name", _NAMES)
def test_every_selector_byte_is_a_total_deterministic_entry_point(name):
    target = _target(name)
    source = b'{"index":1,"label":"hello","values":[true,null,-0.0]}'
    for selector in range(256):
        sample = bytes([selector]) + source
        assert target.fuzz(sample) is None
        assert target.fuzz(sample) is None


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("size", [0, 1, 2, 3, 7, 16, 127, 256, 1024, 65535])
def test_empty_binary_and_maximum_input_payloads_are_bounded(name, size):
    target = _target(name)
    raw = hashlib.shake_256(b"unstd reviewed harness binary matrix").digest(size)
    for selector in range(4):
        assert target.fuzz(bytes([selector]) + raw) is None
    assert target.fuzz(b"") is None


@pytest.mark.parametrize("name", _NAMES)
@given(data=st.binary(max_size=2048))
def test_arbitrary_bytes_do_not_invalidate_the_harness_envelope(name, data):
    assert _target(name).fuzz(data) is None


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"true",
        b"false",
        b"0",
        b"-0.0",
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"1e999",
        b'"\\ud800"',
        b'{"b":1,"a":2,"b":3}',
        b'"caf\xc3\xa9"',
        b"\xef\xbb\xbf[]",
        b"\xff\xfe\x7b\x00\x7d\x00",
        b"[",
        b"\xff",
        b"01",
        b"1" * 512,
        b"[" * 96 + b"0" + b"]" * 96,
    ],
)
def test_json_raw_and_encode_modes_preserve_extensions_encodings_and_errors(raw):
    for selector in (0, 1, 3, 7, 39):
        json_target.fuzz(bytes([selector]) + raw)


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-16", "utf-32", "utf-16-be", "utf-32-be"]
)
def test_json_lexical_bounds_respect_strings_escapes_and_wire_encoding(encoding):
    shallow = stdjson.dumps({"text": '[]{}\\"' * 200}).encode(encoding)
    deep = ('["}]",' * 97 + "0" + "]" * 97).encode(encoding)
    assert json_target.bounded(shallow)
    assert not json_target.bounded(deep)
    assert not json_target.bounded(("1" * 513).encode(encoding))
    for selector in (0, 3):
        json_target.fuzz(bytes([selector]) + deep)


def test_json_value_comparison_detects_nan_type_signed_zero_and_key_order():
    json_target.same([float("nan"), -0.0], [float("nan"), -0.0])
    with pytest.raises(AssertionError):
        json_target.same(True, 1)
    with pytest.raises(AssertionError):
        json_target.same(0.0, -0.0)
    with pytest.raises(AssertionError):
        json_target.same({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert math.isnan(json_target.value(b"x")["values"][5])


@pytest.mark.parametrize(
    ("attribute", "sample", "replacement"),
    [
        ("loads", b"\x000", lambda *args, **kw: False),
        ("dumps", b"\x06hello", lambda *args, **kw: "corrupt"),
        ("dumpb", b"\x06hello", lambda *args, **kw: b"corrupt"),
        ("canonical", b"\x06hello", lambda *args, **kw: b"corrupt"),
    ],
)
def test_json_oracle_detects_corrupt_decoder_and_each_encoder(
    monkeypatch, attribute, sample, replacement
):
    monkeypatch.setattr(json_target.jsonx, attribute, replacement)
    with pytest.raises(AssertionError):
        json_target.fuzz(sample)


@pytest.mark.parametrize("name", ["json", "base64", "ndjson"])
def test_unexpected_parser_exceptions_escape_instead_of_becoming_rejections(
    monkeypatch, name
):
    target = _target(name)
    sentinel = RuntimeError("unexpected implementation failure")

    def broken(*args, **kwargs):
        raise sentinel

    if name == "json":
        monkeypatch.setattr(target.jsonx, "loads", broken)
    elif name == "base64":
        monkeypatch.setattr(target.b64, "b64decode", broken)
    else:
        monkeypatch.setattr(target.ndjson, "_parse", broken)
    with pytest.raises(RuntimeError) as caught:
        target.fuzz(b"\x000")
    assert caught.value is sentinel


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"Zg==",
        b"Zg",
        b"Zh",
        b"Zg=!",
        b"_-8",
        b"+/8=",
        b"+=A",
        b"Z ! g",
        b"====",
        b"a",
        b"\xff",
        b"\x00",
        b"A" * 128 + b"!\n",
    ],
)
def test_base64_raw_strict_lenient_url_mime_and_text_paths(raw):
    for selector in (0, 2, 3, 10, 18, 26, 34, 42, 50):
        base64_target.fuzz(bytes([selector]) + raw)
    for selector in (1, 5, 9, 13, 17, 21, 25):
        base64_target.fuzz(bytes([selector]) + raw)


def test_base64_url_oracle_pads_retained_alphabet_after_ignoring_junk():
    assert base64_target._url(b"Z ! g") == b"f"
    assert base64_target._url(b"_-8") == base64.urlsafe_b64decode(b"_-8=")
    with pytest.raises(ValueError, match="ASCII"):
        base64_target._url("\ud800")


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Python 3.15 unpadded mode")
def test_base64_url_oracle_keeps_runtime_interior_padding_semantics():
    assert base64_target._url(b"AA=A") == b"\0\0"
    assert base64_target._url(b"-=A") == b"\xf8"


@pytest.mark.parametrize(
    ("attribute", "sample", "replacement"),
    [
        ("b64decode", b"\x00Zg==", lambda *args, **kw: b"wrong"),
        ("encode", b"\x01payload", lambda *args, **kw: "wrong"),
        ("decode", b"\x02Zg", lambda *args, **kw: b"wrong"),
    ],
)
def test_base64_oracles_detect_corrupt_compatible_and_convenience_apis(
    monkeypatch, attribute, sample, replacement
):
    monkeypatch.setattr(base64_target.b64, attribute, replacement)
    with pytest.raises(AssertionError):
        base64_target.fuzz(sample)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\n\n\r\n",
        b"0\n{bad}\n2\n",
        b"\xff\n0",
        b"[\n0\n",
        b"1\n2",
        b'"a\\nb"\n',
        '"a\u0085b\u2028c\u2029d"\n'.encode(),
        "\u00a01\u00a0\n{bad}\n\t2\r\n".encode(),
        b"\v1\f\n\t2\r\n",
    ],
)
def test_ndjson_physical_rows_error_order_skip_policy_and_hooks(raw):
    for selector in (0, 1, 2):
        ndjson_target.fuzz(bytes([selector]) + raw)


@pytest.mark.parametrize(
    "sample", [b"\x000\n{bad}\n2", b"\x01\xff\n" + b"[" * 2000 + b"0" + b"]" * 2000]
)
def test_ndjson_domain_checks_each_physical_line_independently(sample):
    ndjson_target.fuzz(sample)


def test_ndjson_model_exposes_physical_line_numbers_and_stops_strictly():
    raw = [b"\n", b"1", b"{bad}", b"2", b"\xff"]
    values, failures = ndjson_target._model(raw, False)
    assert values == [1]
    assert [number for number, _ in failures] == [3]
    values, failures = ndjson_target._model(raw, True)
    assert values == [1, 2]
    assert [number for number, _ in failures] == [3, 5]


@pytest.mark.parametrize(
    ("attribute", "sample", "replacement"),
    [
        ("_parse", b"\x000\n1", lambda *args, **kw: 42),
        ("dumpb", b"\x02payload", lambda *args, **kw: b"wrong"),
        ("loads", b"\x000\n{bad}\n1", lambda *args, **kw: []),
    ],
)
def test_ndjson_oracle_detects_corrupt_rows_encoding_and_error_policy(
    monkeypatch, attribute, sample, replacement
):
    monkeypatch.setattr(ndjson_target.ndjson, attribute, replacement)
    with pytest.raises(AssertionError):
        ndjson_target.fuzz(sample)


@pytest.mark.parametrize(
    ("selector", "raw"),
    [
        (0, b'{"index":1,"label":"x","weights":[0.0,-0.0],"tags":{"a":2}}'),
        (0, b'{"index":1,"label":"x","unknown":null}'),
        (0, b'{"index":-1,"label":"x"}'),
        (0, b'{"index":1,"label":null}'),
        (1, b'{"index":"1","label":"x","weights":["1.5"]}'),
        (2, b'{"type":"put","row":{"index":1,"label":"x"}}'),
        (2, b'{"type":"delete","index":1}'),
        (2, b'{"type":"unknown"}'),
        (3, b'[{"index":1,"label":"x"}]'),
    ],
)
def test_structs_fixed_schemas_validation_strictness_and_tagged_unions(selector, raw):
    _target("structs").fuzz(bytes([selector]) + raw)


@pytest.mark.parametrize("attribute", ["decode", "encode", "decode_or"])
def test_structs_oracle_detects_corrupt_validation_encoding_and_default(
    monkeypatch, attribute
):
    target = _target("structs")
    codec = target._CODECS[0, None]
    if attribute == "decode":
        monkeypatch.setattr(codec, "decode", lambda raw: target.Row(99, "wrong"))
    elif attribute == "encode":
        monkeypatch.setattr(codec, "encode", lambda raw: b"wrong")
    else:
        monkeypatch.setattr(target.structs.Codec, "decode_or", lambda *args, **kw: None)
    with pytest.raises(AssertionError):
        target.fuzz(b'\x00{"index":1,"label":"right"}')


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"opaque\x00\xffclaims",
        b"\xed\xa0\x80",
        b"\xff",
        b'{"exp":0,"aud":"other"}',
        b"Zg..bad",
        b"a" * 512,
    ],
)
def test_tokens_arbitrary_text_authentic_tampering_and_signed_bad_envelopes(raw):
    target = _target("tokens")
    for selector in (0, 1, 2):
        target.fuzz(bytes([selector]) + raw)


def test_token_model_requires_canonical_payload_and_signature_pad_bits():
    target = _target("tokens")
    assert target._model(target._sign("Zg")) == b"f"
    for payload in ("Zh", "Zg=", "Zg!", "/w", ""):
        assert target._model(target._sign(payload)) is None
    payload, signature = target._sign("Zg").split(".")
    final = target._ALPHABET.index(signature[-1])
    alias = signature[:-1] + target._ALPHABET[final | 1]
    assert target._model(payload + "." + alias) is None


@pytest.mark.parametrize(
    ("attribute", "replacement", "sample"),
    [
        ("verify", lambda *args, **kw: b"forged", b"\x00attacker"),
        ("mint", lambda *args, **kw: "wrong", b"\x01claims"),
        ("verify_mac", lambda *args, **kw: True, b"\x01claims"),
        ("verify_mac", lambda *args, **kw: False, b"\x01claims"),
    ],
)
def test_token_independent_crypto_model_detects_acceptance_mint_and_mac_bugs(
    monkeypatch, attribute, replacement, sample
):
    target = _target("tokens")
    monkeypatch.setattr(target.token, attribute, replacement)
    with pytest.raises(AssertionError):
        target.fuzz(sample)


def test_token_parser_exceptions_escape_the_fuzz_boundary(monkeypatch):
    target = _target("tokens")
    sentinel = ValueError("attacker input must never produce this")

    def broken(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(target.token, "verify", broken)
    with pytest.raises(ValueError, match="attacker input") as caught:
        target.fuzz(b"\x00hostile")
    assert caught.value is sentinel


def test_structs_backend_utf8_errors_preserve_decode_parity_and_decode_or_default():
    target = _target("structs")
    sample = b'\x00{"i\x8byndex":0,"label":"x"}'
    target.fuzz(sample)
    codec = target._CODECS[0, None]
    with pytest.raises(UnicodeDecodeError) as raw_caught:
        target._DECODERS[0].decode(sample[1:])
    with pytest.raises(UnicodeDecodeError) as caught:
        codec.decode(sample[1:])
    assert type(caught.value) is type(raw_caught.value) is UnicodeDecodeError
    assert caught.value.args == raw_caught.value.args
    default = object()
    assert codec.decode_or(sample[1:], default) is default
    assert codec.decode_or(sample[1:]) is None
    control = b'{"index":0,"label":"x"}'
    assert codec.decode(control) == target.Row(0, "x")
    assert codec.decode_or(control, default) == target.Row(0, "x")
    target.fuzz(b"\x00" + control)


def test_structs_oracle_detects_decode_or_losing_backend_utf8_error_default(
    monkeypatch,
):
    target = _target("structs")
    original = target.structs.Codec.decode_or

    def lose_utf8_default(self, raw, default=None):
        try:
            self.decode(raw)
        except UnicodeError:
            return None
        return original(self, raw, default)

    monkeypatch.setattr(target.structs.Codec, "decode_or", lose_utf8_default)
    with pytest.raises(AssertionError):
        target.fuzz(b'\x00{"i\x8byndex":0,"label":"x"}')

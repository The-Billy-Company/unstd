"""Parser fuzz harnesses enforce independent laws and reject broken results.

These gates execute every envelope branch, byte order, numeric kind and regex
grammar deterministically. Injected wrong production results must fail the law;
malformed inputs, declared expansion attacks and unsupported backend capability
controls prove rejection and bounded admission are intentional.
"""

from __future__ import annotations

import gzip as stdlib_gzip
import importlib
import io
import struct
import wave

import pytest

from tests.support.backends import require_backend
from tests.tooling.fuzz.targets import gzip, pack, rex, toml, wav


def _yaml():
    require_backend("ruamel.yaml")
    return importlib.import_module("tests.tooling.fuzz.targets.yaml")


@pytest.mark.parametrize("mode", range(3))
@pytest.mark.parametrize("selector", range(44))
def test_pack_all_numeric_widths_endian_and_alias_paths(mode, selector):
    pack.fuzz(bytes((mode, selector)) + bytes(range(256)))
    pack.fuzz(bytes((mode, selector)))


def test_pack_encoding_oracle_rejects_wrong_native_result(monkeypatch):
    pack.fuzz(b"\0\x02\x00\x80\xff\x7f")
    monkeypatch.setattr(pack.binpack, "pack_array", lambda *_: b"")
    with pytest.raises(AssertionError):
        pack.fuzz(b"\0\x02\x00\x80\xff\x7f")


def test_pack_decoding_oracle_rejects_wrong_native_result(monkeypatch):
    monkeypatch.setattr(pack.binpack, "unpack_array", lambda *_: [])
    with pytest.raises(AssertionError):
        pack.fuzz(b"\x01\x02\x00\x80")


def test_pack_partial_elements_cannot_be_silently_ignored(monkeypatch):
    original = pack.binpack.unpack_array
    monkeypatch.setattr(
        pack.binpack,
        "unpack_array",
        lambda dtype, raw: original(dtype, raw[: len(raw) // 2 * 2]),
    )
    with pytest.raises(AssertionError, match="partial trailing"):
        pack.fuzz(b"\x01\x02\x00\x80")


@pytest.mark.parametrize("selector", range(20))
@pytest.mark.parametrize("payload", [b"", b"\x00\x80\xff\x7f" * 5, bytes(range(256))])
def test_wav_valid_channels_rates_streams_and_quantization(selector, payload):
    wav.fuzz(bytes((0, selector)) + payload)


@pytest.mark.parametrize("selector", range(12))
def test_wav_generated_invalid_headers_have_success_controls(selector):
    wav.fuzz(bytes((2, selector)) + b"\x00\x80\xff\x7f" * 4)
    wav.fuzz(bytes((0, selector)) + b"\x00\x80\xff\x7f" * 4)


def _pcm16(raw, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, 2, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(raw)
    return output.getvalue()


@pytest.mark.parametrize(
    "raw", [b"", b"RIFF", b"RIFF\x00\x00\x00\x00WAVE", _pcm16(b"\x00\x80\xff\x7f")]
)
def test_wav_raw_valid_and_malformed_dispatch(raw):
    wav.fuzz(b"\x01\0" + raw)


def test_wav_known_sample_model_rejects_wrong_decoder_result(monkeypatch):
    wav.fuzz(b"\0\0\x00\x80\xff\x7f")
    monkeypatch.setattr(wav.wavx, "read", lambda *_args, **_kwargs: ([0, 0], 8000))
    with pytest.raises(AssertionError):
        wav.fuzz(b"\0\0\x00\x80\xff\x7f")


@pytest.mark.parametrize(
    "kind", ["riff-size", "channels", "data-size", "foreign-format"]
)
def test_wav_hostile_allocation_headers_are_refused_before_native_read(
    monkeypatch, kind
):
    raw = bytearray(_pcm16(b"\0\0"))
    if kind == "riff-size":
        raw[4:8] = b"\xff" * 4
    elif kind == "channels":
        raw[22:24] = b"\xff" * 2
    elif kind == "data-size":
        raw[40:44] = struct.pack("<I", 2**30)
    else:
        raw[:4] = b"fLaC"

    def forbidden(*_args, **_kwargs):
        pytest.fail("hostile allocation header reached the decoder")

    monkeypatch.setattr(wav.wavx, "read", forbidden)
    wav.fuzz(b"\x01\0" + raw)


def test_wav_fmt_extent_cannot_seek_beyond_its_parent_riff_chunk(monkeypatch):
    reproducer = bytes.fromhex(
        "0100524946463000000057415645666d74201025000001000100401f0000"
        "803e000002001000646174610c0000000080ffff000001000040ff7f"
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("inconsistent fmt extent reached native decoding")

    monkeypatch.setattr(wav.wavx, "read", forbidden)
    wav.fuzz(reproducer)


@pytest.mark.parametrize("source", [b"\x01\0RIFF", b"\x02\0\0\0"])
def test_wav_unexpected_decoder_runtime_errors_propagate(monkeypatch, source):
    def broken_decoder(*_args, **_kwargs):
        msg = "unexpected decoder implementation fault"
        raise RuntimeError(msg)

    monkeypatch.setattr(wav.wavx, "read", broken_decoder)
    with pytest.raises(RuntimeError, match="unexpected decoder implementation fault"):
        wav.fuzz(source)


def test_wav_inconsistent_pcm_byte_rate_is_outside_shared_valid_parity(monkeypatch):
    reproducer = bytes.fromhex(
        "0100524946462400000057415645666d74201000000001000200803e00ff"
        "00fa0000040010006461746100000000"
    )

    def reject_malformed(*_args, **_kwargs):
        msg = "malformed PCM format metadata"
        raise ValueError(msg)

    monkeypatch.setattr(wav.wavx, "read", reject_malformed)
    wav.fuzz(reproducer)
    # The same decoder refusal must still fail for a well-formed PCM16 control.
    with pytest.raises(ValueError, match="malformed PCM format metadata"):
        wav.fuzz(b"\x01\0" + _pcm16(b"\0\0\xff\x7f", channels=2))


def test_wav_declared_partial_sample_is_outside_shared_valid_parity(monkeypatch):
    raw = bytearray(_pcm16(b"\0\x80\xff\x7f"))
    raw[40:44] = struct.pack("<I", 3)

    def reject_partial(*_args, **_kwargs):
        msg = "PCM data is not a whole number of frames"
        raise ValueError(msg)

    monkeypatch.setattr(wav.wavx, "read", reject_partial)
    wav.fuzz(b"\x01\0" + raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"a = 1",
        b"x = nan\ny = [inf, -inf]",
        b"a = [",
        b"a = 1\na = 2",
        b"\xff\x00",
        b"a = 1979-05-27T07:32:00Z",
    ],
)
def test_toml_raw_values_and_exact_decode_errors(raw):
    toml.fuzz(b"\0" + raw)


@pytest.mark.parametrize("raw", [b"", b'\0\xff\n"\\', bytes(range(256))])
def test_toml_known_models_and_style_preserving_writer(raw):
    toml.fuzz(b"\x01" + raw)


def test_toml_writer_roundtrip_rejects_corrupt_serialization(monkeypatch):
    require_backend("tomlkit")
    toml.fuzz(b"\x01payload")
    monkeypatch.setattr(toml.toml, "dumps", lambda *_: "wrong = true\n")
    with pytest.raises(AssertionError):
        toml.fuzz(b"\x01payload")


def test_toml_explicit_missing_writer_capability_keeps_read_laws(monkeypatch):
    monkeypatch.setattr(toml.toml, "_HAVE_TOMLKIT", False)

    def forbidden(*_args):
        pytest.fail("writer was invoked without its capability")

    monkeypatch.setattr(toml.toml, "dumps", forbidden)
    toml.fuzz(b"\x01payload")


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize(
    "payload", [b"", b'\0\xff\n"\\', bytes(range(256)), b"\x17\x06leaf"]
)
def test_yaml_every_safe_policy_branch(mode, payload):
    _yaml().fuzz(bytes((mode,)) + payload)


@pytest.mark.parametrize(
    "raw",
    [
        b"a: 1\n",
        b"a: .nan\n",
        b"a: 1\na: 2\n",
        b"&root [*root]\n",
        b"!!python/object/apply:builtins.eval [bad]\n",
        b"[",
        b"\xff\x00",
        b"a: &a [leaf]\nb: [*a, *a]\n",
    ],
)
def test_yaml_raw_safe_values_errors_and_recursive_aliases(raw):
    _yaml().fuzz(b"\0" + raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"user: {<<: {role: reader}, role: reader, role: admin}\n",
        b"user: {<<: {role: reader, role: admin}}\n",
        b"user: {<<: {role: reader}, role: reader, role: admin, date: 2001-32-15}\n",
        b"user: {<<: {role: reader}, role: admin}\n",
        b"user: {<<: {role: reader}, date: 2001-32-15}\n",
        b"&d {a:,*d,a:}",
    ],
    ids=[
        "duplicate-target",
        "duplicate-source",
        "duplicate-before-invalid-date",
        "valid-override",
        "invalid-date-parity",
        "unhashable-key-before-duplicate",
    ],
)
def test_yaml_raw_merge_policy_distinguishes_duplicates_from_overrides(raw):
    _yaml().fuzz(b"\0" + raw)


def test_yaml_raw_merge_policy_cannot_accept_a_duplicate_key(monkeypatch):
    target = _yaml()
    monkeypatch.setattr(target.yaml, "loads", lambda *_: {"user": {"role": "admin"}})
    monkeypatch.setattr(target.yaml, "load", lambda *_: {"user": {"role": "admin"}})
    with pytest.raises(AssertionError):
        target.fuzz(b"\0user: {<<: {role: reader}, role: reader, role: admin}\n")


def test_yaml_competing_errors_cannot_hide_acceptance_or_unrelated_refusal(monkeypatch):
    target = _yaml()
    raw = b"\0&d {a:,*d,a:}"
    target.fuzz(raw)
    monkeypatch.setattr(target.yaml, "loads", lambda *_: {})
    with pytest.raises(AssertionError):
        target.fuzz(raw)

    def unrelated_error(_source):
        msg = "unrelated scalar refusal"
        raise ValueError(msg)

    monkeypatch.setattr(target.yaml, "loads", unrelated_error)
    with pytest.raises(AssertionError):
        target.fuzz(raw)


@pytest.mark.parametrize("raw", [b"date: 2001-32-15\n", b"date: 2001-02-30\n"])
def test_yaml_invalid_dates_preserve_scalar_value_error_parity(raw):
    _yaml().fuzz(b"\0" + raw)


@pytest.mark.parametrize("raw", [b"date: 2001-32-15\n", b"ok: true\n"])
def test_yaml_scalar_value_errors_cannot_hide_a_parser_parity_failure(monkeypatch, raw):
    target = _yaml()

    def broken_parser(_source):
        msg = "unexpected scalar rejection"
        raise ValueError(msg)

    monkeypatch.setattr(target.yaml, "loads", broken_parser)
    with pytest.raises(AssertionError):
        target.fuzz(b"\0" + raw)


def test_yaml_known_model_rejects_wrong_production_value(monkeypatch):
    target = _yaml()
    target.fuzz(b"\x01payload")
    monkeypatch.setattr(target.yaml, "loads", lambda *_: {})
    with pytest.raises(AssertionError):
        target.fuzz(b"\x01payload")


def test_yaml_unsafe_tag_acceptance_cannot_pass_as_an_expected_parser_error(
    monkeypatch,
):
    target = _yaml()
    monkeypatch.setattr(target.yaml, "loads", lambda *_: "accepted")
    with pytest.raises(AssertionError, match="constructor tag"):
        target.fuzz(b"\x02payload")


def test_yaml_alias_identity_law_detects_graph_expansion(monkeypatch):
    target = _yaml()
    original = target.yaml.loads

    def expanded(source):
        return {key: list(value) for key, value in original(source).items()}

    monkeypatch.setattr(target.yaml, "loads", expanded)
    with pytest.raises(AssertionError):
        target.fuzz(b"\x03\x03\x03leaf")


@pytest.mark.parametrize("selector", range(256))
def test_rex_every_shared_grammar_flag_transport_and_limit(selector):
    rex.fuzz(bytes((0, selector)) + b"aab\n123\xff\xc3\xa9")


@pytest.mark.parametrize("selector", range(16))
def test_rex_ascii_subjects_exercise_native_eligible_character_classes(selector):
    rex.fuzz(bytes((0, selector)) + b"aab ab 123\tXYZ")


@pytest.mark.parametrize("selector", range(16))
@pytest.mark.parametrize("limit", range(4))
@pytest.mark.parametrize("template", range(3))
def test_rex_replacement_templates_and_limits_vary_independently(
    selector, limit, template
):
    rex.fuzz(bytes((0, selector, limit, template)) + b"aab ab 123")


@pytest.mark.parametrize(
    "pattern",
    [b"", b"[", b"(?P<x>a)(?P<x>b)", b"\\1", b"a$", b"[[:alpha:]]", b"(a+)+$", b"\xff"],
)
def test_rex_raw_compile_includes_invalid_and_deliberately_divergent_grammar(pattern):
    rex.fuzz(b"\x01\0" + pattern + b"\0subject")


def test_rex_match_projection_oracle_rejects_wrong_result(monkeypatch):
    rex.fuzz(b"\0\0abc")
    monkeypatch.setattr(rex.rex, "search", lambda *_: None)
    with pytest.raises(AssertionError):
        rex.fuzz(b"\0\0abc")


def test_rex_literal_entropy_preserves_arbitrary_octets_in_bytes_transport():
    rex.fuzz(b"\0\xfd\x7e\xfc\x06\x16\x83")


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize(
    "payload", [b"", b"\0", b"\x01", bytes(range(256)), b"compressible" * 128]
)
def test_gzip_each_member_corruption_and_stream_branch(mode, payload):
    gzip.fuzz(bytes((mode,)) + payload)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not gzip",
        stdlib_gzip.compress(b"hello", mtime=0),
        stdlib_gzip.compress(b"hello", mtime=0)[:-1],
    ],
)
def test_gzip_raw_valid_and_exact_error_family_controls(raw):
    gzip.fuzz(b"\0" + raw)


def test_gzip_independent_decode_oracle_rejects_wrong_native_result(monkeypatch):
    gzip.fuzz(b"\x01hello")
    monkeypatch.setattr(gzip.gzipx, "decompress", lambda *_: b"wrong")
    with pytest.raises(AssertionError):
        gzip.fuzz(b"\x01hello")


@pytest.mark.parametrize("forged_size", [False, True])
def test_gzip_expansion_guard_rejects_bombs_before_unbounded_decode(
    monkeypatch, *, forged_size
):
    raw = stdlib_gzip.compress(b"a" * (65536 * 8), mtime=0)
    if forged_size:
        raw = raw[:-4] + b"\0" * 4

    def forbidden(*_args):
        pytest.fail("over-budget stream reached unbounded decompression")

    monkeypatch.setattr(gzip.gzipx, "decompress", forbidden)
    gzip.fuzz(b"\0" + raw)

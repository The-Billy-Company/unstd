"""Bounded native buffer stress and explicit repeated-call retention checks."""

from __future__ import annotations

import gc
import json
import math
import struct
import subprocess
import sys
import textwrap
import tracemalloc

import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
orjson = pytest.importorskip("orjson")
pytestmark = pytest.mark.skipif(
    not hasattr(native, "ascii_text"), reason="native JSON text conversion unavailable"
)


def _large_text(kind):
    if kind == "controls":
        return "".join(map(chr, range(128))) * 4096
    if kind == "bmp":
        return "\x7f\x80\xff\u0100\u2028\u2029\uffff" * 32768
    if kind == "astral":
        return "\U00010000\U0001f600\U0010ffff" * 32768
    return ("\\" * 31 + '",:1e-5' + "é😀") * 8192


@pytest.mark.parametrize("kind", ["controls", "bmp", "astral", "quoted"])
@pytest.mark.parametrize("indent", [None, 2])
def test_large_unicode_buffers_preserve_every_escape_and_quote(kind, indent):
    text = _large_text(kind)
    value = {'é😀,:\\"': [text, text[::-1]], "tail": True}
    expected = json.dumps(value, indent=indent)
    raw = json.dumps(value, ensure_ascii=False, indent=indent).encode()
    assert native.ascii_text(raw) == expected
    assert jsonx.dumps(value, indent=indent) == expected


@pytest.mark.parametrize("slashes", range(64))
def test_quote_runs_do_not_turn_string_digits_into_numeric_tokens(slashes):
    text = "\\" * slashes + '",:1e-5' + "\\" * (63 - slashes)
    value = {text: [text, 0.84551240822557006, -0.0]}
    expected = json.dumps(value, indent=2)
    assert jsonx.dumps(value, indent=2) == expected
    raw = orjson.dumps(value, option=orjson.OPT_INDENT_2)
    assert native.ascii_text(raw) == raw.decode()


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("position", range(120, 137))
def test_float_prefix_near_128_character_boundary_preserves_exact_value(
    binary, position
):
    padding = "x" * (position - len('{"padding":"","value":'))
    document = (
        '{"padding":"'
        + padding
        + '","value":0.84551240822557006,"tail":"'
        + "é😀" * 3000
        + '"}'
    )
    assert document.index("0.845") == position
    source = document.encode() if binary else document
    assert json.dumps(jsonx.loads(source)) == json.dumps(json.loads(source))


@pytest.mark.parametrize("raw", [b"\xc0\x80", b"\xf0\x9f\x98", b"\xf4\x90\x80\x80"])
def test_truncated_and_out_of_range_utf8_keep_exact_decode_errors(raw):
    for operation in (native.ascii_text, jsonx.loads):
        with pytest.raises(UnicodeDecodeError) as expected:
            json.loads(raw)
        with pytest.raises(type(expected.value)) as actual:
            operation(raw)
        assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize("digits", [63, 64, 65])
def test_integers_around_numeric_scratch_size_use_exact_stdlib_fallback(digits):
    # Accepted float tokens are shorter than the scratch buffer. Larger integer
    # spellings must fall back before the normalizer sees backend output.
    value = {"integer": 10 ** (digits - 1), "float": 0.84551240822557006}
    assert jsonx.dumps(value, indent=2) == json.dumps(value, indent=2)


def _finite_float_corpus(size):
    values = [0.0, -0.0, 1e-5, 1e-6, 1e16, 1e20, 5e-324, sys.float_info.max]
    index = 0
    while len(values) < size:
        bits = (index * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        value = struct.unpack("!d", bits.to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
        index += 1
    return values


def test_real_finite_float_tokens_fit_numeric_scratch_buffer():
    values = _finite_float_corpus(4096)
    assert max(map(len, map(orjson.dumps, values))) < 64
    value = {"values": values}
    assert jsonx.dumps(value, indent=2) == json.dumps(value, indent=2)


def _retention(operation):
    # Warm imports, interpreter caches and allocator bins before measurement.
    for _ in range(100):
        operation()
    gc.collect()
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        initial, _ = tracemalloc.get_traced_memory()
        samples = []
        for _ in range(12):
            for _iteration in range(25):
                operation()
            gc.collect()
            current, _peak = tracemalloc.get_traced_memory()
            samples.append(current)
        final, peak = tracemalloc.get_traced_memory()
        # Small tracing/list bookkeeping is included. Retaining even one of
        # these output documents per batch would exceed the plateau bound.
        assert final - initial < 4 * 1024, (initial, final, samples)
        assert max(samples[-4:]) - min(samples[:4]) < 4 * 1024, samples
        return {"initial": initial, "final": final, "peak": peak, "samples": samples}
    finally:
        if not already_tracing:
            tracemalloc.stop()


@pytest.mark.resources
def test_ascii_conversion_allocations_plateau_after_warmup(record_testsuite_property):
    raw = orjson.dumps({"é😀": "é😀" * 4096})
    evidence = _retention(lambda: native.ascii_text(raw))
    record_testsuite_property("ascii_retention", json.dumps(evidence))


@pytest.mark.resources
@pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="finite float normalization is needed on 3.12"
)
def test_float_normalization_allocations_plateau_after_warmup(
    record_testsuite_property,
):
    value = {"values": _finite_float_corpus(512)}
    expected = json.dumps(value, indent=2).encode()
    assert native.encode_indent(value, orjson.OPT_INDENT_2) == expected
    evidence = _retention(lambda: native.encode_indent(value, orjson.OPT_INDENT_2))
    record_testsuite_property("float_retention", json.dumps(evidence))


@pytest.mark.resources
def test_declined_wide_proof_does_not_retain_partial_outputs(record_testsuite_property):
    value = [None] * 262144
    assert native.encode_wide(value, 0) is NotImplemented
    evidence = _retention(lambda: native.encode_wide(value, 0))
    record_testsuite_property("declined_wide_retention", json.dumps(evidence))


@pytest.mark.resources
@pytest.mark.parametrize("operation", ["identity_snapshot", "encode_text"])
def test_owned_snapshot_and_float_writer_allocations_plateau(
    operation, record_testsuite_property
):
    if not hasattr(native, operation):
        pytest.skip("native owned snapshot/finite-float writer unavailable")
    # A fresh interpreter proves actual fast-path acceptance even when earlier
    # profiler tests leave conservative monitoring metadata on older CPython.
    script = textwrap.dedent(f"""
        import json
        from unstd.serde import jsonx
        from unstd.serde import _json_native as native
        from tests.suites.product.serde.parity.test_jsonx_native_memory import _retention
        jsonx._identity_snapshot(None, set())
        value = [{{'id': i, 'text': 'café😀', 'score': i / 7.0}} for i in range(128)]
        operation = {operation!r}
        if operation == 'identity_snapshot':
            def call():
                result = native.identity_snapshot(value)
                assert result == value and result is not value
                assert result[0] is not value[0]
        else:
            expected = json.dumps(value)
            def call():
                assert native.encode_text(value, True, True) == expected
        print(json.dumps(_retention(call)))
    """)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and bounded audit source
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    record_testsuite_property(operation + "_retention", completed.stdout.strip())

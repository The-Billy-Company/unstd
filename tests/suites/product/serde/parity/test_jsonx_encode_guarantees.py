"""Wider native proofs retain callback, error, stack, and output boundaries."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import textwrap

import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
orjson = pytest.importorskip("orjson")
_WIDE_SIZE = 65_540


def _native_call(kind, value):
    if kind == "indent":
        return native.encode_indent(value, orjson.OPT_INDENT_2)
    return native.encode_wide(value, 0, True)


@pytest.mark.parametrize("kind", ["wide", "indent"])
def test_wide_profile_mutation_precedes_proof_and_follows_complete_result(kind):
    value = [None] * _WIDE_SIZE
    function = native.encode_indent if kind == "indent" else native.encode_wide
    expected_value = [*value, 7]
    expected = json.dumps(
        expected_value, **({"indent": 2} if kind == "indent" else {})
    ).encode()
    events = []
    sentinel = object()

    def profile(frame, event, argument):
        if argument is function:
            events.append(event)
            if event == "c_call":
                value.append(7)
            elif event == "c_return":
                value.append(sentinel)
        if argument is orjson.dumps:
            pytest.fail("a profile callback ran inside proof and serialization")

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        actual = _native_call(kind, value)
    finally:
        sys.setprofile(previous)
    assert actual == expected
    assert events == ["c_call", "c_return"]
    assert value[-1] is sentinel
    assert _native_call(kind, value) is NotImplemented


@pytest.mark.parametrize("kind", ["wide", "indent"])
def test_wide_trace_mutation_is_checked_before_serialization(kind):
    value = [None] * _WIDE_SIZE
    sentinel = object()

    def invoke():
        return _native_call(kind, value)

    def trace(frame, event, argument):
        if frame.f_code is invoke.__code__ and event == "line":
            value[-1] = sentinel
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        assert invoke() is NotImplemented
    finally:
        sys.settrace(previous)


@pytest.mark.parametrize("kind", ["wide", "indent"])
def test_wide_proofs_do_not_invoke_subclass_or_flag_callbacks(kind):
    class Integer(int):
        def __int__(self):
            pytest.fail("native proof converted an integer subclass")

        def __repr__(self):
            pytest.fail("native proof represented an integer subclass")

    class Flag:
        def __bool__(self):
            pytest.fail("native proof evaluated a custom flag")

        def __index__(self):
            pytest.fail("native proof converted an option")

    value = [None] * _WIDE_SIZE + [Integer(7)]
    assert _native_call(kind, value) is NotImplemented
    if kind == "indent":
        assert native.encode_indent([], Integer(1)) is NotImplemented
        assert native.encode_indent([], Flag()) is NotImplemented
        assert native.encode_indent([], True) is NotImplemented
    else:
        assert native.encode_wide([], Integer(0), False) is NotImplemented
        assert native.encode_wide([], Flag(), False) is NotImplemented
        assert native.encode_wide([], True, False) is NotImplemented
        assert native.encode_wide([], 0, Flag()) is NotImplemented
        assert native.encode_wide([], 0, 1) is NotImplemented


@pytest.mark.parametrize("options", [{}, {"indent": 2}, {"separators": (",", ":")}])
def test_wide_default_callbacks_match_stdlib_once_including_mutation(options):
    def run(dumps):
        sentinel = object()
        value = [None] * _WIDE_SIZE + [sentinel]
        calls = []

        def default(item):
            assert item is sentinel
            calls.append(item)
            value.append("added by the callback")
            return {"custom": 7}

        return dumps(value, default=default, **options), len(calls)

    assert run(jsonx.dumps) == run(json.dumps)


@pytest.mark.parametrize("integer", [-(2**63) - 1, 2**64, 2**1000])
@pytest.mark.parametrize("options", [{}, {"indent": 2}, {"separators": (",", ":")}])
def test_wide_oversized_integer_fallback_has_exact_stdlib_output(integer, options):
    value = [None] * _WIDE_SIZE + [integer]
    assert jsonx.dumps(value, **options) == json.dumps(value, **options)
    assert (
        jsonx.dumpb(value)
        == json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    )


@pytest.mark.parametrize("options", [{}, {"indent": 2}, {"separators": (",", ":")}])
def test_wide_integer_digit_limit_errors_match_stdlib(options):
    value = [None] * _WIDE_SIZE + [10**700]
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(ValueError, match="limit") as expected:
            json.dumps(value, **options)
        with pytest.raises(ValueError, match="limit") as actual:
            jsonx.dumps(value, **options)
        assert str(actual.value) == str(expected.value)
        with pytest.raises(ValueError, match="limit") as expected:
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        with pytest.raises(ValueError, match="limit") as actual:
            jsonx.dumpb(value)
        assert str(actual.value) == str(expected.value)
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udfff", id="low-surrogate"),
        pytest.param("\ud800\udfff", id="literal-pair"),
    ],
)
@pytest.mark.parametrize("options", [{}, {"indent": 2}, {"separators": (",", ":")}])
def test_wide_surrogate_fallback_preserves_text_and_utf8_errors(text, options):
    value = [None] * _WIDE_SIZE + [text]
    for ensure_ascii in (False, True):
        assert jsonx.dumps(value, ensure_ascii=ensure_ascii, **options) == json.dumps(
            value, ensure_ascii=ensure_ascii, **options
        )
    with pytest.raises(UnicodeEncodeError) as expected:
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    with pytest.raises(UnicodeEncodeError) as actual:
        jsonx.dumpb(value)
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize("options", [{}, {"indent": 2}, {"separators": (",", ":")}])
def test_wide_stream_write_failure_retains_prefix_and_defers_later_callbacks(options):
    failure = OSError("stopped after a partial JSON document")

    def run(dump):
        chunks, callbacks = [], []
        value = [None] * _WIDE_SIZE + [object()]

        class Sink:
            def write(self, chunk):
                if len(chunks) == 17:
                    raise failure
                chunks.append(chunk)

        def default(item):
            callbacks.append(item)
            return "converted"

        with pytest.raises(OSError, match="partial JSON document") as actual:
            dump(value, Sink(), default=default, **options)
        assert actual.value is failure
        assert callbacks == []
        return chunks

    assert run(jsonx.dump) == run(json.dump)


@pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="finite float normalization is needed on 3.12"
)
def test_indented_float_bit_patterns_cache_collisions_and_call_isolation():
    values = [0.0, -0.0, 5e-324, -5e-324, sys.float_info.max]
    for boundary in (1e-4, 1e-5, 1e16, 2**53 * 1.0):
        values.extend(
            (
                math.nextafter(boundary, 0.0),
                boundary,
                math.nextafter(boundary, math.inf),
            )
        )
    for index in range(4096):
        bits = (index * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        value = struct.unpack("!d", bits.to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    buckets = {}
    collisions = []
    for value in values:
        token = orjson.dumps(value)
        hashed = 14695981039346656037
        for byte in token:
            hashed = ((hashed ^ byte) * 1099511628211) & ((1 << 64) - 1)
        slot = hashed & 15
        if slot in buckets and buckets[slot][0] != token:
            collisions.extend([buckets[slot][1], value] * 3)
        buckets[slot] = token, value
    assert len(collisions) > 100
    values.extend(collisions)
    for sort_keys in (False, True):
        option = orjson.OPT_INDENT_2 | (orjson.OPT_SORT_KEYS if sort_keys else 0)
        for ordered in (values, values[::-1]):
            document = {"z": ordered, "a": 'numeric text: -1e-05, 1e+16, "0.0"'}
            assert (
                native.encode_indent(document, option)
                == json.dumps(document, indent=2, sort_keys=sort_keys).encode()
            )


def test_wide_and_indented_fast_paths_preserve_low_recursion_rejection():
    source = textwrap.dedent(
        """
        import json
        import sys
        from functools import partial
        from unstd.serde import jsonx

        def invoke(function, value, depth):
            if depth:
                return invoke(function, value, depth - 1)
            return function(value)

        def accepted(function, value, depth):
            try:
                invoke(function, value, depth)
            except RecursionError:
                return False
            return True

        for limit in (60, 100):
            sys.setrecursionlimit(limit)
            for nesting in (0, 8, 16):
                value = [None] * 65540
                for _ in range(nesting):
                    value = [value]
                for options in ({}, {"indent": 2}, {"separators": (",", ":")}):
                    reference = partial(json.dumps, **options)
                    actual = partial(jsonx.dumps, **options)
                    for depth in range(limit - 14, limit):
                        expected = accepted(reference, value, depth)
                        observed = accepted(actual, value, depth)
                        assert not observed or expected, (limit, nesting, options, depth)
        """
    )
    result = subprocess.run(  # noqa: S603 - isolated fixed-source stack probe
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wide_and_indented_encoding_on_a_small_thread_stack():
    source = textwrap.dedent(
        """
        import json
        import sys
        import threading
        from unstd.serde import jsonx

        threading.stack_size(128 * 1024)
        sys.setrecursionlimit(400)
        errors = []

        def run():
            try:
                values = [[None] * 65540, [1.25, -0.0, 5e-324] * 20]
                for nesting in (0, 8, 16, 17, 80):
                    value = 7
                    for _ in range(nesting):
                        value = [value]
                    values.append(value)
                for value in values:
                    for options in ({}, {"indent": 2}, {"separators": (",", ":")}):
                        assert jsonx.dumps(value, **options) == json.dumps(value, **options)
                    expected = json.dumps(
                        value, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    assert jsonx.dumpb(value) == expected
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        assert not errors, errors
        """
    )
    result = subprocess.run(  # noqa: S603 - isolated fixed-source small-stack probe
        [sys.executable, "-X", "faulthandler", "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["wide", "indent"])
def test_wide_visit_budget_counts_the_root_and_each_scalar(kind):
    value = [None] * (262_144 - 1)
    expected = json.dumps(value, **({"indent": 2} if kind == "indent" else {})).encode()
    assert _native_call(kind, value) == expected
    value.append(None)
    assert _native_call(kind, value) is NotImplemented
    assert native.encode([None] * (65_536 - 1)) is not NotImplemented
    assert native.encode([None] * 65_536) is NotImplemented


def test_small_alias_graph_has_a_bounded_native_proof_without_encoding_expansion():
    source = textwrap.dedent(
        """
        import orjson
        from unstd.serde import _json_native as native

        value = 0
        for _ in range(8):
            value = [value] * 64
        assert native.encode_wide(value, 0, False) is NotImplemented
        assert native.encode_indent(value, orjson.OPT_INDENT_2) is NotImplemented
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed bounded alias-graph proof
        [sys.executable, "-X", "faulthandler", "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr

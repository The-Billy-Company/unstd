"""Native float encoding retains atomicity, bounded work and exact scalars."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from unstd.serde import jsonx
from unstd.serde._internal import json_encode


native = pytest.importorskip("unstd.serde._json_native")
text_writer = getattr(native, "encode_text", getattr(native, "repeat_text", None))
pytestmark = pytest.mark.skipif(
    text_writer is None, reason="native repeated-float text writer unavailable"
)


def _records(text="café"):
    return [{"id": index, "text": text, "score": 1.5} for index in range(500)]


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("spaced", [False, True])
def test_repeated_float_public_encoding_avoids_fallback(
    ensure_ascii, spaced, monkeypatch
):
    value = _records()
    options = {"ensure_ascii": ensure_ascii}
    if not spaced:
        options["separators"] = (",", ":")
    expected = json.dumps(value, **options)
    expected_bytes = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode()

    def forbidden(*args, **kwargs):
        pytest.fail("repeated finite floats fell back to the stdlib encoder")

    monkeypatch.setattr(jsonx, "_DEFAULT_ENCODER", SimpleNamespace(encode=forbidden))
    monkeypatch.setattr(json_encode, "_encoder", forbidden)
    assert jsonx.dumps(value, **options) == expected
    assert jsonx.dumpb(value) == expected_bytes


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("spaced", [False, True])
def test_repeated_float_profile_callbacks_run_outside_complete_encoding(
    ensure_ascii, spaced
):
    value = _records()
    expected_value = [*value, 7]
    options = {"ensure_ascii": ensure_ascii}
    if not spaced:
        options["separators"] = (",", ":")
    expected = json.dumps(expected_value, **options)
    events = []
    sentinel = object()

    def profile(frame, event, argument):
        if argument is text_writer:
            events.append(event)
            if event == "c_call":
                value.append(7)
            elif event == "c_return":
                value.append(sentinel)

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        actual = text_writer(value, ensure_ascii, spaced)
    finally:
        sys.setprofile(previous)
    assert actual == expected
    assert events == ["c_call", "c_return"]
    assert value[-1] is sentinel
    assert text_writer(value, ensure_ascii, spaced) is NotImplemented


def test_repeated_float_trace_mutation_is_revalidated_before_return():
    value = _records()

    def invoke():
        return text_writer(value, True, True)

    def trace(frame, event, argument):
        if frame.f_code is invoke.__code__ and event == "line":
            value[-1]["score"] = object()
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        assert invoke() is NotImplemented
    finally:
        sys.settrace(previous)


@pytest.mark.parametrize("large_integer_tail", [False, True])
def test_pending_gc_cannot_clear_borrowed_input_during_native_walk(large_integer_tail):
    source = textwrap.dedent(
        """
        import gc
        import json
        import sys
        from unstd.serde import _json_native as native

        writer = getattr(native, "encode_text", getattr(native, "repeat_text", None))
        rows = [{"id": i, "text": "café", "score": 1.5} for i in range(500)]
        expected = json.dumps(rows)
        large_integer_tail = LARGE_INTEGER_TAIL
        if large_integer_tail:
            rows.append(2**100)
        finalized = []
        gc.collect()
        gc.disable()

        class Pending:
            def __init__(self):
                self.cycle = self
            def __del__(self):
                rows.clear()
                finalized.append(True)

        def profile(frame, event, argument):
            if argument is writer and event == "c_call":
                Pending()
                gc.set_threshold(1, 1, 1)
                gc.enable()

        sys.setprofile(profile)
        result = writer(rows, True, True)
        gc.disable()
        sys.setprofile(None)
        if large_integer_tail:
            assert result is NotImplemented
        else:
            assert result == expected
        gc.collect()
        assert finalized == [True]
        assert rows == []
        """
    )
    source = source.replace("LARGE_INTEGER_TAIL", repr(large_integer_tail))
    result = subprocess.run(  # noqa: S603 - isolated fixed-source forced-GC probe
        [sys.executable, "-X", "faulthandler", "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_repeated_float_scalar_escapes_and_integer_edges(ensure_ascii):
    text = '"\\\b\f\n\r\t' + "".join(map(chr, range(32))) + "\x7fcafé汉字🙂\u2028\u2029"
    values = _records(text)
    values.extend([-(2**29), 2**29, 2**30 - 1])
    for spaced in (False, True):
        options = {"ensure_ascii": ensure_ascii}
        if not spaced:
            options["separators"] = (",", ":")
        assert text_writer(values, ensure_ascii, spaced) == json.dumps(
            values, **options
        )


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udfff", id="low-surrogate"),
        pytest.param("\ud800\udfff", id="literal-surrogate-pair"),
        2**63,
        2**64 - 1,
        2**64,
        -(2**63),
        -(2**63) - 1,
        math.inf,
        -math.inf,
        math.nan,
        object(),
    ],
)
def test_late_unsupported_value_discards_the_entire_private_result(tail):
    value = _records()
    value.append(tail)
    assert text_writer(value, True, True) is NotImplemented
    assert text_writer(value, False, False) is NotImplemented


def test_operation_local_spelling_cache_is_released_on_success_and_decline():
    text = "dynamic café " + str(id(object()))
    value = _records(text)
    references = sys.getrefcount(text)
    for number in (1.5, -0.0, 5e-324, sys.float_info.max):
        for row in value:
            row["score"] = number
        assert text_writer(value, True, True) == json.dumps(value)
        assert sys.getrefcount(text) == references
    value.append(object())
    assert text_writer(value, True, True) is NotImplemented
    assert sys.getrefcount(text) == references


def test_repeated_float_bit_patterns_and_cache_replacement_match_stdlib():
    values = [0.0, -0.0, 5e-324, -5e-324, sys.float_info.max]
    for index in range(256):
        bits = (index * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        value = struct.unpack("!d", bits.to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    for start in range(0, len(values) - 3, 4):
        group = values[start : start + 4] * 8
        for ensure_ascii in (False, True):
            for spaced in (False, True):
                options = {"ensure_ascii": ensure_ascii}
                if not spaced:
                    options["separators"] = (",", ":")
                assert text_writer(group, ensure_ascii, spaced) == json.dumps(
                    group, **options
                )


def test_repeated_float_writer_flags_are_exact_and_never_coerced():
    class Flag:
        def __bool__(self):
            pytest.fail("the native writer evaluated caller truthiness")

    assert text_writer(_records(), Flag(), True) is NotImplemented
    assert text_writer(_records(), True, Flag()) is NotImplemented
    assert text_writer(_records(), 1, True) is NotImplemented
    assert text_writer(_records(), True, 1) is NotImplemented


def test_repeated_float_output_budget_declines_without_retaining_string_refs():
    text = "🙂" * 256
    value = _records(text)
    references = sys.getrefcount(text)
    assert text_writer(value, True, True) is NotImplemented
    assert sys.getrefcount(text) == references
    assert jsonx.dumps(value) == json.dumps(value)


def test_repeated_float_alias_expansion_is_bounded_before_result_exposure():
    source = textwrap.dedent(
        """
        from unstd.serde import _json_native as native
        writer = getattr(native, "encode_text", getattr(native, "repeat_text", None))
        value = [1.5] * 64
        for _ in range(8):
            value = [value] * 64
        assert writer(value, True, True) is NotImplemented
        assert writer(value, False, False) is NotImplemented
        """
    )
    result = subprocess.run(  # noqa: S603 - bounded native-only alias expansion
        [sys.executable, "-X", "faulthandler", "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("integer", [-(2**63), 2**63, 2**64 - 1, 2**64, 10**700])
def test_late_large_integer_public_fallback_retains_exact_output(integer):
    value = _records()
    value.append(integer)
    assert jsonx.dumps(value) == json.dumps(value)
    assert (
        jsonx.dumpb(value)
        == json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    )


def test_late_digit_limited_integer_preserves_the_stdlib_error():
    value = _records()
    value.append(10**700)
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(ValueError, match="limit") as expected:
            json.dumps(value)
        with pytest.raises(ValueError, match="limit") as actual:
            jsonx.dumps(value)
        assert str(actual.value) == str(expected.value)
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("spaced", [False, True])
def test_unique_float_ieee_corpus_and_cache_evictions(ensure_ascii, spaced):
    values = [0.0, -0.0, 5e-324, -5e-324, sys.float_info.max]
    for index in range(4096):
        bits = (index * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        number = struct.unpack("!d", bits.to_bytes(8, "big"))[0]
        if math.isfinite(number):
            values.append(number)
    values += values[::-1]
    options = {"ensure_ascii": ensure_ascii}
    if not spaced:
        options["separators"] = (",", ":")
    value = {"values": values, "text": "numeric text: 1e-05, -0.0, café🙂"}
    expected = json.dumps(value, **options)
    assert text_writer(value, ensure_ascii, spaced) == expected
    assert jsonx.dumps(value, **options) == expected


@pytest.mark.parametrize(
    "value", [0.0, -0.0, 1.5, 1e-5, 1e16, 5e-324, sys.float_info.max]
)
def test_finite_scalar_float_uses_the_stdlib_spelling(value):
    assert text_writer(value, True, True) == json.dumps(value)


def test_fifth_float_value_replacement_resets_the_previous_spelling():
    values = [1.5, -0.0, 1e-5, 1e16, 5e-324, 0.0, sys.float_info.max] * 200
    for group in (values, values[::-1], values[1:]):
        assert text_writer(group, True, True) == json.dumps(group)

"""Wide decoded trees finish one proof without losing precise numeric fallbacks.

Isolated processes substitute a native builtin for the precise parser before
the facade captures it. Its integer result makes an unnecessary second parse
observable without profiling callbacks that disable native dispatch. Public
stdlib comparisons retain numeric values, errors and hook histories separately.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from unstd.serde import jsonx


pytest.importorskip("unstd.serde._json_native")
pytest.importorskip("msgspec")
pytest.importorskip("orjson")


def _fast_worker(body: str) -> None:
    source = textwrap.dedent("""
        import json
        import sys
        from types import SimpleNamespace
        import msgspec
        msgspec.json.Decoder = lambda: SimpleNamespace(decode=len)
        from unstd.serde import jsonx, _json_native as native

        control = {'padding': 'x' * 2050, 'tree': [1, 2]}
        document = json.dumps(control)
        observed = native.loads_lines(document)
        if observed == [len(document)]:
            raise SystemExit(77)
        assert observed == [control], observed

        def assert_document(document):
            for raw in (document, document.encode()):
                expected = json.loads(raw)
                result = native.loads_lines(raw)
                assert type(result) is list and len(result) == 1, result
                actual = result[0]
                assert type(actual) is type(expected), (actual, expected)
                assert json.dumps(actual) == json.dumps(expected), (actual, expected)
                # Earlier CPython versions do not install custom vectorcall
                # bindings; the direct native batch above still tests routing.
                if sys.version_info[:2] == (3, 14):
                    actual = jsonx.loads(raw)
                    assert type(actual) is type(expected), (actual, expected)
                    assert json.dumps(actual) == json.dumps(expected)
    """)
    source += textwrap.dedent(body)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and literal routing probes
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("source-parser stack guard selected the precise decoder")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_wide_exact_documents_never_reparse_after_the_original_visit_limit() -> None:
    _fast_worker("""
        values = [
            list(range(100_000)),
            [[] for _ in range(100_000)],
            [{'id': index, 'text': 'café😀', 'active': True}
             for index in range(30_000)],
            {str(index): index for index in range(100_000)},
        ]
        for value in values:
            assert_document(json.dumps(value, ensure_ascii=False))
    """)


def test_quoted_numbers_and_escaped_structure_keep_the_fast_parser() -> None:
    _fast_worker(r"""
        for marker in ('ascii', 'café', '界', '😀'):
            for slashes in range(9):
                text = (marker + '\\' * slashes + '"[],{}: 0.125 1e200 '
                        + '9' * 100 + '\\' * (slashes + 1)) * 64
                value = {'text': text, 'small_integer': 999999999999999999,
                         'negative': -999999999999999999, 'tail': [True, None]}
                assert_document(json.dumps(value, ensure_ascii=False))
    """)


@pytest.mark.parametrize(
    "number", ["0.84551240822557006", "-0.0", "18446744073709551616", "9" * 640]
)
def test_late_numeric_values_remain_exact_after_many_siblings(number):
    document = "[" + "0," * 100_000 + number + "]"
    for raw in (document, document.encode()):
        expected = json.loads(raw)
        actual = jsonx.loads(raw)
        assert type(actual[-1]) is type(expected[-1])
        assert json.dumps(actual) == json.dumps(expected)


def _late_document(marker: str, token: str) -> str:
    trap = marker + r'"\\ [{]}, 0.125 -1e9 9999999999999999999 '
    return (
        '{"padding":'
        + json.dumps(trap * 128, ensure_ascii=False)
        + ',"value":'
        + token
        + "}"
    )


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
@pytest.mark.parametrize(
    "token",
    [
        "-0",
        "-0.0",
        "1e-4000",
        "-1e-4000",
        "2.4703282292062327e-324",
        "1.7976931348623157e308",
        "1e400",
        "-1e400",
        "999999999999999999",
        "1000000000000000000",
        "18446744073709551616",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_late_numeric_values_match_stdlib_across_quoted_lexical_traps(marker, token):
    document = _late_document(marker, token)
    for raw in (document, document.encode()):
        expected = json.loads(raw)
        actual = jsonx.loads(raw)
        assert type(actual["value"]) is type(expected["value"])
        # Re-encoding distinguishes integer/float and signed zero, and retains
        # stdlib's accepted non-finite extensions without NaN equality pitfalls.
        assert json.dumps(actual) == json.dumps(expected)


@pytest.mark.parametrize(
    "token",
    ["-0", "-0.0", "999999999999999999", "1000000000000000000", "1e-4000", "1e400"],
)
def test_scalar_number_at_source_end_retains_its_type_and_value(token):
    document = " \t\r\n" * 600 + token
    for raw in (document, document.encode()):
        expected = json.loads(raw)
        actual = jsonx.loads(raw)
        assert type(actual) is type(expected)
        assert json.dumps(actual) == json.dumps(expected)


def _assert_same_json_failure(document: str) -> None:
    for raw in (document, document.encode()):
        with pytest.raises(json.JSONDecodeError) as expected:
            json.loads(raw)
        with pytest.raises(json.JSONDecodeError) as actual:
            jsonx.loads(raw)
        assert type(actual.value) is type(expected.value)
        assert actual.value.args == expected.value.args
        for attribute in ("msg", "doc", "pos", "lineno", "colno"):
            assert getattr(actual.value, attribute) == getattr(
                expected.value, attribute
            )


@pytest.mark.parametrize("token", ["-0", "999999999999999999", "1e-4000"])
@pytest.mark.parametrize("suffix", ["x", "]", "."])
def test_scalar_number_with_invalid_suffix_retains_stdlib_diagnostics(token, suffix):
    _assert_same_json_failure(" \t\r\n" * 600 + token + suffix)


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
@pytest.mark.parametrize(
    "token",
    ["-", "01", "-01", "+1", ".1", "1.", "1e", "1e+", "--1", "NaNfalse"],
)
def test_late_invalid_numeric_tokens_retain_complete_stdlib_diagnostics(marker, token):
    _assert_same_json_failure(_late_document(marker, token))


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
@pytest.mark.parametrize("suffix", ["]", " false", "\x00", "\u00a0", ","])
def test_trailing_invalid_input_retains_stdlib_source_and_position(marker, suffix):
    _assert_same_json_failure(_late_document(marker, "0.84551240822557006") + suffix)


def test_duplicate_value_hooks_observe_each_original_numeric_token_once() -> None:
    document = (
        _late_document("😀", "1000000000000000000")[:-1]
        + ',"value":-0.0,"value":1e-4000,"value":NaN}'
    )

    def decoded(function, raw):
        calls = []

        def integer(token):
            calls.append(("int", token))
            return ("integer", token)

        def floating(token):
            calls.append(("float", token))
            return ("floating", token)

        def constant(token):
            calls.append(("constant", token))
            return ("constant", token)

        def pairs(items):
            calls.append(("pairs", tuple(key for key, _value in items)))
            return tuple(items)

        value = function(
            raw,
            parse_int=integer,
            parse_float=floating,
            parse_constant=constant,
            object_pairs_hook=pairs,
        )
        return value, calls

    for raw in (document, document.encode()):
        actual = decoded(jsonx.loads, raw)
        expected = decoded(json.loads, raw)
        assert actual == expected
        assert actual[1] == [
            ("int", "1000000000000000000"),
            ("float", "-0.0"),
            ("float", "1e-4000"),
            ("constant", "NaN"),
            ("pairs", ("padding", "value", "value", "value", "value")),
        ]

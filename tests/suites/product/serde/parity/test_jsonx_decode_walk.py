"""Wide decoded trees finish one proof while retaining precise numeric fallbacks."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from unstd.serde import jsonx, ndjson


pytest.importorskip("unstd.serde._json_native")
pytest.importorskip("msgspec")


def _proof_worker(body: str) -> None:
    # A deliberately different builtin precise decoder makes an unintended
    # second parse observable. Keep orjson real, and bind both before importing
    # the facade. No profiling callback can disable the native dispatch here.
    source = textwrap.dedent("""
        import json
        from types import SimpleNamespace
        import msgspec
        msgspec.json.Decoder = lambda: SimpleNamespace(decode=len)
        from unstd.serde import jsonx, _json_native as native
        jsonx.loads('null')
        control = {'padding': 'x' * 2050, 'tree': [1, 2]}
        control_document = json.dumps(control)
        observed = native.loads_lines(control_document)
        if observed == [len(control_document)]:
            raise SystemExit(77)  # The platform's source-stack guard chose msgspec.
        assert observed == [control], observed
    """) + textwrap.dedent(body)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and literal proof audit
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("native result proof requires sufficient source-parser stack room")
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("mapping", [False, True])
def test_wide_sibling_containers_complete_without_a_second_parse(mapping: bool) -> None:
    _proof_worker(f"""
        rows = [{{'id': index, 'text': 'café😀', 'active': True}}
                for index in range(1000)]
        tree = {{str(index): row for index, row in enumerate(rows)}} if {mapping} else rows
        value = {{'padding': 'x' * 2050, 'tree': tree}}
        document = json.dumps(value, ensure_ascii=False)
        assert native.loads_lines(document) == [value]
        assert native.loads_lines(document.encode()) == [value]
    """)


def test_wide_documents_beyond_the_old_visit_budget_use_one_parse() -> None:
    _proof_worker("""
        # These counts straddle the old 65,536-value result-proof limit.
        # The source-sized result proof admits each complete parsed tree.
        for count in (65534, 65535, 100000):
            value = {'padding': 'x' * 2050, 'tree': [[] for _ in range(count)]}
            document = json.dumps(value)
            assert native.loads_lines(document) == [value], count
            assert native.loads_lines(document.encode()) == [value], count
    """)


def test_deep_refusal_uses_the_precise_parser_with_bounded_c_recursion() -> None:
    _proof_worker("""
        for depth in (1, 32, 63, 64, 128):
            tree = 0
            for level in range(depth):
                tree = [tree] if level % 2 == 0 else {'nested': tree}
            value = {'padding': 'x' * 2050, 'tree': tree}
            document = json.dumps(value)
            expected = [value] if depth < 64 else [len(document)]
            assert native.loads_lines(document) == expected, depth
    """)


@pytest.mark.parametrize(
    "number", ["0.84551240822557006", "18446744073709551616", "-0.0"]
)
def test_late_numbers_in_wide_trees_still_require_the_precise_parser(
    number: str,
) -> None:
    _proof_worker(f"""
        rows = [{{'value': index}} for index in range(1000)]
        prefix = '{{"padding":"' + 'x' * 2050 + '","tree":'
        document = prefix + json.dumps(rows)[:-1] + ',{{"number":{number}}}]}}'
        assert native.loads_lines(document) == [len(document)]
    """)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize(
    "number", ["0.84551240822557006", "18446744073709551616", "-0.0"]
)
def test_public_wide_decode_retains_numeric_types_bits_and_key_order(
    binary: bool, number: str
) -> None:
    rows = [{"value": index} for index in range(1000)]
    document = (
        '{"padding":"'
        + "x" * 2050
        + '","tree":'
        + json.dumps(rows)[:-1]
        + ',{"number":'
        + number
        + "}]}"
    )
    raw = document.encode() if binary else document
    expected = json.dumps(json.loads(raw))
    assert json.dumps(jsonx.loads(raw)) == expected
    assert json.dumps(ndjson.loads(raw)[0]) == expected


def test_discarded_values_keep_source_integer_and_recursion_failures() -> None:
    source = textwrap.dedent("""
        import json
        import sys
        from unstd.serde import jsonx

        sys.set_int_max_str_digits(640)
        def outcome(function, document):
            try:
                return ('value', function(document))
            except Exception as error:
                return ('error', type(error), str(error))

        documents = [
            '{"padding":"' + 'x' * 2050 + '","discarded":' + '9' * 700
            + ',"discarded":0}',
        ]
        for depth in (63, 64, 128, 2048, 6000):
            documents.append('{"padding":"' + 'x' * 2050 + '","discarded":'
                             + '[' * depth + '0' + ']' * depth + ',"discarded":0}')
        for document in documents:
            for raw in (document, document.encode()):
                assert outcome(jsonx.loads, raw) == outcome(json.loads, raw)
    """)
    completed = subprocess.run(  # noqa: S603 - isolated source-limit contracts
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

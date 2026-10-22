"""Malformed bytes reject with ruamel's documented error classes and marks.

Every rejected input has a valid neighbour that differs only in the defect and
parses to a value transcribed from YAML 1.2 (§5.2 encodings, §5.1 printable
set, §5.7 escapes, §6.1 indentation). Marks are 0-based and hand-counted.
"""

from __future__ import annotations

import io

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


OPERATIONS = ["loads", "parse", "load", "load_all"]


def _read(operation: str, raw: bytes) -> object:
    if operation == "load":
        return yaml.load(io.BytesIO(raw))
    if operation == "load_all":
        return list(yaml.load_all(io.BytesIO(raw)))
    return getattr(yaml, operation)(raw)


def _single(operation: str, value: object) -> object:
    return [value] if operation == "load_all" else value


def _error(name: str) -> type[Exception]:
    from ruamel.yaml import composer, parser, reader, scanner

    for module in (reader, scanner, parser, composer):
        if hasattr(module, name):
            error: type[Exception] = getattr(module, name)
            assert issubclass(error, yaml.YAMLError)
            return error
    raise LookupError(name)


# (defect, valid neighbour, neighbour value, error class, 0-based mark)
CASES = {
    "invalid-start-byte": (
        b"key: \xff\n",
        b"key: \xc3\xbf\n",
        {"key": "ÿ"},
        "ReaderError",
        5,
    ),
    "overlong-solidus": (
        b"key: \xc0\xaf\n",
        b"key: /\n",
        {"key": "/"},
        "ReaderError",
        5,
    ),
    "lone-continuation": (
        b"key: \x80\n",
        b"key: \xc2\xa9\n",
        {"key": "©"},
        "ReaderError",
        5,
    ),
    "truncated-sequence": (
        b"key: caf\xc3",
        b"key: caf\xc3\xa9",
        {"key": "café"},
        "ReaderError",
        8,
    ),
    "encoded-surrogate": (
        b"key: \xed\xa0\x80\n",
        b"key: \xed\x9f\xbf\n",
        {"key": "\ud7ff"},
        "ReaderError",
        5,
    ),
    "raw-nul": (
        b"key: a\x00b\n",
        b'key: "a\\0b"\n',
        {"key": "a\x00b"},
        "ReaderError",
        6,
    ),
    "raw-bell": (b"key: \x07\n", b'key: "\\a"\n', {"key": "\x07"}, "ReaderError", 5),
    "raw-escape": (b"key: \x1b\n", b'key: "\\e"\n', {"key": "\x1b"}, "ReaderError", 5),
    "raw-delete": (
        b"key: \x7f\n",
        b'key: "\\x7F"\n',
        {"key": "\x7f"},
        "ReaderError",
        5,
    ),
    "raw-c1": (
        b"key: \xc2\x9f\n",
        b'key: "\\x9F"\n',
        {"key": "\x9f"},
        "ReaderError",
        5,
    ),
    "tab-indentation": (
        b"outer:\n\tinner: 1\n",
        b"outer:\n  inner: 1\n",
        {"outer": {"inner": 1}},
        "ScannerError",
        (1, 0),
    ),
    "unterminated-double": (
        b'key: "abc\n',
        b'key: "abc"\n',
        {"key": "abc"},
        "ScannerError",
        (1, 0),
    ),
    "unterminated-single": (
        b"key: 'abc\n",
        b"key: 'abc'\n",
        {"key": "abc"},
        "ScannerError",
        (1, 0),
    ),
    "unknown-escape": (
        b'key: "\\q"\n',
        b'key: "\\_"\n',
        {"key": "\xa0"},
        "ScannerError",
        (0, 7),
    ),
    "unterminated-flow-sequence": (
        b"key: [1, 2\n",
        b"key: [1, 2]\n",
        {"key": [1, 2]},
        "ParserError",
        (1, 0),
    ),
    "unterminated-flow-mapping": (
        b"key: {a: 1\n",
        b"key: {a: 1}\n",
        {"key": {"a": 1}},
        "ParserError",
        (1, 0),
    ),
    "undefined-alias": (
        b"a: *nope\n",
        b"b: &nope 1\na: *nope\n",
        {"b": 1, "a": 1},
        "ComposerError",
        (0, 3),
    ),
}


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_malformed_input_rejects_with_its_class_and_mark(operation, case):
    raw, neighbour, expected, name, mark = CASES[case]
    with pytest.raises(_error(name)) as caught:
        _read(operation, raw)
    if isinstance(mark, int):
        assert caught.value.position == mark
    else:
        problem = caught.value.problem_mark
        assert (problem.line, problem.column) == mark
    assert _read(operation, neighbour) == _single(operation, expected)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbfkey: value\n",
        "\ufeffkey: value\n".encode("utf-16-le"),
        "\ufeffkey: value\n".encode("utf-16-be"),
    ],
    ids=["utf-8", "utf-16-le", "utf-16-be"],
)
def test_byte_order_marks_select_the_stream_encoding(operation, raw):
    assert _read(operation, raw) == _single(operation, {"key": "value"})


def test_a_bom_before_text_and_stream_documents_is_not_content():
    assert yaml.loads("\ufeffkey: value\n") == {"key": "value"}
    assert list(yaml.load_all(b"\xef\xbb\xbf---\na: 1\n---\nb: 2\n")) == [
        {"a": 1},
        {"b": 2},
    ]


def test_double_quoted_escapes_cover_the_specified_table():
    source = r'k: "\x41\u00e9\U0001F600\N\_\L\P\0\a\e\t\/\""' + "\n"
    expected = 'Aé😀\x85\xa0\u2028\u2029\x00\x07\x1b\t/"'
    assert yaml.loads(source) == {"k": expected}
    assert yaml.parse(source) == {"k": expected}


@pytest.mark.parametrize("operation", ["loads", "parse"])
def test_tabs_remain_content_inside_quotes_and_separate_flow_items(operation):
    assert getattr(yaml, operation)('key: "a\tb"\nflow: [1,\t2]\n') == {
        "key": "a\tb",
        "flow": [1, 2],
    }


def test_rejections_leave_later_reads_working():
    for raw, *_ in CASES.values():
        with pytest.raises(yaml.YAMLError):
            yaml.loads(raw)
    assert yaml.loads(b"working: true\n") == {"working": True}

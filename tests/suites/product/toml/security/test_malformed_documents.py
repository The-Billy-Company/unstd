"""Malformed TOML is rejected with a located error by both halves.

Each document violates a rule shared by TOML 1.0.0 and 1.1.0, so rejection is
the specification's answer for the reader *and* the editable parser. The
reported line is the hand-identified line carrying the defect. Valid controls
share each corpus document's shape so a parser that rejected everything fails.
"""

from __future__ import annotations

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


# (case, malformed document, defect line or None for end of document, valid twin)
CORPUS = [
    ("duplicate-key", "a = 1\na = 2\n", 2, "a = 1\nb = 2\n"),
    ("redeclared-table", "[a]\nb = 1\n[a]\n", 3, "[a]\nb = 1\n[c]\n"),
    ("bare-equals", "x = 1\n= 1\n", 2, "x = 1\ny = 1\n"),
    ("missing-value", "a =\n", 1, "a = 0\n"),
    ("unterminated-string", 'x = 1\n\n[t]\nk = "v\n', 4, 'x = 1\n\n[t]\nk = "v"\n'),
    ("unknown-escape", 'a = "\\q"\n', 1, 'a = "\\t"\n'),
    ("raw-control-char", 'a = "\x01"\n', 1, 'a = "\\u0001"\n'),
    ("leading-zero", "a = 01\n", 1, "a = 1\n"),
    ("dangling-underscore", "a = 1_\n", 1, "a = 1_0\n"),
    ("bare-fraction", "a = .5\n", 1, "a = 0.5\n"),
    ("calendar-overflow", "a = 1979-02-30\n", 1, "a = 1979-02-28\n"),
    ("inline-table-extended", "a = {x = 1}\na.y = 2\n", 2, "a = {x = 1, y = 2}\n"),
    ("static-array-as-aot", "a = [1]\n[[a]]\n", 2, "a = [1]\n[[b]]\n"),
    ("bare-carriage-return", "a = 1\rb = 2\n", 1, "a = 1\r\nb = 2\n"),
    ("unclosed-array", "x = [1, 2\n", None, "x = [1, 2]\n"),
]


@pytest.mark.parametrize(
    ("source", "line", "valid"), [c[1:] for c in CORPUS], ids=[c[0] for c in CORPUS]
)
def test_reader_rejects_with_defect_location(
    source: str, line: int | None, valid: str
) -> None:
    where = r"at end of document" if line is None else rf"at line {line}, column \d+"
    with pytest.raises(toml.TOMLDecodeError, match=rf"\({where}\)$"):
        toml.loads(source)
    assert isinstance(toml.loads(valid), dict)


@pytest.mark.parametrize(
    ("source", "valid"), [(c[1], c[3]) for c in CORPUS], ids=[c[0] for c in CORPUS]
)
def test_editable_parser_rejects_the_shared_dialect_violations(
    source: str, valid: str
) -> None:
    require_backend("tomlkit")
    # Both backends' errors are ValueErrors; the reader's class is tomllib's own.
    with pytest.raises(ValueError):  # noqa: PT011 - common base is the contract
        toml.parse(source)
    assert toml.dumps(toml.parse(valid)) == valid


def test_decode_error_is_a_value_error_and_reusable_after_failure() -> None:
    assert issubclass(toml.TOMLDecodeError, ValueError)
    with pytest.raises(toml.TOMLDecodeError):
        toml.loads("a = = 1\n")
    assert toml.loads("a = 1\n") == {"a": 1}


@pytest.mark.parametrize("source", [b"a = 1\n", bytearray(b"a = 1\n")])
def test_loads_refuses_bytes(source: bytes | bytearray) -> None:
    with pytest.raises(TypeError):
        toml.loads(source)  # type: ignore[arg-type]


DEPTH = 5000


@pytest.mark.parametrize(
    ("source", "child"),
    [
        ("a = " + "[" * DEPTH + "1" + "]" * DEPTH + "\n", lambda node: node[0]),
        ("a = " + "{b = " * DEPTH + "1" + "}" * DEPTH + "\n", lambda node: node["b"]),
    ],
    ids=["arrays", "inline-tables"],
)
def test_deep_nesting_decodes_exactly_or_refuses_catchably(source: str, child) -> None:
    # tomllib is recursive: the interpreter recursion limit decides which outcome.
    try:
        node = toml.loads(source)["a"]
    except (toml.TOMLDecodeError, RecursionError):
        pass
    else:
        for _ in range(DEPTH):
            node = child(node)
        assert node == 1
    assert toml.loads("a = [[1]]\n") == {"a": [[1]]}

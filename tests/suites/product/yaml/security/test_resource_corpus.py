"""Hostile alias, depth, width and length inputs stay bounded in both readers.

Large inputs run in killable workers under a parent deadline and may only
complete or raise a permitted rejection. Small neighbours of each shape are
checked exactly in process against values built alongside their source.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python
from tests.support.parsers.cases import parser_case
from unstd import yaml


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


READERS = ["loads", "parse"]
_TYPE = {"loads": {"dict", "list"}, "parse": {"CommentedMap", "CommentedSeq"}}


def _alias_graph(depth: int) -> str:
    rows = ['level0: &level0 ["leaf"]']
    rows += [
        f"level{level}: &level{level} [" + ", ".join([f"*level{level - 1}"] * 20) + "]"
        for level in range(1, depth + 1)
    ]
    return "\n".join(rows) + "\n"


def _bounded(operation: str, raw: bytes) -> dict[str, object]:
    result = parser_case("yaml", raw, operation=operation)
    assert result == {"kind": "error", "type": "RecursionError"} or (
        result["kind"] == "value" and result["type"] in _TYPE[operation]
    ), result
    return result


def test_editable_alias_graph_shares_nodes_instead_of_expanding():
    assert parser_case(
        "yaml",
        _alias_graph(16).encode(),
        operation="parse",
        inspect="alias-graph",
        depth="16",
    ) == {
        "kind": "value",
        "type": "CommentedMap",
        "summary": {
            "keys": sorted(f"level{i}" for i in range(17)),
            "leaf": ["leaf"],
            "identity": True,
        },
    }


_DUMP_GRAPH = """\
import json, sys
from unstd import yaml
source = sys.argv[1]
report = {}
for reader in ("loads", "parse"):
    text = yaml.dumps(getattr(yaml, reader)(source))
    again = yaml.loads(text)
    report[reader] = {
        "length": len(text),
        "identity": all(
            child is again[f"level{level - 1}"]
            for level in range(1, 17)
            for child in again[f"level{level}"]
        ),
    }
print(json.dumps(report))
"""


def test_writer_emits_aliases_for_a_shared_graph(tmp_path: Path):
    # Expansion would emit 20**16 leaves; the parent deadline kills that worker.
    result = run_python(_DUMP_GRAPH, [_alias_graph(16)], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    for reader in READERS:
        assert report[reader]["identity"] is True
        assert report[reader]["length"] < 4 * len(_alias_graph(16))


@pytest.mark.parametrize("operation", READERS)
@pytest.mark.parametrize(
    ("opening", "closing"), [(b"[", b"]"), (b"{a: ", b"}")], ids=["seq", "map"]
)
def test_balanced_deep_flow_nesting_is_contained(operation, opening, closing):
    _bounded(operation, opening * 600 + closing * 600)
    nested: object = "leaf"
    for _ in range(40):
        nested = [nested] if opening == b"[" else {"a": nested}
    source = (opening * 40).decode() + "leaf" + (closing * 40).decode()
    assert getattr(yaml, operation)(source) == nested


@pytest.mark.parametrize("operation", READERS)
def test_deep_block_indentation_is_contained(operation):
    rows = "".join("  " * level + f"k{level}:\n" for level in range(400))
    _bounded(operation, (rows + "  " * 400 + "leaf\n").encode())
    rows = "".join("  " * level + f"k{level}:\n" for level in range(30))
    expected: object = "leaf"
    for level in reversed(range(30)):
        expected = {f"k{level}": expected}
    assert getattr(yaml, operation)(rows + "  " * 30 + "leaf\n") == expected


@pytest.mark.parametrize("operation", READERS)
def test_wide_collections_complete(operation):
    wide_sequence = "[" + ", ".join(map(str, range(5000))) + "]"
    wide_mapping = "".join(f"k{i}: {i}\n" for i in range(3000))
    assert _bounded(operation, wide_sequence.encode())["kind"] == "value"
    assert _bounded(operation, wide_mapping.encode())["kind"] == "value"
    read = getattr(yaml, operation)
    assert read("[" + ", ".join(map(str, range(1000))) + "]") == list(range(1000))
    assert read("".join(f"k{i}: {i}\n" for i in range(1000))) == {
        f"k{i}": i for i in range(1000)
    }


@pytest.mark.parametrize("operation", READERS)
def test_long_scalars_and_explicit_keys_complete(operation):
    assert _bounded(operation, b"key: " + b"x" * 300_000 + b"\n")["kind"] == "value"
    assert _bounded(operation, b"? " + b"k" * 100_000 + b"\n: 1\n")["kind"] == "value"
    read = getattr(yaml, operation)
    assert read("key: " + "x" * 50_000 + "\n") == {"key": "x" * 50_000}
    assert read("? " + "k" * 50_000 + "\n: 1\n") == {"k" * 50_000: 1}


# YAML 1.2 §7.4.2/§8.2.2: an implicit key is restricted to 1024 characters.
@pytest.mark.parametrize("operation", READERS)
def test_implicit_keys_beyond_the_spec_limit_reject(operation):
    assert parser_case("yaml", b"k" * 1100 + b": 1\n", operation=operation) == {
        "kind": "error",
        "type": "ScannerError",
    }
    assert getattr(yaml, operation)("k" * 1000 + ": 1\n") == {"k" * 1000: 1}


@pytest.mark.parametrize("operation", READERS)
def test_many_distinct_anchors_complete(operation):
    rows = "".join(f"a{i}: &a{i} [{i}]\nb{i}: *a{i}\n" for i in range(2000))
    assert _bounded(operation, rows.encode())["kind"] == "value"
    value = getattr(yaml, operation)(rows[: rows.index("a50:")])
    assert value == {key: [i] for i in range(50) for key in (f"a{i}", f"b{i}")}
    assert all(value[f"a{i}"] is value[f"b{i}"] for i in range(50))

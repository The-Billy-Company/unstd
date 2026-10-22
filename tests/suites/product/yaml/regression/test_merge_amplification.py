"""Repeated merges of one anchor must not amplify a small document.

A 2 KB document merging the previous anchor ten times per level made the safe
readers copy 10**depth pairs; depth 12 never finished inside the worker deadline.
Expected values follow the merge-key rules: earlier sources win, keys union.
"""

from __future__ import annotations

import io

import pytest

from tests.support.environment.backends import require_backend
from tests.support.parsers.cases import parser_case
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def _fanout(depth: int, width: int = 10) -> str:
    rows = ["l0: &l0 {k0: 0}"]
    rows += [
        f"l{i}: &l{i} {{<<: [{', '.join([f'*l{i - 1}'] * width)}], k{i}: {i}}}"
        for i in range(1, depth + 1)
    ]
    return "\n".join(rows) + "\n"


def _chain(depth: int) -> str:
    rows = ["l0: &l0 {k0: 0}"]
    rows += [
        f"l{i}: &l{i} {{<<: [{', '.join(f'*l{j}' for j in range(i))}], k{i}: {i}}}"
        for i in range(1, depth + 1)
    ]
    return "\n".join(rows) + "\n"


def _union(depth: int) -> dict[str, dict[str, int]]:
    return {f"l{i}": {f"k{j}": j for j in range(i + 1)} for i in range(depth + 1)}


@pytest.mark.parametrize("operation", ["loads", "parse"])
def test_fanout_merges_complete_with_the_exact_union(operation):
    raw = _fanout(12).encode()
    assert parser_case("yaml", raw, operation=operation, inspect="value") == {
        "kind": "value",
        "type": "CommentedMap" if operation == "parse" else "dict",
        "value": _union(12),
    }


@pytest.mark.parametrize("operation", ["loads", "parse"])
def test_chained_merges_complete_inside_the_worker_deadline(operation):
    result = parser_case("yaml", _chain(40).encode(), operation=operation)
    assert result == {
        "kind": "value",
        "type": "CommentedMap" if operation == "parse" else "dict",
    }
    assert yaml.loads(_chain(8)) == _union(8)


def test_deduplicated_merges_keep_precedence_order_and_backend_parity():
    from ruamel.yaml import YAML

    source = (
        "a: &a {x: a, only_a: 1}\n"
        "b: &b {<<: [*a, *a], x: b}\n"
        "c: {<<: [*b, *a, *b], y: c}\n" + _fanout(3).replace("l", "f")
    )
    value = yaml.loads(source)
    assert value["b"] == {"x": "b", "only_a": 1}
    assert value["c"] == {"x": "b", "only_a": 1, "y": "c"}
    plain = YAML(typ="safe", pure=True).load(source)
    assert [(key, list(item.items())) for key, item in value.items()] == [
        (key, list(item.items())) for key, item in plain.items()
    ]


@pytest.mark.parametrize("operation", ["loads", "load", "load_all"])
@pytest.mark.parametrize("sources", ["*a, *b, *a", "*b, *a, *b", "*a, *a, *b, *a, *a"])
def test_repeated_aliases_preserve_first_key_order_and_last_pair_precedence(
    operation, sources
):
    from ruamel.yaml import YAML

    source = (
        "a: &a {x: a, only_a: 1}\n"
        "b: &b {x: b, only_b: 2}\n"
        f"c: {{<<: [{sources}], explicit: 3}}\n"
    )
    if operation == "loads":
        value = yaml.loads(source)
    elif operation == "load":
        value = yaml.load(io.StringIO(source))
    else:
        (value,) = yaml.load_all(io.StringIO(source))
    plain = YAML(typ="safe", pure=True).load(source)
    assert value == plain
    assert list(value["c"].items()) == list(plain["c"].items())


@pytest.mark.parametrize("operation", ["loads", "parse"])
def test_duplicate_explicit_keys_are_still_rejected_after_deduplication(operation):
    source = _fanout(3) + "bad: {<<: [*l3, *l3], k9: 1, k9: 2}\n"
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        getattr(yaml, operation)(source)

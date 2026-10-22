"""Safe and editable YAML never construct attacker-selected Python objects.

Success controls share each exercised public entry point. Alias/depth probes
execute in killable workers; alias preservation is checked without expanding
the graph into a second representation.
"""

from __future__ import annotations

import io
import json

import pytest

from tests.support.environment.backends import require_backend
from tests.support.parsers.cases import parser_case
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def _read(operation, source):
    if operation == "load_all":
        return list(yaml.load_all(source))
    if operation == "load":
        return yaml.load(io.StringIO(source))
    return getattr(yaml, operation)(source)


@pytest.mark.parametrize("operation", ["loads", "load", "parse", "load_all"])
@pytest.mark.parametrize(
    "tag",
    [
        "!!python/object/apply:builtins.eval",
        "!!python/object/new:builtins.eval",
        "!attacker",
    ],
)
def test_unknown_and_python_tags_cannot_execute_a_constructor(operation, tag, tmp_path):
    marker = tmp_path / "constructor-executed"
    expression = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    source = tag + " [" + json.dumps(expression) + "]\n"
    with pytest.raises(yaml.YAMLError):
        _read(operation, source)
    assert not marker.exists()
    expected = [{"safe": True}] if operation == "load_all" else {"safe": True}
    assert _read(operation, "safe: true\n") == expected
    # Explicit scalar tagging cannot turn code-shaped text into a constructor.
    literal = "!!str " + json.dumps(expression) + "\n"
    value = _read(operation, literal)
    assert value == ([expression] if operation == "load_all" else expression)
    assert not marker.exists()


@pytest.mark.parametrize("operation", ["loads", "parse", "load_all"])
@pytest.mark.parametrize(
    "source",
    [
        "role: reader\nrole: admin\n",
        "outer: {role: reader, role: admin}\n",
        "{true: first, 1: second}\n",
    ],
)
def test_duplicate_semantic_mapping_keys_are_not_silently_overwritten(
    operation, source
):
    with pytest.raises(yaml.YAMLError):
        _read(operation, source)
    expected = (
        [{"1": "text", 1: "integer"}]
        if operation == "load_all"
        else {"1": "text", 1: "integer"}
    )
    assert _read(operation, "{'1': text, 1: integer}\n") == expected


@pytest.mark.parametrize("operation", ["loads", "parse"])
def test_merge_overrides_are_a_permitted_control_not_duplicate_source_keys(operation):
    source = "defaults: &defaults {role: reader, enabled: true}\nuser: {<<: *defaults, role: writer}\n"
    assert _read(operation, source)["user"] == {"role": "writer", "enabled": True}


def test_bad_later_document_never_executes_and_does_not_close_the_callers_stream(
    tmp_path,
):
    marker = tmp_path / "must-not-exist"
    expression = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    source = io.StringIO(
        "---\nsafe: true\n---\n!!python/object/apply:builtins.eval ["
        + json.dumps(expression)
        + "]\n---\nlater: true\n"
    )
    documents = yaml.load_all(source)
    assert next(documents) == {"safe": True}
    with pytest.raises(yaml.YAMLError):
        next(documents)
    assert not source.closed
    assert not marker.exists()
    assert yaml.loads("safe: true\n") == {"safe": True}


def _alias_graph(depth):
    rows = ['level0: &level0 ["leaf"]']
    rows.extend(
        f"level{level}: &level{level} [" + ", ".join([f"*level{level - 1}"] * 20) + "]"
        for level in range(1, depth + 1)
    )
    return "\n".join(rows) + "\n"


def test_alias_graph_preserves_shared_nodes_and_avoids_expansion():
    value = yaml.loads(_alias_graph(5))
    for level in range(1, 6):
        assert len(value[f"level{level}"]) == 20
        assert all(
            child is value[f"level{level - 1}"] for child in value[f"level{level}"]
        )
    # Expanding this graph would materialize 20**16 leaves. The worker only
    # accepts normal bounded completion; a kill/OOM/timeout is a failing probe.
    assert parser_case(
        "yaml", _alias_graph(16).encode(), inspect="alias-graph", depth="16"
    ) == {
        "kind": "value",
        "type": "dict",
        "summary": {
            "keys": sorted(f"level{i}" for i in range(17)),
            "leaf": ["leaf"],
            "identity": True,
        },
    }


@pytest.mark.parametrize("operation", ["loads", "parse"])
@pytest.mark.parametrize(
    "raw",
    [b"[" * 10000, b"key: [unterminated", b"\xff\xfe\x00", b"value: \x00"],
    ids=["deep-sequence", "truncated-sequence", "invalid-utf16", "nul-byte"],
)
def test_yaml_malformed_depth_and_encoding_cases_are_contained(operation, raw):
    assert parser_case(
        "yaml", b"safe: true\n", operation=operation, inspect="value"
    ) == {
        "kind": "value",
        "type": "CommentedMap" if operation == "parse" else "dict",
        "value": {"safe": True},
    }
    result = parser_case("yaml", raw, operation=operation)
    assert result["kind"] == "error"
    assert result["type"] in {
        "RecursionError",
        "ReaderError",
        "ScannerError",
        "ParserError",
    }


def test_recursive_safe_alias_is_preserved_without_recursive_validation_walk():
    value = yaml.loads("&root [*root]\n")
    assert value[0] is value
    assert parser_case("yaml", b"&root [*root]\n") == {"kind": "value", "type": "list"}

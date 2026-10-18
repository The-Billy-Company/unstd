"""Safe YAML 1.2 values, editable documents, and lazy streams.

Optional-backend tests remain collectable without ruamel. The data contracts are
pinned against representative YAML rather than the shim's parser configuration.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


@pytest.fixture
def yaml_backend() -> ModuleType:
    """The public YAML surface, skipped only when the backend is absent."""
    pytest.importorskip("ruamel.yaml")
    from unstd import yaml

    return yaml


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", None),
        ("# only a comment\n", None),
        ("null\n", None),
        ("hello\n", "hello"),
        ("42\n", 42),
        ("3.25\n", 3.25),
        ("true\n", True),
        ("[one, 2, null]\n", ["one", 2, None]),
        ("name: café\nitems: [1, false]\n", {"name": "café", "items": [1, False]}),
    ],
)
def test_loads_accepts_every_document_root(yaml_backend, source, expected) -> None:
    assert yaml_backend.loads(source) == expected
    assert yaml_backend.loads(source.encode()) == expected


def test_safe_loads_returns_plain_mappings_and_sequences(yaml_backend) -> None:
    value = yaml_backend.loads("items:\n  - name: first\n")
    assert type(value) is dict
    assert type(value["items"]) is list
    assert type(value["items"][0]) is dict


def test_yaml_12_does_not_coerce_legacy_boolean_words(yaml_backend) -> None:
    value = yaml_backend.loads(
        "legacy: [yes, no, on, off, Yes, NO]\n"
        "booleans: [true, false, True, FALSE]\n"
        "leading_zero: 012\n"
        "octal: 0o12\n"
    )
    assert value == {
        "legacy": ["yes", "no", "on", "off", "Yes", "NO"],
        "booleans": [True, False, True, False],
        "leading_zero": 12,
        "octal": 10,
    }


def test_standard_yaml_types_are_safe_native_values(yaml_backend) -> None:
    value = yaml_backend.loads(
        "day: 2026-10-18\n"
        "instant: 2026-10-18T12:34:56Z\n"
        "binary: !!binary SGVsbG8=\n"
        "tags: !!set {alpha: null, beta: null}\n"
        "pairs: !!pairs [{name: first}, {name: second}]\n"
        "explicit: !!str 123\n"
    )
    assert value["day"] == dt.date(2026, 10, 18)
    assert value["instant"] == dt.datetime(2026, 10, 18, 12, 34, 56, tzinfo=dt.UTC)
    assert value["binary"] == b"Hello"
    assert value["tags"] == {"alpha", "beta"}
    assert value["pairs"] == [("name", "first"), ("name", "second")]
    assert value["explicit"] == "123"


def test_yaml_nonfinite_numbers_remain_numbers(yaml_backend) -> None:
    value = yaml_backend.loads("[.inf, -.inf, .nan]\n")
    assert value[0] == math.inf
    assert value[1] == -math.inf
    assert math.isnan(value[2])


@pytest.mark.parametrize("binary", [False, True])
def test_load_reads_caller_owned_handles(yaml_backend, binary) -> None:
    source = "name: café\ncount: 2\n"
    handle = io.BytesIO(source.encode()) if binary else io.StringIO(source)
    assert yaml_backend.load(handle) == {"name": "café", "count": 2}
    assert not handle.closed


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_duplicate_mapping_keys_are_rejected(yaml_backend, reader) -> None:
    with pytest.raises(yaml_backend.YAMLError):
        getattr(yaml_backend, reader)("service:\n  port: 80\n  port: 443\n")


@pytest.mark.parametrize("reader", ["loads", "parse"])
@pytest.mark.parametrize(
    "source",
    [
        "!application value\n",
        "value: !application [one, two]\n",
        "nested: {value: !application {name: first}}\n",
        "!application key: value\n",
        "!!python/object/apply:builtins.str [unexpected]\n",
        "!!python/name:builtins.str ''\n",
        "!!python/object:builtins.object {}\n",
    ],
)
def test_unknown_and_python_tags_are_rejected(yaml_backend, reader, source) -> None:
    with pytest.raises(yaml_backend.YAMLError):
        getattr(yaml_backend, reader)(source)


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_single_document_readers_reject_multiple_documents(
    yaml_backend, reader
) -> None:
    with pytest.raises(yaml_backend.YAMLError):
        getattr(yaml_backend, reader)("first: 1\n---\nsecond: 2\n")


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_parse_failures_do_not_poison_later_calls(yaml_backend, reader) -> None:
    read = getattr(yaml_backend, reader)
    with pytest.raises(yaml_backend.YAMLError):
        read("broken: [\n")
    with pytest.raises(yaml_backend.YAMLError):
        read("bad: !application value\n")
    assert read("working: [1, 2]\n") == {"working": [1, 2]}


def test_yamlerror_is_the_backend_error_base(yaml_backend) -> None:
    from ruamel.yaml.error import YAMLError

    assert yaml_backend.YAMLError is YAMLError


def test_aliases_keep_shared_identity_and_recursive_sequences(yaml_backend) -> None:
    value = yaml_backend.loads("original: &shared [one, two]\ncopy: *shared\n")
    assert value["original"] is value["copy"]
    recursive = yaml_backend.loads("&recursive [*recursive]\n")
    assert recursive[0] is recursive


def test_editable_root_recursive_sequence_roundtrips_its_identity(yaml_backend) -> None:
    value = yaml_backend.parse("&recursive [*recursive]\n")
    assert value[0] is value
    output = yaml_backend.dumps(value)
    assert "&recursive" in output
    assert "*recursive" in output
    reparsed = yaml_backend.loads(output)
    assert reparsed[0] is reparsed


@pytest.mark.parametrize(
    "source", ["node: &node [*node]\n", "node: &node {child: *node}\n"]
)
def test_editable_recursive_aliases_fail_instead_of_losing_data(
    yaml_backend, source
) -> None:
    with pytest.raises(yaml_backend.YAMLError, match="recursive alias"):
        yaml_backend.parse(source)
    assert yaml_backend.parse("working: true\n") == {"working": True}


def test_closing_a_document_iterator_keeps_its_input_open(yaml_backend) -> None:
    handle = io.StringIO("---\nfirst: 1\n---\nsecond: 2\n")
    documents = yaml_backend.load_all(handle)
    assert next(documents) == {"first": 1}
    documents.close()
    assert not handle.closed


def test_safe_merge_keys_respect_explicit_overrides(yaml_backend) -> None:
    value = yaml_backend.loads(
        "defaults: &defaults {host: localhost, port: 80}\n"
        "service:\n  <<: *defaults\n  port: 443\n"
    )
    assert value["service"] == {"host": "localhost", "port": 443}


@pytest.mark.parametrize("binary", [False, True])
def test_editing_preserves_comments_quotes_order_anchors_and_merges(
    yaml_backend, binary
) -> None:
    source = (
        "# application config\n"
        "defaults: &defaults\n"
        '  host: "localhost"  # keep this host\n'
        "  retries: 3\n"
        "service:\n"
        "  <<: *defaults\n"
        "  port: 8080  # public port\n"
        "label: 'on'\n"
    )
    document = yaml_backend.parse(source.encode() if binary else source)
    document["service"]["port"] = 9090
    output = yaml_backend.dumps(document)
    assert "# application config" in output
    assert 'host: "localhost"' in output
    assert "# keep this host" in output
    assert "# public port" in output
    assert "label: 'on'" in output
    assert "&defaults" in output
    assert "<<: *defaults" in output
    assert output.index("defaults:") < output.index("service:") < output.index("label:")
    parsed = yaml_backend.loads(output)
    assert parsed["service"] == {"host": "localhost", "retries": 3, "port": 9090}


@pytest.mark.parametrize(
    "value", [None, "plain", 42, False, [1, None], {"b": 2, "a": 1}]
)
def test_dumps_roundtrips_values_and_returns_text(yaml_backend, value) -> None:
    output = yaml_backend.dumps(value)
    assert isinstance(output, str)
    assert yaml_backend.loads(output) == value


def test_dump_writes_text_without_closing_the_handle(yaml_backend) -> None:
    handle = io.StringIO()
    assert yaml_backend.dump({"name": "café", "enabled": True}, handle) is None
    assert not handle.closed
    assert yaml_backend.loads(handle.getvalue()) == {"name": "café", "enabled": True}


def test_dump_rejects_arbitrary_python_objects(yaml_backend) -> None:
    with pytest.raises(yaml_backend.YAMLError):
        yaml_backend.dumps(object())
    assert yaml_backend.loads(yaml_backend.dumps({"working": True})) == {
        "working": True
    }


@pytest.mark.parametrize(
    "source_kind", ["text", "bytes", "text_handle", "binary_handle"]
)
def test_load_all_accepts_blobs_and_caller_owned_handles(
    yaml_backend, source_kind
) -> None:
    text = "---\nname: café\n---\n[1, 2]\n---\nnull\n"
    sources = {
        "text": text,
        "bytes": text.encode(),
        "text_handle": io.StringIO(text),
        "binary_handle": io.BytesIO(text.encode()),
    }
    source = sources[source_kind]
    assert list(yaml_backend.load_all(source)) == [{"name": "café"}, [1, 2], None]
    if isinstance(source, io.IOBase):
        assert not source.closed


def test_load_all_is_lazy_and_reports_a_later_document_when_reached(
    yaml_backend,
) -> None:
    handle = io.StringIO("---\nfirst: 1\n---\nbroken: [\n")
    documents = yaml_backend.load_all(handle)
    assert iter(documents) is documents
    assert handle.tell() == 0
    assert next(documents) == {"first": 1}
    with pytest.raises(yaml_backend.YAMLError):
        next(documents)
    assert not handle.closed
    assert list(yaml_backend.load_all("---\nworking: true\n")) == [{"working": True}]


@pytest.mark.parametrize("source", ["", b"", "# comment only\n"])
def test_load_all_empty_stream_has_no_documents(yaml_backend, source) -> None:
    assert list(yaml_backend.load_all(source)) == []


@pytest.mark.parametrize("source", ["key: 1\nkey: 2\n", "key: !application value\n"])
def test_load_all_enforces_the_same_safe_document_rules(yaml_backend, source) -> None:
    documents = yaml_backend.load_all("---\nfirst: true\n---\n" + source)
    assert next(documents) == {"first": True}
    with pytest.raises(yaml_backend.YAMLError):
        next(documents)


def test_dump_all_streams_a_generator_and_leaves_the_handle_open(yaml_backend) -> None:
    handle = io.StringIO()

    def documents() -> Iterator[object]:
        yield {"first": 1}
        assert "first: 1" in handle.getvalue()
        yield ["second", None]
        yield None

    assert yaml_backend.dump_all(documents(), handle) is None
    assert not handle.closed
    assert list(yaml_backend.load_all(handle.getvalue())) == [
        {"first": 1},
        ["second", None],
        None,
    ]


def test_base_install_imports_and_every_capability_names_the_extra() -> None:
    code = textwrap.dedent(
        """\
        import importlib.abc
        import io
        import sys

        class BlockRuamel(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "ruamel" or fullname.startswith("ruamel."):
                    raise ModuleNotFoundError("ruamel deliberately unavailable")
                return None

        sys.meta_path.insert(0, BlockRuamel())
        from unstd import yaml

        assert yaml.YAMLError is ValueError
        calls = [
            lambda: yaml.loads("key: value"),
            lambda: yaml.load(io.StringIO("key: value")),
            lambda: yaml.parse("key: value"),
            lambda: yaml.dumps({"key": "value"}),
            lambda: yaml.dump({"key": "value"}, io.StringIO()),
            lambda: list(yaml.load_all("key: value")),
            lambda: yaml.dump_all([{"key": "value"}], io.StringIO()),
        ]
        for call in calls:
            try:
                call()
            except ImportError as error:
                assert "unstd[yaml]" in str(error), str(error)
                assert "pip install" in str(error), str(error)
            else:
                raise AssertionError("YAML operation succeeded without its backend")
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed script and current interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr

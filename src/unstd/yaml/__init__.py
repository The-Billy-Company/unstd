"""Safe YAML 1.2 values and comment-preserving document edits.

``loads`` / ``load`` return native values; ``parse`` retains comments, quotes,
anchors and ordering for ``parse`` → edit → ``dumps``. Both readers reject duplicate
keys and unknown tags. The optional ``yaml`` extra provides ruamel.yaml; there
is no stdlib YAML fallback. Each operation owns its parser and uses the Python
implementation so installing a C extension cannot change scalar resolution.
"""

from __future__ import annotations

from collections.abc import Hashable, MutableSet
from contextlib import closing
from datetime import date, datetime
import io
from typing import IO, TYPE_CHECKING, TypeGuard, override

from unstd._extra import missing


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ruamel.yaml import YAML as _YAML
    from ruamel.yaml.constructor import (
        ConstructorError,
        RoundTripConstructor,
        SafeConstructor,
    )
    from ruamel.yaml.error import YAMLError
    from ruamel.yaml.nodes import MappingNode, Node, ScalarNode
    from ruamel.yaml.representer import RoundTripRepresenter
    from ruamel.yaml.scalarfloat import ScalarFloat

    _HAVE_YAML = True
else:
    try:
        from ruamel.yaml import YAML as _YAML
        from ruamel.yaml.constructor import (
            ConstructorError,
            RoundTripConstructor,
            SafeConstructor,
        )
        from ruamel.yaml.error import YAMLError
        from ruamel.yaml.representer import RoundTripRepresenter
        from ruamel.yaml.scalarfloat import ScalarFloat
    except ImportError:
        _YAML = None
        YAMLError = ValueError
        _HAVE_YAML = False
    else:
        _HAVE_YAML = True


type YamlScalar = str | int | float | bool | bytes | date | datetime | None
type YamlValue = (
    YamlScalar
    | dict[Hashable, YamlValue]
    | list[YamlValue]
    | tuple[YamlValue, ...]
    | MutableSet[Hashable]
)

__all__ = [
    "YAMLError",
    "YamlScalar",
    "YamlValue",
    "dump",
    "dump_all",
    "dumps",
    "load",
    "load_all",
    "loads",
    "parse",
]

if _HAVE_YAML:

    class _SafeConstructor(SafeConstructor):
        """Validate source keys before merging disables backend duplicate checks."""

        _merge_source = False

        @override
        def flatten_mapping(self, node: MappingNode) -> None:
            """Keep explicit duplicates distinct from permitted merge overrides."""
            if node.merge is None and (
                self._merge_source
                or any(
                    key.tag == "tag:yaml.org,2002:merge" for key, _value in node.value
                )
            ):
                keys: dict[Hashable, None] = {}
                for key_node, _value_node in node.value:
                    if key_node.tag == "tag:yaml.org,2002:merge":
                        continue
                    if key_node.tag == "tag:yaml.org,2002:value":
                        key_node.tag = "tag:yaml.org,2002:str"
                    key: object = self.construct_object(key_node, deep=True)
                    if isinstance(key, list):
                        key = tuple(key)
                    if not isinstance(key, Hashable):
                        context = "while constructing a mapping"
                        problem = "found unhashable key"
                        raise ConstructorError(
                            context,
                            node.start_mark,
                            problem,
                            key_node.start_mark,
                        )
                    if self.check_mapping_key(node, key_node, keys, key, None):
                        keys[key] = None
            # Aliased sources can already have been flattened. Their inherited
            # keys must not be checked again as explicit source declarations.
            # Ordinary mappings retain the backend's checks and diagnostics;
            # its recursive flatten calls identify inline merge sources too.
            prior_source = self._merge_source
            self._merge_source = True
            try:
                super().flatten_mapping(node)
            finally:
                self._merge_source = prior_source

    class _Constructor(RoundTripConstructor):
        """An editable constructor that refuses unresolved recursive aliases."""

        @override
        def construct_object(self, node: Node, deep: bool = False) -> object:
            """Refuse the backend's unresolved ``None`` placeholder before data loss."""
            if node not in self.constructed_objects and node in self.recursive_objects:
                raise ConstructorError(
                    None,
                    None,
                    "recursive alias cannot be preserved by the editable loader",
                    node.start_mark,
                )
            value: object = super().construct_object(node, deep=deep)
            return value

    # add_constructor copies inherited tables on the subclass before updating.
    _Constructor.add_constructor(None, SafeConstructor.construct_undefined)
    # Explicit standard !!str nodes remain strings in our public value domain.
    # The backend's default TaggedScalar wrapper is outside that domain;
    # construct_scalar retains quotes and anchors without admitting unknown tags.
    _Constructor.add_constructor(
        "tag:yaml.org,2002:str", RoundTripConstructor.construct_scalar
    )

    class _Representer(RoundTripRepresenter):
        """Keep parsed float spellings only while they still denote the value."""

        @override
        def represent_scalar_float(self, data: ScalarFloat) -> ScalarNode:
            node: ScalarNode = super().represent_scalar_float(data)
            try:
                exact = float(node.value) == data
            except ValueError:  # .inf and .nan spellings are exact
                exact = True
            if exact:
                return node
            # The backend truncates exponent mantissas instead of rounding them.
            text = self.represent_float(float(data)).value
            return self.represent_scalar(node.tag, text, anchor=node.anchor)

    _Representer.add_representer(ScalarFloat, _Representer.represent_scalar_float)


def _engine(*, editable: bool = False) -> _YAML:
    """A private parser/emitter session with fixed safe semantics."""
    if not _HAVE_YAML:
        missing("yaml", "yaml")()
    engine = _YAML(typ="rt" if editable else "safe", pure=True)
    engine.Constructor = _Constructor if editable else _SafeConstructor
    if editable:
        engine.Representer = _Representer
        engine.preserve_quotes = True
    engine.allow_duplicate_keys = False
    engine.default_flow_style = False
    return engine


def _is_value(value: object) -> TypeGuard[YamlValue]:
    """Narrow the untyped backend boundary without copying or expanding aliases."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if node is None or isinstance(node, str | int | float | bytes | date):
            continue
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(node, dict):
            if not all(isinstance(key, Hashable) for key in node):
                return False
            pending.extend(node.values())
        elif isinstance(node, list | tuple):
            pending.extend(node)
        elif isinstance(node, MutableSet):
            if not all(isinstance(item, Hashable) for item in node):
                return False
        else:
            return False
    return True


def _value(value: object) -> YamlValue:
    """The backend's native YAML value domain, checked at the boundary."""
    if _is_value(value):
        return value
    msg = f"unsupported YAML value of type {type(value).__name__}"
    raise TypeError(msg)


def loads(text: str | bytes) -> YamlValue:
    """One YAML document as native values; empty input produces ``None``."""
    return _value(_engine().load(text))


def load(fp: IO[str] | IO[bytes]) -> YamlValue:
    """One document from a caller-owned text or binary handle."""
    return _value(_engine().load(fp))


def parse(text: str | bytes) -> YamlValue:
    """An editable document retaining comments, quotes, anchors and key order."""
    return _value(_engine(editable=True).load(text))


def dumps(value: object) -> str:
    """A YAML document as Unicode text, retaining metadata from ``parse``."""
    stream = io.StringIO()
    dump(value, stream)
    return stream.getvalue()


def dump(value: object, fp: IO[str]) -> None:
    """Write a document to a caller-owned text handle; never constructs objects."""
    _engine(editable=True).dump(value, fp)


def load_all(source: str | bytes | IO[str] | IO[bytes]) -> Iterator[YamlValue]:
    """Lazily read a multi-document stream, leaving caller-owned handles open.

    Keep the handle open while iterating. The parser belongs to this iterator;
    closing the iterator releases its parser without closing the input handle.
    """
    with closing(_engine().load_all(source)) as documents:
        for value in documents:
            yield _value(value)


def dump_all(values: Iterable[object], fp: IO[str]) -> None:
    """Write documents lazily with ``---`` separators to a caller-owned handle."""
    _engine(editable=True).dump_all(values, fp)

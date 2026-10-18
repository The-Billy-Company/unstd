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
from typing import IO, TYPE_CHECKING, TypeGuard

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
    from ruamel.yaml.nodes import Node
else:
    try:
        from ruamel.yaml import YAML as _YAML
        from ruamel.yaml.constructor import (
            ConstructorError,
            RoundTripConstructor,
            SafeConstructor,
        )
        from ruamel.yaml.error import YAMLError
    except ImportError:
        _YAML = None
        YAMLError = ValueError


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

if _YAML is not None:

    class _Constructor(RoundTripConstructor):
        """An editable constructor that refuses unresolved recursive aliases."""

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


def _engine(*, editable: bool = False) -> _YAML:
    """A private parser/emitter session with fixed safe semantics."""
    if _YAML is None:
        missing("yaml", "yaml")()
    engine = _YAML(typ="rt" if editable else "safe", pure=True)
    if editable:
        engine.Constructor = _Constructor
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

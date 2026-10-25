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
from typing import IO, TYPE_CHECKING

from unstd._extra import missing
from unstd.yaml._internal.values import _is_value


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ruamel.yaml import YAML as _YAML
    from ruamel.yaml.error import YAMLError as YAMLError

    from unstd.yaml._internal.engine import create_engine as _create_engine

    _HAVE_YAML = True
else:
    try:
        from ruamel.yaml import YAML as _YAML
        from ruamel.yaml.error import YAMLError as YAMLError

        from unstd.yaml._internal.engine import create_engine as _create_engine
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


def _engine(*, editable: bool = False) -> _YAML:
    """A private parser/emitter session with fixed safe semantics."""
    if not _HAVE_YAML:
        missing("yaml", "yaml")()
    return _create_engine(_YAML, editable=editable)


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


# Keep public callable identity and pickles stable across lazy backend loading.
for _export in (dump, dump_all, dumps, load, load_all, loads, parse):
    _export.__module__ = "unstd.yaml"

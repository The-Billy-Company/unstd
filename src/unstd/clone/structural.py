"""Fast structural deep clone — a stdlib-``copy``-faithful stand-in for data.

``copy.deepcopy`` is a general object-graph walker: every node pays a memo lookup,
a dispatch-table probe, and (for anything but a builtin container) a
``__reduce_ex__`` round-trip. Data crossing a program's seams — ``dict``/``list``/
``tuple``/``set`` trees, dataclasses, pydantic models, ``msgspec.Struct`` — needs
none of that generality, so :func:`deep` walks it with exact-type dispatch instead:

- **Leaves never pay a call.** The container comprehensions test each child's
  exact type inline and return atoms (``str``/``int``/``float``/``bool``/``None``/
  ``bytes``/the immutable ``datetime`` values …) as-is, the same objects
  ``deepcopy`` itself returns for them.
- **Data classes are rebuilt the way ``deepcopy`` rebuilds them**, with the walker
  in place of the recursive ``deepcopy``: a plain dataclass is ``cls.__new__`` plus
  its walked ``__dict__`` (what ``object.__reduce_ex__(4)`` reconstructs to); a
  pydantic model mirrors ``BaseModel.__deepcopy__`` line for line; a
  ``(callable, args)`` reducer such as ``msgspec.Struct`` is re-called on walked
  args. Each shape is admitted per class, once, only when the class leaves the
  default copy protocol alone.
- **Everything else is copied by ``copy.deepcopy``, per node.** A custom
  ``__deepcopy__``, a dict subclass, an lxml element, a lock: the walker hands that
  one node to ``deepcopy`` and keeps walking its siblings.

Two semantic differences from ``deepcopy``, both deliberate and both what the
serialization round-trip this replaced already had: **shared references are not
preserved** (an object reached twice is cloned twice), and a **cycle** among
walked containers is detected by the recursion limit and handed to ``deepcopy``
whole. Use :func:`deepcopy` directly for live object graphs where aliasing is
meaning.

:func:`asdict` is the same walker in ``dataclasses.asdict``'s shape — dataclasses
become ``dict``, ``dict``/``list``/``tuple`` are rebuilt, and every other leaf is
deep-copied — without ``asdict``'s ``fields()`` call per instance and
``deepcopy`` per non-atomic leaf. Containers ``asdict`` special-cases but this
walker does not (namedtuples, ``dict``/``list``/``tuple`` subclasses) hand the
whole call back to the stdlib, so the result is always ``dataclasses.asdict``'s.

Pure stdlib: no extra is needed. The ``copy``/``deepcopy`` re-exports keep a file
migrating off ``import copy`` working after a one-line import swap.
"""

from __future__ import annotations

from collections.abc import Callable
import copy as _stdlib
import copyreg
import dataclasses
import datetime as _dt
import enum
import sys
import types
from typing import Any
import weakref


copy = _stdlib.copy
deepcopy = _stdlib.deepcopy

__all__ = ["asdict", "copy", "deep", "deepcopy"]

# Exact types ``copy.deepcopy`` returns as-is, plus the immutable datetime values
# it would only rebuild equal.
_ATOMS: frozenset[type] = frozenset(
    {
        type(None),
        int,
        float,
        bool,
        complex,
        bytes,
        str,
        range,
        type,
        property,
        types.CodeType,
        types.FunctionType,
        types.BuiltinFunctionType,
        types.EllipsisType,
        types.NotImplementedType,
        weakref.ref,
        _dt.date,
        _dt.time,
        _dt.datetime,
        _dt.timedelta,
        _dt.timezone,
    }
)

type _Clone = Callable[[Any], Any]
_NODES: dict[type, _Clone | None] = {}


def _key(k: Any) -> Any:
    return k if type(k) in _ATOMS else _walk(k)


def _walk(o: Any) -> Any:
    t = type(o)
    if t is dict:
        return {
            (k if type(k) is str else _key(k)): (v if type(v) in _ATOMS else _walk(v))
            for k, v in o.items()
        }
    if t is list:
        return [v if type(v) in _ATOMS else _walk(v) for v in o]
    if t is tuple:
        return tuple([v if type(v) in _ATOMS else _walk(v) for v in o])
    if t in _ATOMS:
        return o
    if t is set:
        return {_key(v) for v in o}
    if t is frozenset:
        return frozenset([_key(v) for v in o])
    try:
        node = _NODES[t]
    except KeyError:
        node = _NODES[t] = _admit(t)
    return node(o) if node else _stdlib.deepcopy(o)


# ── per-class admission: which default-protocol shapes the walker may rebuild ──


def _default_protocol(cls: type) -> bool:
    """Whether ``deepcopy`` would take the default ``object.__reduce_ex__`` route."""
    return (
        getattr(cls, "__deepcopy__", None) is None
        and cls not in copyreg.dispatch_table
        and getattr(cls, "__reduce_ex__", None) is object.__reduce_ex__
        and getattr(cls, "__reduce__", None) is object.__reduce__
    )


def _admit(cls: type) -> _Clone | None:
    if isinstance(cls, enum.EnumType):
        return (
            _same
            if getattr(cls, "__deepcopy__", None) is enum.Enum.__deepcopy__
            else None
        )
    if (pd := sys.modules.get("pydantic")) and issubclass(cls, pd.BaseModel):
        return (
            _model
            if getattr(cls, "__deepcopy__", None) is pd.BaseModel.__deepcopy__
            else None
        )
    if (ms := sys.modules.get("msgspec")) and issubclass(cls, ms.Struct):
        return _reduced if getattr(cls, "__deepcopy__", None) is None else None
    if (
        dataclasses.is_dataclass(cls)
        and _default_protocol(cls)
        and getattr(cls, "__getstate__", None) is object.__getstate__
        and not hasattr(cls, "__setstate__")
        and not hasattr(cls, "__slots__")
    ):
        return _dataclass
    return None


def _same(o: Any) -> Any:
    return o


def _dataclass(o: Any) -> Any:
    cls: Any = type(o)
    twin = cls.__new__(cls)
    if state := o.__dict__:
        twin.__dict__.update(_walk(state))
    return twin


def _reduced(o: Any) -> Any:
    rv = o.__reduce__()
    if len(rv) != 2:  # state/items need deepcopy's full _reconstruct
        return _stdlib.deepcopy(o)
    fn, args = rv
    return fn(*[a if type(a) in _ATOMS else _walk(a) for a in args])


def _model(o: Any) -> Any:
    """``pydantic.BaseModel.__deepcopy__``, with the walker in place of ``deepcopy``."""
    from pydantic_core import PydanticUndefined

    cls: Any = type(o)
    setattr_ = object.__setattr__
    twin = cls.__new__(cls)
    setattr_(twin, "__dict__", _walk(o.__dict__))
    extra = o.__pydantic_extra__
    setattr_(twin, "__pydantic_extra__", None if extra is None else _walk(extra))
    setattr_(twin, "__pydantic_fields_set__", set(o.__pydantic_fields_set__))
    private = getattr(o, "__pydantic_private__", None)
    setattr_(
        twin,
        "__pydantic_private__",
        None
        if private is None
        else _walk({k: v for k, v in private.items() if v is not PydanticUndefined}),
    )
    return twin


# ── the surface ────────────────────────────────────────────────────────────────


def deep[T](obj: T) -> T:
    """Deep-clone ``obj`` — an exact-type structural walk, ``copy.deepcopy`` per foreign node.

    Plain data (``dict``/``list``/``tuple``/``set``/``frozenset``, dataclasses,
    pydantic models, ``msgspec.Struct``) is rebuilt without ``deepcopy``'s memo and
    reducer machinery; any node the walker does not own — a custom
    ``__deepcopy__``, a container subclass, a live resource — is copied by
    ``copy.deepcopy`` itself. The result is value-equal and fully independent of
    the source. Unlike ``deepcopy``, an object reached twice is cloned twice; a
    cyclic graph falls back to ``deepcopy`` whole.
    """
    if type(obj) in _ATOMS:
        return obj
    try:
        return _walk(obj)  # type: ignore[no-any-return]
    except RecursionError:
        return _stdlib.deepcopy(obj)


_FIELDS: dict[type, tuple[str, ...]] = {}


class _DeferError(Exception):
    """A container ``dataclasses.asdict`` treats specially and this walker does not."""


def _plain(o: Any) -> Any:
    t = type(o)
    if t in _ATOMS:
        return o
    if t is dict:
        return {
            (k if type(k) is str else _plain(k)): (
                v if type(v) in _ATOMS else _plain(v)
            )
            for k, v in o.items()
        }
    if t is list:
        return [v if type(v) in _ATOMS else _plain(v) for v in o]
    if t is tuple:
        return tuple([v if type(v) in _ATOMS else _plain(v) for v in o])
    if dataclasses.is_dataclass(t):
        try:
            names = _FIELDS[t]
        except KeyError:
            names = _FIELDS[t] = tuple(f.name for f in dataclasses.fields(t))
        return {
            n: (v if type(v := getattr(o, n)) in _ATOMS else _plain(v)) for n in names
        }
    if isinstance(o, (dict, list, tuple)):
        raise _DeferError
    return _walk(o)


def asdict(
    obj: Any, *, dict_factory: Callable[[list[tuple[str, Any]]], Any] = dict
) -> Any:
    """``dataclasses.asdict``, walked with exact-type dispatch — same result, same errors.

    A custom ``dict_factory``, a non-dataclass argument, or a tree holding a
    container ``asdict`` special-cases (namedtuple, ``dict``/``list``/``tuple``
    subclass) is handed to the stdlib, so the answer is always the stdlib's.
    """
    if (
        dict_factory is not dict
        or not dataclasses.is_dataclass(obj)
        or isinstance(obj, type)
    ):
        return dataclasses.asdict(obj, dict_factory=dict_factory)
    try:
        return _plain(obj)
    except _DeferError:
        return dataclasses.asdict(obj)

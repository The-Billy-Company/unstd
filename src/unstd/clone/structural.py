"""Fast structural deep clone — a stdlib-``copy``-faithful stand-in for data.

``copy.deepcopy`` is a general object-graph walker: every node pays a memo lookup,
a dispatch-table probe, and (for anything but a builtin container) a
``__reduce_ex__`` round-trip. Data crossing a program's seams — ``dict``/``list``/
``tuple``/``set`` trees, dataclasses, pydantic models, ``msgspec.Struct`` — can
use exact-type dispatch and a shared memo instead:

- **Scalar leaves never pay a call.** The container walks test each child's
  exact type inline and return atoms (``str``/``int``/``float``/``bool``/``None``/
  ``bytes`` …) as-is. Immutable temporal values enter the memo to keep references
  consistent across foreign copy hooks. Datetimes and times stay shared when
  their timezone is absent or an exact immutable stdlib type; custom timezones
  retain ``deepcopy``'s copy protocol.
- **Data classes are rebuilt the way ``deepcopy`` rebuilds them**, with the walker
  in place of the recursive ``deepcopy``: a plain dataclass is ``cls.__new__`` plus
  its walked ``__dict__`` (what ``object.__reduce_ex__(4)`` reconstructs to); a
  pydantic model copies ``BaseModel.__deepcopy__``'s fields and state; a
  ``(callable, args)`` reducer such as ``msgspec.Struct`` is re-called on walked
  args. Each shape is admitted per class, once, only when the class leaves the
  default copy protocol alone.
- **Everything else is copied by ``copy.deepcopy``, per node.** A custom
  ``__deepcopy__``, a dict subclass, an lxml element, a lock: the walker hands that
  one node to ``deepcopy`` and keeps walking its siblings.

A per-call memo preserves shared references across structural nodes and foreign
copy hooks: an object reached twice reuses the same clone. Immutable leaves stay
shared with the source. The native walk detects builtin cycles and hands the
whole graph to ``deepcopy``; the Python walker also
memoizes mutable outputs before visiting their children.

:func:`asdict` is the same walker in ``dataclasses.asdict``'s shape — dataclasses
become ``dict``, ``dict``/``list``/``tuple`` are rebuilt, and every other leaf is
deep-copied — without ``asdict``'s ``fields()`` call per instance and
``deepcopy`` per non-atomic leaf. Containers ``asdict`` special-cases but this
walker does not (namedtuples, ``dict``/``list``/``tuple`` subclasses) hand the
whole call back to the stdlib, so the result is always ``dataclasses.asdict``'s.

No extra is needed. An optional bundled C walker accelerates bounded exact
dict/list/tuple graphs, including dataclass and model state. Foreign
nodes retain this Python implementation and their existing copy protocols. The
``copy``/``deepcopy`` re-exports keep a file
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
from zoneinfo import ZoneInfo


type _Clone = Callable[[Any, dict[int, Any]], Any]
type _RecordAdmission = Callable[[type], tuple[bool, _Clone]]


_native_clone: Callable[[Any], Any] | None
try:
    from unstd.clone._clone_native import clone_checked as _native_clone
except ImportError:
    _native_clone = None

_native_graph: Callable[..., Any] | None
try:
    from unstd.clone._clone_native import clone_graph as _native_graph
except ImportError:
    _native_graph = None

_native_record_graph: (
    Callable[[Any, type, Callable[[Any, dict[int, Any]], Any], dict[int, Any]], Any]
    | None
)
try:
    from unstd.clone._clone_native import clone_record_graph as _native_record_graph
except ImportError:
    _native_record_graph = None

_native_walk_graph: (
    Callable[[Any, _Clone, _RecordAdmission, dict[int, Any]], Any] | None
)
try:
    from unstd.clone._clone_native import (
        clone_walk_canonical_graph as _native_walk_graph,
    )
except ImportError:
    try:
        from unstd.clone._clone_native import clone_walk_graph as _legacy_walk_graph
    except ImportError:
        _native_walk_graph = None
    else:
        _native_walk_graph = _legacy_walk_graph

_native_records: Callable[[Any, type, Callable[[Any], Any]], Any] | None
try:
    from unstd.clone._clone_native import clone_records as _native_records
except ImportError:
    _native_records = None


_native_dataclass: Callable[..., Any] | None
try:
    from unstd.clone._clone_native import clone_dataclass as _native_dataclass
except ImportError:
    _native_dataclass = None

# Extensions with the general graph walker also accept the selected-handler
# flag. Keep this capability when tests or benchmarks disable that walker.
_NATIVE_SELECTED_MEMO = _native_walk_graph is not None


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
        _dt.timedelta,
        _dt.timezone,
    }
)
# These immutable temporal values are rebuilt by stdlib deepcopy. They must
# enter our memo so foreign copy hooks and structural siblings agree on identity.
_GRAPH_ATOMS = _ATOMS - {_dt.date, _dt.timedelta, _dt.timezone}
# A datetime/time is immutable, but a caller's tzinfo can own mutable state.
# Admit only exact immutable timezone types; subclasses keep their copy hooks.
_TIMEZONES = frozenset({type(None), _dt.timezone, ZoneInfo})

# Admission metadata must not keep dynamically generated classes alive.
_NODES: weakref.WeakKeyDictionary[type, _Clone | None] = weakref.WeakKeyDictionary()
_RECORDS: weakref.WeakSet[type] = weakref.WeakSet()


def _walk(o: Any, memo: dict[int, Any] | None = None) -> Any:
    t = type(o)
    if t in _GRAPH_ATOMS:
        return o
    if memo is None:
        memo = {}
    identity = id(o)
    if identity in memo:
        return memo[identity]
    # Copy hooks may remove a source node while we walk. Pin it until this
    # operation ends so its id cannot be recycled into a different memo entry.
    memo_identity = id(memo)
    try:
        keep_alive = memo[memo_identity]
    except KeyError:
        keep_alive = memo[memo_identity] = []
    keep_alive.append(o)
    if t is _dt.timezone:
        offset = o.utcoffset(None)
        copied_offset = _walk(offset, memo)
        twin_zone = (
            o
            if copied_offset is offset
            else _dt.timezone(copied_offset, o.tzname(None))
        )
        memo[identity] = twin_zone
        return twin_zone
    if t in _ATOMS or t is ZoneInfo:
        memo[identity] = o
        return o
    if t is dict:
        twin: Any = {}
        memo[identity] = twin
        for k, v in o.items():
            key = (
                k
                if type(k) is str
                else k
                if type(k) in _GRAPH_ATOMS
                else _walk(k, memo)
            )
            twin[key] = v if type(v) in _GRAPH_ATOMS else _walk(v, memo)
        memo[identity] = twin
        return twin
    if t is list:
        twin = []
        memo[identity] = twin
        # Append as we go: a foreign copy hook can refer back to this list.
        for v in o:
            twin.append(v if type(v) in _GRAPH_ATOMS else _walk(v, memo))
        memo[identity] = twin
        return twin
    if t is tuple:
        values = [v if type(v) in _GRAPH_ATOMS else _walk(v, memo) for v in o]
        # A child can recursively finish this tuple through a mutable backref.
        if identity in memo:
            return memo[identity]
        twin = tuple(values)
    elif t is _dt.datetime or t is _dt.time:
        zone = o.tzinfo
        if type(zone) in _TIMEZONES:
            copied_zone = _walk(zone, memo)
            twin = o if copied_zone is zone else o.replace(tzinfo=copied_zone)
        else:
            twin = _stdlib.deepcopy(o, memo)
    elif t is set:
        twin = set()
        memo[identity] = twin
        for v in o:
            twin.add(v if type(v) in _GRAPH_ATOMS else _walk(v, memo))
        memo[identity] = twin
        return twin
    elif t is frozenset:
        values = [v if type(v) in _GRAPH_ATOMS else _walk(v, memo) for v in o]
        if identity in memo:
            return memo[identity]
        twin = frozenset(values)
    else:
        try:
            node = _NODES[t]
        except KeyError:
            node = _NODES[t] = _admit(t)
        twin = node(o, memo) if node else _foreign(o, memo)
    memo[identity] = twin
    return twin


_TEMPORAL_WALK = _walk
_TEMPORAL_WALK_CODE = _walk.__code__


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
        return (
            _reduced
            if getattr(cls, "__deepcopy__", None) is None
            and cls not in copyreg.dispatch_table
            and getattr(cls, "__reduce_ex__", None) is object.__reduce_ex__
            and getattr(cls, "__reduce__", None) is ms.Struct.__reduce__
            else None
        )
    if (
        dataclasses.is_dataclass(cls)
        and _default_protocol(cls)
        and getattr(cls, "__getstate__", None) is object.__getstate__
        and not hasattr(cls, "__setstate__")
        and not hasattr(cls, "__slots__")
        and not hasattr(cls, "__getnewargs__")
        and not hasattr(cls, "__getnewargs_ex__")
    ):
        # Read the first MRO entries statically. A custom descriptor for
        # __new__ or __dict__ must not run merely to choose an accelerator.
        # Native records use ordinary allocation/attribute reading only;
        # custom-new and descriptor-bearing records retain _dataclass. Setters
        # are bypassed by __dict__.update, including frozen dataclass setters.
        if (
            _native_walk_graph is not None
            and type(cls) is type
            and cls.__base__ is object
            and "__del__" not in vars(cls)
        ):
            attributes: dict[str, Any] = {}
            for base in cls.__mro__:
                namespace = vars(base)
                for name in (
                    "__new__",
                    "__getattribute__",
                    "__dict__",
                ):
                    if name not in attributes and name in namespace:
                        attributes[name] = namespace[name]
            descriptor = attributes.get("__dict__")
            if (
                attributes.get("__new__") is object.__new__
                and attributes.get("__getattribute__") is object.__getattribute__
                and type(descriptor) is types.GetSetDescriptorType
                and descriptor.__objclass__ is cls
            ):
                _RECORDS.add(cls)
        return _dataclass
    return None


def _same(o: Any, memo: dict[int, Any]) -> Any:
    return o


def _foreign(o: Any, memo: dict[int, Any]) -> Any:
    """Copy an opaque node through the current stdlib hook with its graph memo."""
    return _stdlib.deepcopy(o, memo)


def _walk_data(o: Any, memo: dict[int, Any]) -> Any:
    """Try native cloning at a state boundary with the enclosing graph's memo."""
    if id(o) in memo:
        return memo[id(o)]
    if _native_clone is not None:
        if _native_walk_graph is not None:
            result = _native_walk_graph(o, _walk, _record_admit, memo)
            if result is not NotImplemented:
                return result
        elif _native_graph is not None and not _NATIVE_SELECTED_MEMO:
            result = _native_graph(o, memo)
            if result is not NotImplemented:
                return result
    return _walk(o, memo)


def _dataclass(o: Any, memo: dict[int, Any]) -> Any:
    if _native_clone is not None and _native_dataclass is not None:
        twin = (
            _native_dataclass(o, memo, _walk, False)
            if _NATIVE_SELECTED_MEMO
            else _native_dataclass(o, memo, _walk)
        )
        if twin is not NotImplemented:
            return twin
    cls: Any = type(o)
    twin = cls.__new__(cls)
    memo[id(o)] = twin
    if state := o.__dict__:
        twin.__dict__.update(_walk_data(state, memo))
    return twin


def _reduced(o: Any, memo: dict[int, Any]) -> Any:
    rv = o.__reduce__()
    if len(rv) != 2:  # state/items need deepcopy's full _reconstruct
        return _stdlib.deepcopy(o, memo)
    fn, args = rv
    values = [a if type(a) in _GRAPH_ATOMS else _walk(a, memo) for a in args]
    if id(o) in memo:
        return memo[id(o)]
    return fn(*values)


_PYDANTIC_UNDEFINED: Any = None


def _model(o: Any, memo: dict[int, Any]) -> Any:
    """Reconstruct pydantic instance state with the enclosing graph's memo."""
    global _PYDANTIC_UNDEFINED
    if _PYDANTIC_UNDEFINED is None:
        # Keep the optional import lazy, but out of every model in a large tree.
        from pydantic_core import PydanticUndefined

        _PYDANTIC_UNDEFINED = PydanticUndefined

    cls: Any = type(o)
    setattr_ = object.__setattr__
    twin = cls.__new__(cls)
    memo[id(o)] = twin
    setattr_(twin, "__dict__", _walk_data(o.__dict__, memo))
    extra = o.__pydantic_extra__
    setattr_(
        twin, "__pydantic_extra__", None if extra is None else _walk_data(extra, memo)
    )
    setattr_(twin, "__pydantic_fields_set__", _walk(o.__pydantic_fields_set__, memo))
    private = getattr(o, "__pydantic_private__", None)
    if private is None:
        copied_private = None
    else:
        # Snapshot before a value's copy hook can mutate the source mapping.
        # A filtered private view is a separate protocol projection: putting it
        # under id(private) would erase Undefined entries from ordinary aliases.
        if type(private) is dict:
            snapshot = private.copy()
            projected = any(v is _PYDANTIC_UNDEFINED for v in snapshot.values())
            if projected:
                snapshot = {
                    k: v for k, v in snapshot.items() if v is not _PYDANTIC_UNDEFINED
                }
        else:
            # Pydantic converts a mapping subclass to a plain private-state
            # dictionary. That derived view cannot replace the mapping's own
            # graph identity or change an ordinary sibling's cloned type.
            snapshot = {}
            projected = True
            for key, value in private.items():
                if value is not _PYDANTIC_UNDEFINED:
                    snapshot[key] = value
        if projected:
            copied_private = _walk_data(snapshot, memo)
        elif id(private) in memo:
            copied_private = memo[id(private)]
        else:
            copied_private = {}
            memo[id(private)] = copied_private
            memo_identity = id(memo)
            try:
                keep_alive = memo[memo_identity]
            except KeyError:
                keep_alive = memo[memo_identity] = []
            keep_alive.append(private)
            copied_private.update(
                {
                    (
                        k
                        if type(k) is str
                        else k
                        if type(k) in _GRAPH_ATOMS
                        else _walk(k, memo)
                    ): (v if type(v) in _GRAPH_ATOMS else _walk(v, memo))
                    for k, v in snapshot.items()
                }
            )
            memo[id(private)] = copied_private
    setattr_(twin, "__pydantic_private__", copied_private)
    return twin


# ── the surface ────────────────────────────────────────────────────────────────


def _record_admit(cls: type) -> tuple[bool, _Clone]:
    """Select the same handler as Python at an already pinned source node."""
    try:
        node = _NODES[cls]
    except KeyError:
        node = _NODES[cls] = _admit(cls)
    return node is _dataclass and cls in _RECORDS, node or _foreign


def deep[T](obj: T) -> T:
    """Deep-clone data, preserving shared references within each call.

    Exact builtin containers, dataclasses, pydantic models and msgspec structs
    use a structural walk. Foreign nodes retain ``copy.deepcopy``'s protocol
    and share the same memo. Owned mutable data is copied independently;
    repeated references reuse their clone. Immutable atoms remain shared, and
    foreign copy hooks control their own results.
    """
    kind = type(obj)
    if kind in _GRAPH_ATOMS:
        return obj
    try:
        if (
            _native_clone is not None
            and _native_graph is not None
            and (kind is dict or kind is list or kind is tuple)
        ):
            cloned = _native_graph(obj)
            if cloned is not NotImplemented:
                return cloned  # type: ignore[no-any-return]
        memo: dict[int, Any] = {}
        if _native_clone is not None and _native_walk_graph is not None:
            cloned = _native_walk_graph(obj, _walk, _record_admit, memo)
            if cloned is not NotImplemented:
                return cloned  # type: ignore[no-any-return]
        return _walk(obj, memo)  # type: ignore[no-any-return]
    except RecursionError:
        return _stdlib.deepcopy(obj)


_FIELDS: weakref.WeakKeyDictionary[type, tuple[str, ...]] = weakref.WeakKeyDictionary()


class _DeferError(Exception):
    """A container ``dataclasses.asdict`` treats specially and this walker does not."""


def _plain_field(o: Any) -> Any:
    """Try native data conversion once at a dataclass field boundary."""
    t = type(o)
    if _native_clone is not None and (t is dict or t is list or t is tuple):
        result = _native_clone(o)
        if result is not NotImplemented:
            return result
    return _plain(o)


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
    # ``t`` is already a type; avoid is_dataclass's instance/type dispatch for
    # every record in the tree while retaining its inherited-field semantics.
    if hasattr(t, "__dataclass_fields__"):
        try:
            names = _FIELDS[t]
        except KeyError:
            names = _FIELDS[t] = tuple(f.name for f in dataclasses.fields(t))
        return {
            n: (v if type(v := getattr(o, n)) in _ATOMS else _plain_field(v))
            for n in names
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
        # Inline is_dataclass's predicate without changing class/metaclass
        # attribute access or the stdlib's non-instance error behavior.
        or not hasattr(
            obj if isinstance(obj, type) else type(obj), "__dataclass_fields__"
        )
        or isinstance(obj, type)
    ):
        return dataclasses.asdict(obj, dict_factory=dict_factory)
    try:
        return _plain(obj)
    except _DeferError:
        return dataclasses.asdict(obj)

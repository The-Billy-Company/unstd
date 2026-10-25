"""Generated mixed typed trees against ``copy.deepcopy`` and ``dataclasses.asdict``.

Trees mix every shape the walker owns (builtin containers, dataclasses, enums,
immutable temporal values) with shapes it must hand to ``deepcopy`` per node
(namedtuples, ``Decimal``, ``bytearray``). The stdlib result is the authority for
value and exact type at every position; isolation is checked by object identity
of every mutable node and by mutating each side after cloning.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import datetime as dt
import enum
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd import clone
from unstd.clone import structural


class _Color(enum.Enum):
    RED = "red"
    BLUE = (1, 2)


class _Mode(enum.StrEnum):
    FAST = "fast"


class _Level(enum.IntEnum):
    HIGH = 3


class _Perm(enum.Flag):
    READ = 1
    WRITE = 2


@dataclasses.dataclass
class _Record:
    items: list[Any]
    meta: dict[str, Any]
    tag: Any = None


@dataclasses.dataclass
class _Child(_Record):
    bag: set[Any] = dataclasses.field(default_factory=set)


@dataclasses.dataclass(frozen=True)
class _Key:
    left: Any
    right: Any


_Pair = collections.namedtuple("_Pair", "left right")

_ATOM = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False),
    st.complex_numbers(allow_nan=False, allow_infinity=False, max_magnitude=1e6),
    st.text(max_size=8),
    st.binary(max_size=8),
    st.decimals(allow_nan=False, allow_infinity=False, places=3),
    st.dates(),
    st.timedeltas(),
    st.datetimes(),
    st.datetimes(timezones=st.just(dt.UTC)),
    st.times(timezones=st.sampled_from([None, dt.UTC])),
    st.sampled_from([*_Color, *_Mode, *_Level, _Perm.READ | _Perm.WRITE]),
)
_HASHABLE = st.recursive(
    _ATOM,
    lambda inner: st.one_of(
        st.tuples(inner, inner),
        st.lists(inner, max_size=4).map(frozenset),
        st.builds(_Pair, inner, inner),
        st.builds(_Key, inner, inner),
    ),
    max_leaves=8,
)


def _keyed(values: st.SearchStrategy[Any], max_size: int) -> st.SearchStrategy[Any]:
    # Unique-element draws repr their element strategy, which is huge here.
    return st.lists(st.tuples(_HASHABLE, values), max_size=max_size).map(dict)


_TREE = st.recursive(
    st.one_of(_HASHABLE, st.binary(max_size=8).map(bytearray)),
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        _keyed(inner, max_size=4),
        st.tuples(inner, inner),
        st.lists(_HASHABLE, max_size=4).map(set),
        st.builds(_Pair, inner, inner),
        st.builds(
            _Record,
            st.lists(inner, max_size=3),
            st.dictionaries(st.text(max_size=4), inner, max_size=3),
            inner,
        ),
        st.builds(
            _Child,
            st.lists(inner, max_size=2),
            st.dictionaries(st.text(max_size=4), inner, max_size=2),
            inner,
            st.lists(_HASHABLE, max_size=3).map(set),
        ),
    ),
    max_leaves=24,
)
_MUTABLE = (list, dict, set, bytearray, _Record, _Child)


def _match(element: object, candidates: Any) -> object:
    # Equal set members cannot coexist, so equality identifies the counterpart.
    (found,) = [c for c in candidates if c == element]
    return found


def _assert_twin(actual: object, expected: object) -> None:
    """Same exact type and value at every position, in the same order."""
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert len(actual) == len(expected)
        for (k, v), (ek, ev) in zip(actual.items(), expected.items(), strict=True):
            _assert_twin(k, ek)
            _assert_twin(v, ev)
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            _assert_twin(a, e)
    elif isinstance(expected, (set, frozenset)):
        assert len(actual) == len(expected)
        for a in actual:
            _assert_twin(a, _match(a, expected))
    elif dataclasses.is_dataclass(expected):
        _assert_twin(vars(actual), vars(expected))
    else:
        assert actual == expected or (actual != actual and expected != expected)
        assert repr(actual) == repr(expected)


def _nodes(value: object) -> list[object]:
    """Every node reachable through containers and dataclass fields."""
    found, stack = [], [value]
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, (list, tuple, set, frozenset)):
            stack.extend(node)
        elif dataclasses.is_dataclass(node):
            stack.extend(vars(node).values())
    return found


def _mutable_ids(value: object) -> set[int]:
    return {id(n) for n in _nodes(value) if isinstance(n, _MUTABLE)}


def _mutate_all(value: object) -> None:
    for node in _nodes(value):
        if isinstance(node, list):
            node.append("mutated")
        elif isinstance(node, dict):
            node["mutated"] = "mutated"
        elif isinstance(node, set):
            node.add("mutated")
        elif isinstance(node, bytearray):
            node.extend(b"!")
        elif isinstance(node, _Record):
            node.tag = "mutated"


@given(_TREE)
def test_deep_matches_deepcopy_type_and_value_everywhere(source: object) -> None:
    expected = copy.deepcopy(source)
    actual = clone.deep(source)
    _assert_twin(actual, expected)
    _assert_twin(actual, source)


@given(_TREE)
def test_mutating_either_side_never_reaches_the_other(generated: object) -> None:
    source = copy.deepcopy(generated)  # Hypothesis reprs mutated arguments
    snapshot = copy.deepcopy(source)
    cloned = clone.deep(source)
    assert not _mutable_ids(cloned) & _mutable_ids(source)

    _mutate_all(cloned)
    assert source == snapshot
    _assert_twin(source, snapshot)

    cloned = clone.deep(source)
    _mutate_all(source)
    assert cloned == snapshot
    _assert_twin(cloned, snapshot)


@given(_TREE)
def test_shared_alias_identity_is_preserved_like_deepcopy(shared: object) -> None:
    source = [shared, {"again": shared}]
    expected = copy.deepcopy(source)
    actual = clone.deep(source)

    _assert_twin(actual, expected)
    assert expected[0] is expected[1]["again"]
    assert actual[0] is actual[1]["again"]
    assert not _mutable_ids(actual) & _mutable_ids(source)


@pytest.fixture(scope="module")
def native_clone() -> Any:
    return pytest.importorskip("unstd.clone._clone_native").clone


# Exact builtins with string keys: the only trees the C walker accepts.
_BUILTIN = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=6),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=4), inner, max_size=3)
        | st.tuples(inner, inner)
    ),
    max_leaves=12,
)


@st.composite
def _aliased_builtin(draw: st.DrawFn) -> object:
    """A bounded acyclic graph with aliases repeated through several containers."""
    pool: list[Any] = [draw(st.lists(_BUILTIN, max_size=3))]
    for _ in range(draw(st.integers(min_value=1, max_value=5))):
        shared = pool[draw(st.integers(min_value=0, max_value=len(pool) - 1))]
        other = pool[draw(st.integers(min_value=0, max_value=len(pool) - 1))]
        shape = draw(st.sampled_from(["list", "tuple", "dict"]))
        if shape == "dict":
            pool.append({"first": shared, "again": shared, "other": other})
        elif shape == "tuple":
            pool.append((shared, shared, other))
        else:
            pool.append([shared, shared, other])
    return [{"branch": pool[-1]}, {"branch": pool[-1]}]


@given(source=_aliased_builtin())
@pytest.mark.parametrize("backend", ["native", "native_checked"])
def test_native_tree_copy_expands_every_alias_in_a_builtin_graph(
    backend: str, source: Any
) -> None:
    """An optimization may reuse immutable leaves, never a copied mutable branch."""
    native = pytest.importorskip("unstd.clone._clone_native")
    copier = native.clone_checked if backend == "native_checked" else native.clone
    expected = copy.deepcopy(source)
    assert expected[0]["branch"] is expected[1]["branch"]
    snapshot = copy.deepcopy(source)

    actual = copier(source)
    assert actual is not NotImplemented
    _assert_twin(actual, expected)
    mutable_nodes = [node for node in _nodes(actual) if type(node) in (list, dict)]
    mutable_ids = [id(node) for node in mutable_nodes]
    assert len(mutable_ids) == len(set(mutable_ids))
    assert not set(mutable_ids) & _mutable_ids(source)
    _mutate_all(actual)
    _assert_twin(source, snapshot)


def _assert_builtin_aliases(
    actual: Any, source: Any, twins: dict[int, Any] | None = None
) -> None:
    if type(source) not in (dict, list, tuple):
        return
    if twins is None:
        twins = {}
    if id(source) in twins:
        assert actual is twins[id(source)]
        return
    twins[id(source)] = actual
    if type(source) is dict:
        for key, value in source.items():
            _assert_builtin_aliases(actual[key], value, twins)
    else:
        for value, original in zip(actual, source, strict=True):
            _assert_builtin_aliases(value, original, twins)


@given(source=_aliased_builtin())
@pytest.mark.parametrize("backend", ["python", "native"])
def test_deep_preserves_every_alias_in_a_builtin_graph(
    backend: str, source: Any
) -> None:
    expected = copy.deepcopy(source)
    snapshot = copy.deepcopy(source)
    with pytest.MonkeyPatch.context() as monkeypatch:
        if backend == "python":
            monkeypatch.setattr(structural, "_native_clone", None)
            monkeypatch.setattr(structural, "_native_records", None)
            if hasattr(structural, "_native_graph"):
                monkeypatch.setattr(structural, "_native_graph", None)
        else:
            pytest.importorskip("unstd.clone._clone_native")
        actual = clone.deep(source)

    _assert_twin(actual, expected)
    _assert_builtin_aliases(actual, source)
    assert not _mutable_ids(actual) & _mutable_ids(source)
    _mutate_all(actual)
    _assert_twin(source, snapshot)


@given(builtin=_BUILTIN, source=_TREE)
def test_native_and_python_paths_agree_on_the_same_inputs(
    native_clone: Any, builtin: object, source: object
) -> None:
    accepted = ([builtin], {"root": builtin}, (builtin, [builtin]))
    assert all(native_clone(root) is not NotImplemented for root in accepted)
    roots = (*accepted, [source], {"root": source}, (source, builtin))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(structural, "_native_clone", None)
        python = [clone.deep(root) for root in roots]
        monkeypatch.setattr(structural, "_native_clone", native_clone)
        accelerated = [clone.deep(root) for root in roots]
    for p, a, root in zip(python, accelerated, roots, strict=True):
        _assert_twin(p, a)
        assert not _mutable_ids(a) & _mutable_ids(root)
        assert not _mutable_ids(p) & _mutable_ids(root)
        _assert_twin(a, copy.deepcopy(root))


@dataclasses.dataclass
class _Box:
    value: Any
    rest: list[Any]
    index: dict[Any, Any]


@given(_TREE, st.lists(_TREE, max_size=3), _keyed(_TREE, max_size=3))
def test_asdict_matches_dataclasses_asdict_types_and_isolation(
    value: object, rest: list[object], index: dict[object, object]
) -> None:
    box = copy.deepcopy(_Box(value, [_Box(r, [], {}) for r in rest], index))
    snapshot = copy.deepcopy(box)
    try:
        expected = dataclasses.asdict(box)
    except TypeError as error:
        # A dataclass key converts to an unhashable dict; the error is contractual.
        with pytest.raises(type(error)):
            clone.asdict(box)
        assert box == snapshot
        return
    actual = clone.asdict(box)

    _assert_twin(actual, expected)
    assert not _mutable_ids(actual) & _mutable_ids(box)
    _mutate_all(actual)
    assert box == snapshot

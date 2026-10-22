"""Copy-hook failures propagate unchanged and leave no observable clone state.

Each failing node sits at one structural position the walker owns. The stdlib
``copy.deepcopy`` on the same tree is the control: the same exception object
escapes, the source graph keeps its identities and values, no half-built twin
outlives the handled failure, and the next clone of the same classes succeeds.
"""

from __future__ import annotations

import collections
import copy
import copyreg
import dataclasses
import gc
import threading
from typing import Any, Self
import weakref

import pytest

from tests.support.environment.backends import require_backend
from unstd import clone
from unstd.clone import structural


_FAILURE = LookupError("copy hook failed")
_Pair = collections.namedtuple("_Pair", "left right")


class _RaisingDeepcopy:
    def __init__(self) -> None:
        self.armed = True

    def __eq__(self, other: object) -> bool:
        return type(other) is _RaisingDeepcopy

    def __hash__(self) -> int:
        return 7

    def __deepcopy__(self, memo: dict[int, object]) -> _RaisingDeepcopy:
        if self.armed:
            raise _FAILURE
        return _RaisingDeepcopy()


class _RaisingReduce(_RaisingDeepcopy):
    __deepcopy__ = None  # type: ignore[assignment]

    def __reduce_ex__(self, protocol: object) -> Any:
        if self.armed:
            raise _FAILURE
        return _RaisingReduce, ()


class _Registered(_RaisingDeepcopy):
    __deepcopy__ = None  # type: ignore[assignment]


def _registered_reducer(obj: _Registered) -> Any:
    if obj.armed:
        raise _FAILURE
    return _Registered, ()


@dataclasses.dataclass(eq=False)
class _Tracked:
    """A walker-admitted dataclass whose every allocation is observable."""

    payload: Any
    born: Any = dataclasses.field(default=None, compare=False)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        twin = super().__new__(cls)
        _ALLOCATED.append(weakref.ref(twin))
        return twin


_ALLOCATED: list[weakref.ref[_Tracked]] = []


def _positions(bad: object) -> dict[str, object]:
    return {
        "root": bad,
        "dict-value": {"ok": [1], "bad": bad},
        "dict-key": {"ok": [1], bad: [2]},
        "list": [[1], bad],
        "tuple": ([1], bad),
        "set": {1, bad},
        "frozenset": frozenset({1, bad}),
        "dataclass-field": _Tracked([[1], bad]),
        "namedtuple": _Pair([1], bad),
        "nested": [{"deep": (_Tracked({"x": bad}),)}],
    }


_HOOKS = {
    "deepcopy": _RaisingDeepcopy,
    "reduce_ex": _RaisingReduce,
    "copyreg": _Registered,
}


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(copyreg.dispatch_table, _Registered, _registered_reducer)


@pytest.fixture(params=["python", "native"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "native":
        native = pytest.importorskip("unstd.clone._clone_native")
        monkeypatch.setattr(structural, "_native_clone", native.clone)
    else:
        monkeypatch.setattr(structural, "_native_clone", None)
    return request.param


@pytest.mark.parametrize("hook", list(_HOOKS))
@pytest.mark.parametrize("position", list(_positions(None)))
def test_hook_error_escapes_unchanged_and_the_source_survives(
    hook: str, position: str, backend: str
) -> None:
    bad = _HOOKS[hook]()
    source = _positions(bad)[position]
    snapshot = _comparable(source)
    nodes_before = [id(n) for n in _reachable(source)]

    with pytest.raises(LookupError) as expected:
        copy.deepcopy(source)
    with pytest.raises(LookupError) as observed:
        clone.deep(source)

    assert expected.value is _FAILURE
    assert observed.value is _FAILURE
    assert observed.value.__cause__ is None
    assert [id(n) for n in _reachable(source)] == nodes_before
    assert _comparable(source) == snapshot

    bad.armed = False
    recovered = clone.deep(source)
    assert _comparable(recovered) == _comparable(copy.deepcopy(source))
    assert not {id(n) for n in _reachable(recovered) if _is_mutable(n)} & {
        id(n) for n in _reachable(source) if _is_mutable(n)
    }


def _reachable(value: object) -> list[object]:
    found, stack = [], [value]
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, dict):
            stack.extend([*node.keys(), *node.values()])
        elif isinstance(node, (list, tuple, set, frozenset)):
            stack.extend(
                sorted(node, key=repr) if isinstance(node, (set, frozenset)) else node
            )
        elif isinstance(node, _Tracked):
            stack.append(node.payload)
    return found


def _is_mutable(node: object) -> bool:
    return isinstance(node, (list, dict, set, _Tracked))


def _comparable(value: object) -> object:
    """A structural fingerprint independent of object identity."""
    if isinstance(value, dict):
        return ("dict", [(_comparable(k), _comparable(v)) for k, v in value.items()])
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, sorted(map(repr, map(_comparable, value))))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, [_comparable(v) for v in value])
    if isinstance(value, _Tracked):
        return ("tracked", _comparable(value.payload))
    if isinstance(value, _RaisingDeepcopy):
        return type(value).__name__
    return value


def test_abandoned_partial_twins_are_released_like_deepcopy() -> None:
    class Fresh:
        # A shared exception's traceback would pin the frames under test.
        def __deepcopy__(self, memo: dict[int, object]) -> Fresh:
            message = "fresh failure"
            raise LookupError(message)

    def failing_twins(copier: Any) -> list[weakref.ref[_Tracked]]:
        source = [_Tracked([_Tracked([1]), Fresh()])]
        _ALLOCATED.clear()
        try:
            copier(source)
        except LookupError:
            pass
        else:
            pytest.fail("the failing hook did not propagate")
        twins = list(_ALLOCATED)
        _ALLOCATED.clear()
        return twins

    control = failing_twins(copy.deepcopy)
    observed = failing_twins(clone.deep)
    gc.collect()
    assert control, "deepcopy control allocated no partial twin"
    assert observed, "the walker never reached the admitted dataclass"
    assert all(ref() is None for ref in control)
    assert all(ref() is None for ref in observed), "a half-built clone survived"


@pytest.mark.parametrize(
    "position", ["root", "dict-value", "list", "tuple", "dataclass-field", "nested"]
)
def test_uncopyable_values_raise_deepcopys_exception_class(
    position: str, backend: str
) -> None:
    source = _positions(threading.Lock())[position]
    with pytest.raises(TypeError) as expected:
        copy.deepcopy(source)
    with pytest.raises(type(expected.value)):
        clone.deep(source)


def test_hash_failure_while_rebuilding_a_set_propagates_with_identity() -> None:
    failure = ArithmeticError("hash failed")

    class Fragile:
        hashes = 0

        def __hash__(self) -> int:
            Fragile.hashes += 1
            if Fragile.hashes > 1:
                raise failure
            return 1

    member = Fragile()
    source = {member}
    with pytest.raises(ArithmeticError) as expected:
        copy.deepcopy(source)
    Fragile.hashes = 1
    with pytest.raises(ArithmeticError) as observed:
        clone.deep(source)
    assert expected.value is observed.value is failure
    assert list(source) == [member]


def test_failing_admission_hook_does_not_poison_the_class_cache() -> None:
    @dataclasses.dataclass
    class Holder:
        items: list[Any]

    bad = _RaisingDeepcopy()
    with pytest.raises(LookupError):
        clone.deep(Holder([bad]))
    healthy = Holder([[1, 2]])
    twin = clone.deep(healthy)
    assert type(twin) is Holder
    assert twin == healthy
    assert twin.items is not healthy.items
    assert twin.items[0] is not healthy.items[0]


def test_pydantic_field_extra_and_private_hook_errors_propagate() -> None:
    pydantic = require_backend("pydantic")

    class Model(pydantic.BaseModel, extra="allow"):
        field: Any = None
        _private: Any = pydantic.PrivateAttr(default=None)

    for where in ("field", "extra", "private"):
        bad = _RaisingDeepcopy()
        model = Model(field=[bad]) if where == "field" else Model()
        if where == "extra":
            model = Model(note={"k": bad})
        elif where == "private":
            model._private = [bad]
        state = (
            dict(model.__dict__),
            dict(model.__pydantic_extra__ or {}),
            dict(model.__pydantic_private__ or {}),
            set(model.model_fields_set),
        )
        with pytest.raises(LookupError) as expected:
            model.model_copy(deep=True)
        with pytest.raises(LookupError) as observed:
            clone.deep({"model": model})
        assert expected.value is observed.value is _FAILURE
        assert (
            model.__dict__,
            model.__pydantic_extra__ or {},
            model.__pydantic_private__ or {},
            model.model_fields_set,
        ) == state
        bad.armed = False
        twin = clone.deep(model)
        assert twin == model.model_copy(deep=True)
        assert twin.model_fields_set == model.model_fields_set


def test_msgspec_post_init_error_on_clone_propagates_with_identity() -> None:
    msgspec = require_backend("msgspec")
    failure = OverflowError("post-init refused the copy")
    calls: list[int] = []

    class Point(msgspec.Struct):
        x: int
        tags: list[str]

        def __post_init__(self) -> None:
            calls.append(self.x)
            if len(calls) > 1:
                raise failure

    original = Point(1, ["a"])
    with pytest.raises(OverflowError) as expected:
        copy.deepcopy(original)
    del calls[1:]
    with pytest.raises(OverflowError) as observed:
        clone.deep([original])
    assert expected.value is observed.value is failure
    assert calls == [1, 1], "the failed reconstruction was retried"
    assert (original.x, original.tags) == (1, ["a"])

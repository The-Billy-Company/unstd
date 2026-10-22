"""Native cloning retains types, ownership, fallback hooks and GC mutation safety."""

from __future__ import annotations

import gc
import subprocess
import sys
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.clone import structural


native = pytest.importorskip("unstd.clone._clone_native")
_ATOM = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(), st.text(), st.binary()
)
_TREE = st.recursive(
    _ATOM,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(st.text(max_size=8), children, max_size=8),
        st.tuples(children, children),
    ),
    max_leaves=40,
)


def _assert_clone(actual: Any, source: Any) -> None:
    assert type(actual) is type(source)
    if type(source) is dict:
        assert actual is not source
        assert list(actual) == list(source)
        for (key, value), (old_key, old_value) in zip(
            actual.items(), source.items(), strict=True
        ):
            assert key is old_key
            _assert_clone(value, old_value)
    elif type(source) is list or type(source) is tuple:
        if source or type(source) is list:
            assert actual is not source
        assert len(actual) == len(source)
        for value, old in zip(actual, source, strict=True):
            _assert_clone(value, old)
    else:
        # Includes NaNs: the contract is atomic identity, not equality.
        assert actual is source


@given(_TREE)
def test_native_tree_matches_python_types_order_and_independence(source: Any) -> None:
    actual = native.clone(source)
    assert actual is not NotImplemented
    _assert_clone(actual, source)
    _assert_clone(structural._walk(source), source)


def test_native_preserves_atomic_identity_and_duplicates_shared_branches() -> None:
    shared = [complex(2, 3), Ellipsis, NotImplemented]
    source = {"left": shared, "right": shared}
    actual = structural.deep(source)
    _assert_clone(actual, source)
    assert actual["left"] is not actual["right"]
    actual["left"].append(4)
    assert shared == [complex(2, 3), Ellipsis, NotImplemented]


def test_cycles_and_depth_refuse_without_exposing_partial_output() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    assert native.clone(cycle) is NotImplemented
    actual = structural.deep(cycle)
    assert actual is actual[0]
    assert actual is not cycle
    deep: Any = 1
    for _ in range(150):
        deep = [deep]
    assert native.clone(deep) is NotImplemented
    _assert_clone(structural.deep(deep), deep)


def test_refusal_never_runs_copy_hash_equality_or_subclass_iteration_hooks() -> None:
    calls: list[str] = []

    class Foreign:
        def __hash__(self) -> int:
            calls.append("hash")
            return 1

        def __eq__(self, other: object) -> bool:
            calls.append("equal")
            return self is other

        def __deepcopy__(self, memo: object) -> str:
            calls.append("copy")
            return "copied"

    class Items(list):
        def __iter__(self):
            calls.append("iterate")
            return super().__iter__()

    foreign = Foreign()
    keyed = {foreign: [1]}
    calls.clear()
    for source in (keyed, {"nested": [foreign]}, {"nested": Items([1])}):
        assert native.clone(source) is NotImplemented
        assert calls == []
    assert structural.deep({"nested": [foreign]}) == {"nested": ["copied"]}
    assert calls == ["copy"]


@pytest.mark.parametrize("enabled", [False, True])
def test_copy_hooks_keep_live_list_and_dict_mutation_semantics(
    enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(structural, "_native_clone", native.clone if enabled else None)
    items: list[Any] = []

    class Append:
        def __deepcopy__(self, memo: object) -> str:
            items.append("appended")
            return "copied"

    items.append(Append())
    assert structural.deep(items) == ["copied", "appended"]
    mapping: dict[str, Any] = {}

    class Resize:
        def __deepcopy__(self, memo: object) -> str:
            mapping["new"] = 1
            return "copied"

    mapping["first"] = Resize()
    with pytest.raises(RuntimeError, match="dictionary changed size"):
        structural.deep(mapping)


@pytest.mark.parametrize("container", [dict, list])
def test_gc_mutation_with_allocation_pressure(container: type) -> None:
    values = [{"payload": [i, i + 1]} for i in range(200)]
    source = dict(enumerate(values)) if container is dict else values
    # Native dictionaries require exact string keys.
    if container is dict:
        source = {str(key): value for key, value in source.items()}
    thresholds = gc.get_threshold()
    was_enabled = gc.isenabled()
    collections = 0
    active = False

    def mutate(phase: str, info: dict[str, int]) -> None:
        nonlocal collections
        if active and phase == "start":
            collections += 1
            if collections == 1:
                source.clear()

    # Modern maintenance releases may defer collection to evaluator safe points.
    # This stressor is valid both there and on runtimes which collect in C
    # allocations; ownership cannot depend on when collection is scheduled.
    gc.collect()
    gc.callbacks.append(mutate)
    try:
        gc.set_threshold(1, 1, 1)
        gc.enable()
        active = True
        try:
            actual = native.clone(source)
        except RuntimeError:
            assert container is dict
        else:
            assert type(actual) is container
            assert actual is not source
        active = False
        assert collections >= 1
    finally:
        active = False
        gc.callbacks.remove(mutate)
        gc.set_threshold(*thresholds)
        if not was_enabled:
            gc.disable()


def test_success_and_refusal_release_all_owned_atomic_references() -> None:
    value = "distinct atomic value " * 17
    accepted = [{"key": [value, value]}]
    refused = [{"key": [value, value]}, object()]
    tracked = (
        value,
        accepted,
        accepted[0],
        accepted[0]["key"],
        refused,
        refused[0],
        refused[0]["key"],
    )
    before = [sys.getrefcount(item) for item in tracked]
    for _ in range(1000):
        native.clone(accepted)
        assert native.clone(refused) is NotImplemented
    assert [sys.getrefcount(item) for item in tracked] == before


def test_traversal_breadth_budget_defers_without_changing_public_result() -> None:
    value = [0] * 65536
    assert native.clone(value) is NotImplemented
    assert native.clone([value[:32768], value[32768:]]) is NotImplemented
    _assert_clone(structural.deep(value), value)


def test_recursion_pressure_and_dispatch_limit_changes_keep_python_fallback() -> None:
    # Isolate recursion and collector settings from pytest's own call stack.
    code = """
import sys
from unstd.clone import structural as s
from unstd.clone import _clone_native as n
value = 1
for _ in range(60):
    value = [value]
backend = s._native_clone
for use_native in (False, True):
    s._native_clone = backend if use_native else None
    sys.setrecursionlimit(40)
    try:
        s.deep(value)
    except RecursionError:
        pass
    else:
        raise AssertionError('Python recursion contract changed')
    finally:
        sys.setrecursionlimit(1000)
changed = []
def lower_limit(frame, event, arg):
    if event == 'c_call' and arg is n.clone:
        sys.setprofile(None)
        changed.append(True)
        sys.setrecursionlimit(40)
sys.setprofile(lower_limit)
try:
    result = n.clone(value)
    assert changed
    assert result is NotImplemented
finally:
    sys.setprofile(None)
    sys.setrecursionlimit(1000)
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal source, no shell
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(
    sys.version_info >= (3, 14),
    reason="CPython 3.14 guards the machine stack instead of a C recursion counter",
)
def test_native_stack_pressure_defers_without_preserving_acyclic_aliases() -> None:
    # Pair the public CPython enter/leave APIs to simulate a nested extension
    # caller without actually consuming the machine stack or needing a compiler.
    # The subprocess contains the deliberately exhausted C recursion budget.
    code = """
import ctypes
from unstd.clone import structural as s
from unstd.clone import _clone_native as n
enter = ctypes.pythonapi.Py_EnterRecursiveCall
enter.argtypes = [ctypes.c_char_p]
enter.restype = ctypes.c_int
leave = ctypes.pythonapi.Py_LeaveRecursiveCall
leave.argtypes = []
leave.restype = None
entered = 0
exhausted = False
try:
    while entered < 20000:
        try:
            enter(b' while simulating a nested extension caller')
        except RecursionError:
            exhausted = True
            break
        entered += 1
finally:
    for _ in range(entered):
        leave()
assert exhausted, 'bounded probe did not reach the CPython C recursion budget'
pressure = entered - 100
assert pressure > 0
shared = [1]
source = [shared, shared]
for _ in range(100):
    source = [source]
for backend in (None, n.clone):
    s._native_clone = backend
    entered = 0
    try:
        for _ in range(pressure):
            enter(b' while simulating a nested extension caller')
            entered += 1
        if backend is not None:
            assert n.clone(source) is NotImplemented
        result = s.deep(source)
    finally:
        for _ in range(entered):
            leave()
    for _ in range(100):
        result = result[0]
    assert result[0] is not result[1], 'native pressure selected whole-graph deepcopy'
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal source, no shell
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

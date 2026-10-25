"""Native cloning retains types, ownership, fallback hooks and GC mutation safety."""

from __future__ import annotations

import copy
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


def test_native_deep_preserves_atomic_and_shared_branch_identity() -> None:
    shared = [complex(2, 3), Ellipsis, NotImplemented]
    source = {"left": shared, "right": shared}
    actual = structural.deep(source)
    _assert_clone(actual, source)
    assert actual["left"] is actual["right"]
    actual["left"].append(4)
    assert shared == [complex(2, 3), Ellipsis, NotImplemented]


def test_native_graph_copies_aliases_without_a_python_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = [1, {"values": [2]}]
    source = {"first": shared, "again": shared, "tuple": (shared, shared)}

    def refuse(*_args: object) -> object:
        pytest.fail("eligible shared builtin graphs must stay native")

    monkeypatch.setattr(structural, "_walk", refuse)
    actual = structural.deep(source)

    assert (
        actual["first"] is actual["again"] is actual["tuple"][0] is actual["tuple"][1]
    )
    assert actual["first"] is not shared
    assert actual["first"][1]["values"] is not shared[1]["values"]
    tree_copy = native.clone(source)
    assert tree_copy["first"] is not tree_copy["again"]


@pytest.mark.parametrize("external_memo", [False, True])
@pytest.mark.parametrize("levels", [30, 75])
def test_native_graph_copies_dense_aliases_without_expanding_the_graph(
    external_memo: bool, levels: int
) -> None:
    source: Any = [1]
    for _ in range(levels):
        source = [source, source]

    actual = (
        native.clone_graph(source, {}) if external_memo else native.clone_graph(source)
    )

    assert actual is not NotImplemented
    original = source
    for _ in range(levels):
        assert actual is not original
        assert actual[0] is actual[1]
        actual, original = actual[0], original[0]
    assert actual == original == [1]
    assert actual is not original


@pytest.mark.parametrize("external_memo", [False, True])
@given(source=_TREE)
def test_native_graph_matches_exact_tree_types_order_and_independence(
    source: Any, external_memo: bool
) -> None:
    actual = (
        native.clone_graph(source, {}) if external_memo else native.clone_graph(source)
    )

    assert actual is not NotImplemented
    _assert_clone(actual, source)


@pytest.mark.parametrize("external_memo", [False, True])
def test_native_graph_keeps_equal_distinct_nodes_and_aliases_across_a_wide_graph(
    external_memo: bool,
) -> None:
    rows = [{"values": [1]} for _ in range(500)]
    repeated = [0, 31, 63, 64, 255, 256, 499]
    source = {"rows": rows, "again": [rows[index] for index in repeated]}

    actual = (
        native.clone_graph(source, {}) if external_memo else native.clone_graph(source)
    )

    assert actual is not NotImplemented
    _assert_clone(actual, source)
    assert len({id(row) for row in actual["rows"]}) == len(rows)
    assert len({id(row["values"]) for row in actual["rows"]}) == len(rows)
    for index, repeated_row in zip(repeated, actual["again"], strict=True):
        assert repeated_row is actual["rows"][index]
    actual["rows"][0]["values"].append(2)
    assert actual["again"][0]["values"] == [1, 2]
    assert all(row["values"] == [1] for row in rows)


def test_native_graph_without_external_memo_releases_sources_and_partial_results() -> (
    None
):
    leaf = [1]
    source = [leaf, leaf]
    original_references = sys.getrefcount(leaf)

    first = native.clone_graph(source)
    second = native.clone_graph(source)

    assert first[0] is first[1]
    assert second[0] is second[1]
    assert first[0] is not second[0]
    assert first[0] is not leaf
    assert sys.getrefcount(leaf) == original_references
    assert native.clone_graph([leaf, object()]) is NotImplemented
    assert sys.getrefcount(leaf) == original_references
    cycle: list[Any] = [leaf]
    cycle.append(cycle)
    with pytest.raises(RecursionError):
        native.clone_graph(cycle)
    assert sys.getrefcount(leaf) == original_references + 1
    cycle.clear()
    assert sys.getrefcount(leaf) == original_references


def test_native_graph_honors_existing_memo_entries_and_records_the_root() -> None:
    shared = [1]
    already_copied = [9]
    source = [shared, {"again": shared}]
    memo = {id(shared): already_copied}

    actual = native.clone_graph(source, memo)

    assert actual is not NotImplemented
    assert actual[0] is actual[1]["again"] is already_copied
    assert memo[id(source)] is actual
    assert shared == [1]


def test_native_graph_reuses_a_preseeded_root_before_checking_its_cycle() -> None:
    source: list[Any] = []
    source.append(source)
    already_copied: list[Any] = []
    memo = {id(source): already_copied}

    assert native.clone_graph(source, memo) is already_copied
    assert memo[id(source)] is already_copied


def test_native_graph_memo_bridges_a_later_stdlib_deepcopy() -> None:
    shared = [1]
    source = {"nested": [shared, shared]}
    memo: dict[int, Any] = {}

    actual = native.clone_graph(source, memo)
    later = copy.deepcopy({"again": shared}, memo)

    assert actual is not NotImplemented
    assert actual["nested"][0] is actual["nested"][1] is later["again"]
    assert memo[id(source["nested"])] is actual["nested"]
    assert later["again"] is not shared


def test_native_graph_refusal_leaves_the_callers_memo_unchanged() -> None:
    shared = [1]
    source = {"plain": shared, "foreign": object()}
    already_copied = [9]
    memo = {id(shared): already_copied}
    initial = memo.copy()

    assert native.clone_graph(source, memo) is NotImplemented

    assert memo == initial
    assert memo[id(shared)] is already_copied


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


def test_tuple_cycle_fallback_preserves_aliases_across_the_whole_graph() -> None:
    shared = [1]
    backrefs: list[Any] = []
    ring = (backrefs, shared)
    backrefs.extend([ring, shared])
    source = {"ring": ring, "outside": shared}

    assert native.clone(source) is NotImplemented
    actual = structural.deep(source)
    assert actual is not source
    assert actual["ring"] is not ring
    assert actual["ring"][0] is not backrefs
    assert actual["ring"][0][0] is actual["ring"]
    assert actual["ring"][0][1] is actual["ring"][1] is actual["outside"]
    actual["outside"].append(2)
    assert shared == [1]


@pytest.mark.parametrize("kind", ["list", "dict", "tuple"])
def test_checked_builtin_cycles_skip_the_python_recursion_probe(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = [1]
    backrefs: list[Any] = []
    source: Any
    if kind == "dict":
        source = {"cycle": backrefs, "first": shared, "again": shared}
        backrefs.append(source)
    elif kind == "tuple":
        source = (backrefs, shared, shared)
        backrefs.append(source)
    else:
        source = [backrefs, shared, shared]
        backrefs.append(source)

    def refuse(_obj: object) -> object:
        pytest.fail("a proved builtin cycle must skip the Python recursion probe")

    assert native.clone(source) is NotImplemented
    with pytest.raises(RecursionError):
        native.clone_checked(source)
    monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    monkeypatch.setattr(structural, "_walk", refuse)
    actual = structural.deep(source)
    if kind == "dict":
        assert actual["cycle"][0] is actual
        assert actual["first"] is actual["again"]
        assert actual["first"] is not shared
    else:
        assert actual[0][0] is actual
        assert actual[1] is actual[2]
        assert actual[1] is not shared


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
    for copier in (native.clone, native.clone_checked, native.clone_graph):
        for source in (keyed, {"nested": [foreign]}, {"nested": Items([1])}):
            assert copier(source) is NotImplemented
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


def test_gc_observers_never_see_partially_initialized_sequences() -> None:
    # Presized lists and tuples temporarily contain NULL slots at the C level.
    # A collector callback must not be able to retrieve one and traverse it.
    # Keep the observer in a subprocess so a broken ownership invariant produces
    # a bounded test failure rather than taking down the entire pytest worker.
    code = """
import gc
from unstd.clone import _clone_native as native

gc.collect()
observations = 0
def inspect(phase, info):
    global observations
    if phase != 'start':
        return
    for value in gc.get_objects():
        if type(value) in (list, tuple) and len(value) == 257:
            repr(value)
    observations += 1

gc.callbacks.append(inspect)
gc.set_threshold(1, 1, 1)
for kind in (list, tuple):
    source = kind([[i] for i in range(257)])
    for _ in range(20):
        actual = native.clone(source)
        assert type(actual) is kind
        assert actual == source
        assert actual is not source
assert observations, 'collector observer never ran'
gc.callbacks.remove(inspect)
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal source, no shell
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gc_inserted_hash_collisions_never_invoke_foreign_equality() -> None:
    # Distinct string hashes sharing the initial index put the retained key
    # behind a removable slot. A later foreign key can occupy that slot, making
    # even an exact-string dictionary lookup invoke its __eq__ implementation.
    target = "retained clone key"
    blocker = next(
        str(index) for index in range(1024) if hash(str(index)) & 7 == hash(target) & 7
    )
    calls: list[str] = []

    class Foreign:
        def __hash__(self) -> int:
            calls.append("hash")
            return hash(target)

        def __eq__(self, other: object) -> bool:
            calls.append("equal")
            return False

    foreign = Foreign()
    # Retain a general-key table so adding Foreign does not rebuild a compact
    # Unicode-only table and remove the deliberately placed collision hole.
    source: dict[Any, Any] = {0: None}
    del source[0]
    source[blocker] = [1]
    source[target] = [2]
    thresholds = gc.get_threshold()
    was_enabled = gc.isenabled()
    active = False
    mutated = False
    collision_proved = False

    def mutate(phase: str, info: dict[str, int]) -> None:
        nonlocal mutated, collision_proved
        if active and phase == "start" and not mutated:
            mutated = True
            del source[blocker]
            source[foreign] = []
            # Positive control for the lookup trap, then exclude work performed
            # by the mutator itself from the native no-hooks assertion.
            calls.clear()
            source[target]
            collision_proved = "equal" in calls
            calls.clear()

    gc.collect()
    gc.callbacks.append(mutate)
    try:
        gc.set_threshold(1, 1, 1)
        gc.enable()
        active = True
        try:
            actual = native.clone(source)
        except RuntimeError:
            pass
        else:
            if actual is not NotImplemented:
                assert type(actual) is dict
                assert all(type(key) is str for key in actual)
        active = False
        assert mutated
        assert collision_proved
        assert calls == []
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


@pytest.mark.parametrize("container", [list, tuple, dict])
def test_scalar_breadth_does_not_exhaust_the_native_container_budget(
    container: type,
) -> None:
    values = [0] * 65536
    value = (
        {str(index): item for index, item in enumerate(values)}
        if container is dict
        else container(values)
    )
    _assert_clone(native.clone(value), value)
    branches = [value, value]
    _assert_clone(native.clone(branches), branches)
    _assert_clone(structural.deep(value), value)


def test_container_breadth_budget_defers_without_changing_public_result() -> None:
    wide = [[] for _ in range(65536)]
    assert native.clone(wide) is NotImplemented
    _assert_clone(structural.deep(wide), wide)


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
def test_native_stack_pressure_defers_and_preserves_acyclic_aliases() -> None:
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
    assert result[0] is result[1], 'native pressure split a shared reference'
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal source, no shell
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

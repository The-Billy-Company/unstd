"""Dataclass acceleration retains live iteration, allocation and state observations."""

from __future__ import annotations

import copy
import dataclasses
import gc
import subprocess
import sys
from typing import Any, Self
import weakref

import pytest

from unstd import clone
from unstd.clone import structural


@pytest.fixture(params=["python", "native"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "native":
        native = pytest.importorskip("unstd.clone._clone_native")
        monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    else:
        for name in list(vars(structural)):
            if name.startswith("_native_"):
                monkeypatch.setattr(structural, name, None)


def test_homogeneous_list_recipe_keeps_live_replacement_and_heterogeneous_append(
    backend: None,
) -> None:
    armed = False
    source: list[Any] = []
    calls: list[str] = []

    class Foreign:
        def __deepcopy__(self, memo: object) -> dict[str, list[int]]:
            calls.append("foreign")
            return {"copied": [9]}

    @dataclasses.dataclass
    class Row:
        values: list[int]

        def __new__(cls, *_args: object) -> Self:
            nonlocal armed
            if armed:
                armed = False
                source[1] = Foreign()
                source.append(Row([3]))
            return object.__new__(cls)

    source.extend([Row([1]), Row([2])])
    armed = True
    actual = clone.deep(source)

    assert len(actual) == 3
    assert type(actual[0]) is Row
    assert actual[0].values == [1]
    assert actual[0].values is not source[0].values
    assert actual[1] == {"copied": [9]}
    assert type(actual[2]) is Row
    assert actual[2].values == [3]
    assert actual[2].values is not source[2].values
    assert calls == ["foreign"]


def test_homogeneous_list_recipe_stops_when_first_allocator_clears_source(
    backend: None,
) -> None:
    armed = False
    source: list[Any] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

        def __new__(cls, *_args: object) -> Self:
            nonlocal armed
            if armed:
                armed = False
                source.clear()
            return object.__new__(cls)

    first = Row([1])
    source.extend([first, Row([2])])
    armed = True
    actual = clone.deep(source)

    assert source == []
    assert len(actual) == 1
    assert type(actual[0]) is Row
    assert actual[0] is not first
    assert actual[0].values == [1]
    assert actual[0].values is not first.values


@pytest.mark.parametrize("container", [list, tuple])
def test_later_dataclass_state_is_read_after_earlier_allocator_runs(
    backend: None, container: type
) -> None:
    armed = False
    later: Any = None

    @dataclasses.dataclass
    class Row:
        values: list[int]

        def __new__(cls, *_args: object) -> Self:
            nonlocal armed
            if armed:
                armed = False
                later.__dict__ = {"values": [99], "undeclared": [4]}
            return object.__new__(cls)

    first = Row([1])
    later = Row([2])
    source = container([first, later])
    armed = True
    actual = clone.deep(source)

    assert type(actual) is container
    assert actual[0].values == [1]
    assert actual[0].values is not first.values
    assert actual[1].values == [99]
    assert actual[1].undeclared == [4]
    assert actual[1].values is not later.values
    assert actual[1].undeclared is not later.undeclared


def test_custom_dict_access_keeps_the_existing_source_then_twin_order(
    backend: None,
) -> None:
    observed: list[int] = []
    armed = False

    @dataclasses.dataclass
    class Row:
        values: list[int]

        def __getattribute__(self, name: str) -> Any:
            if armed and name == "__dict__":
                observed.append(id(self))
            return object.__getattribute__(self, name)

    source = Row([1])
    armed = True
    actual = clone.deep(source)
    armed = False

    assert observed == [id(source), id(actual)]
    assert type(actual) is Row
    assert actual.values == [1]
    assert actual.values is not source.values


def test_nonfield_state_and_nonstring_state_keys_are_cloned(backend: None) -> None:
    @dataclasses.dataclass
    class Row:
        values: list[int]

    source = Row([1])
    source.__dict__["extra"] = [2]
    source.__dict__[7] = [3]
    actual = clone.deep([source])[0]

    assert type(actual) is Row
    assert list(vars(actual)) == ["values", "extra", 7]
    assert vars(actual) == vars(source)
    assert all(vars(actual)[key] is not value for key, value in vars(source).items())


def test_empty_state_skips_access_to_the_uninitialized_twin_dict(backend: None) -> None:
    observed: list[int] = []
    armed = False

    @dataclasses.dataclass
    class Empty:
        def __getattribute__(self, name: str) -> Any:
            if armed and name == "__dict__":
                observed.append(id(self))
            return object.__getattribute__(self, name)

    source = Empty()
    armed = True
    actual = clone.deep(source)
    armed = False

    assert observed == [id(source)]
    assert type(actual) is Empty
    assert actual is not source
    assert vars(actual) == {}


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_destination_update_is_captured_before_copying_state_descendants(
    backend: None, lookup_fails: bool
) -> None:
    events: list[str] = []
    armed = False
    source: Any = None
    failure = LookupError("update lookup failed")

    class Destination:
        owner: Any = None
        tag = "original"

        @property
        def update(self) -> Any:
            events.append("lookup")
            if lookup_fails:
                raise failure
            selected = self.tag

            def apply(state: Any) -> None:
                events.append(f"update-{selected}")
                object.__getattribute__(self.owner, "__dict__").update(state)

            return apply

    destination = Destination()

    class ChangeUpdate:
        def __deepcopy__(self, memo: object) -> list[int]:
            events.append("copy")
            destination.tag = "replaced"
            return [7]

    @dataclasses.dataclass
    class Row:
        values: list[Any]

        def __getattribute__(self, name: str) -> Any:
            if armed and name == "__dict__":
                if self is source:
                    events.append("source")
                else:
                    events.append("twin")
                    destination.owner = self
                    return destination
            return object.__getattribute__(self, name)

    source = Row([ChangeUpdate()])
    armed = True
    if lookup_fails:
        with pytest.raises(LookupError) as observed:
            clone.deep(source)
        assert observed.value is failure
        assert events == ["source", "twin", "lookup"]
    else:
        actual = clone.deep(source)
        assert events == ["source", "twin", "lookup", "copy", "update-original"]
        assert type(actual) is Row
        assert actual.values == [[7]]
        assert actual.values is not source.values


def test_source_item_finalizer_runs_after_live_iteration_decides_to_stop(
    backend: None,
) -> None:
    source: list[Any] = []
    finalized: list[str] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    class Foreign:
        def __deepcopy__(self, memo: object) -> dict[str, list[int]]:
            del source[1]
            return {"copied": [8]}

        def __del__(self) -> None:
            finalized.append("foreign")
            source.append("finalized")

    source.extend([Row([1]), Foreign(), Row([3])])
    actual = clone.deep(source)

    assert len(actual) == 2
    assert type(actual[0]) is Row
    assert actual[0].values == [1]
    assert actual[1] == {"copied": [8]}
    assert finalized == ["foreign"]
    assert type(source[1]) is Row
    assert source[1].values == [3]
    assert source[2] == "finalized"


def test_dictionary_source_item_lives_through_the_next_mutation_check(
    backend: None,
) -> None:
    source: dict[str, Any] = {}
    finalized: list[str] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    class Foreign:
        def __deepcopy__(self, memo: object) -> list[int]:
            del source["victim"]
            return [8]

        def __del__(self) -> None:
            finalized.append("foreign")
            source["replacement"] = [9]

    source.update(first=Row([1]), victim=Foreign(), last=Row([3]))
    with pytest.raises(RuntimeError, match="dictionary changed size"):
        clone.deep(source)

    assert finalized == ["foreign"]
    assert list(source) == ["first", "last", "replacement"]
    assert source["replacement"] == [9]


@pytest.mark.parametrize(
    "protocol", ["descriptor", "metaclass-getattr", "metaclass-hash"]
)
@pytest.mark.parametrize("mutation", ["clear", "grow"])
def test_native_recipe_probe_does_not_run_protocol_hooks_before_fetching_the_row(
    backend: None, protocol: str, mutation: str
) -> None:
    source: list[Any] = []
    armed = False
    calls: list[str] = []
    changed = False

    def mutate_once() -> None:
        nonlocal changed
        if not changed:
            changed = True
            if mutation == "clear":
                source.clear()
            else:
                source.append(Row([3, 4]))

    class Meta(type):
        def __getattribute__(cls, name: str) -> Any:
            if armed and protocol == "metaclass-getattr" and name == "__deepcopy__":
                calls.append("getattr")
                mutate_once()
            return super().__getattribute__(name)

        def __hash__(cls) -> int:
            if armed and protocol == "metaclass-hash":
                calls.append("hash")
                mutate_once()
            return super().__hash__()

    class NullCopy:
        def __get__(self, instance: object, owner: type) -> None:
            if armed:
                calls.append("descriptor")
                mutate_once()
            return

    if protocol == "descriptor":

        @dataclasses.dataclass
        class Row:
            values: list[int]
            __deepcopy__ = NullCopy()

    else:

        @dataclasses.dataclass
        class Row(metaclass=Meta):
            values: list[int]

    first = Row([1, 2])
    source.append(first)
    armed = True
    actual = clone.deep(source)
    armed = False

    assert calls
    assert len(actual) == (1 if mutation == "clear" else 2)
    if mutation == "clear":
        assert source == []
    else:
        assert len(source) == 2
        assert type(actual[1]) is Row
        assert actual[1].values == [3, 4]
        assert actual[1].values is not source[1].values
    assert type(actual[0]) is Row
    assert actual[0] is not first
    assert actual[0].values == [1, 2]
    assert actual[0].values is not first.values


def test_class_changed_by_admission_keeps_the_already_selected_default_recipe(
    backend: None,
) -> None:
    armed = False
    current: Any = None
    copied: list[str] = []

    class NewClass:
        def __deepcopy__(self, memo: object) -> str:
            copied.append("new-copy-hook")
            return "redispatched"

    class ChangeClass:
        def __get__(self, instance: object, owner: type) -> None:
            if armed:
                current.__class__ = NewClass

    @dataclasses.dataclass
    class Original:
        values: list[int]
        __deepcopy__ = ChangeClass()

    current = Original([1, 2])
    source = [current]
    armed = True
    actual = clone.deep(source)
    armed = False

    assert type(current) is NewClass
    assert type(actual[0]) is NewClass
    assert actual[0] is not current
    assert actual[0].values == [1, 2]
    assert actual[0].values is not current.values
    assert copied == []


def test_plain_dataclass_state_uses_the_native_record_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")

    @dataclasses.dataclass
    class Row:
        values: list[int]

    def refuse(_obj: object, _memo: object) -> object:
        pytest.fail("a plain record's builtin state must stay in the native helper")

    monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    monkeypatch.setattr(structural, "_native_dataclass", native.clone_dataclass)
    monkeypatch.setattr(structural, "_walk_data", refuse)
    source = Row([1, 2])

    actual = clone.deep(source)

    assert type(actual) is Row
    assert actual == source
    assert actual is not source
    assert actual.values is not source.values


def test_dataclass_state_hook_reuses_parent_and_outer_field_memo(backend: None) -> None:
    calls: list[dict[int, Any]] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    class State(dict):
        def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
            calls.append(memo)
            # The instance is safely visible before any descendant copy hook,
            # and its destination dictionary is populated only after state copy.
            assert memo[id(source)] is not source
            assert vars(memo[id(source)]) == {}
            twin: dict[str, Any] = {}
            memo[id(self)] = twin
            twin.update(
                (key, copy.deepcopy(value, memo)) for key, value in self.items()
            )
            twin["hooked"] = True
            return twin

    shared = [1]
    source = Row(shared)
    source.__dict__ = State(values=shared, parent=source)

    actual = clone.deep([source, shared, source])

    assert len(calls) == 1
    assert actual[0] is actual[2]
    assert actual[0].values is actual[1]
    assert actual[0].values is not shared
    assert actual[0].parent is actual[0]
    assert actual[0].hooked is True
    assert type(vars(actual[0])) is dict
    assert "hooked" not in source.__dict__


def test_native_record_helper_reuses_a_preseeded_twin() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")

    @dataclasses.dataclass
    class Row:
        values: list[int]

    def refuse(_obj: object, _memo: object) -> object:
        pytest.fail("a supplied twin must avoid allocation and state traversal")

    source = Row([1])
    existing = object()
    memo = {id(source): existing}

    assert native.clone_dataclass(source, memo, refuse) is existing
    assert memo == {id(source): existing}


def test_native_record_helper_refuses_custom_allocation_before_copying() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    allocations: list[int] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

        def __new__(cls, *_args: object) -> Self:
            allocations.append(1)
            return object.__new__(cls)

    def refuse(_obj: object, _memo: object) -> object:
        pytest.fail("refusal must not copy record state")

    source = Row([1])
    allocations.clear()
    memo: dict[int, Any] = {}

    assert native.clone_dataclass(source, memo, refuse) is NotImplemented
    assert allocations == []
    assert memo == {}


def test_failed_record_hook_releases_a_cycle_on_the_published_twin(
    backend: None,
) -> None:
    allocated: list[weakref.ref[Any]] = []

    @dataclasses.dataclass
    class Row:
        value: Any

    class Failure:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            twin = memo[id(source)]
            twin.loop = twin
            allocated.append(weakref.ref(twin))
            msg = "record hook failed"
            raise LookupError(msg)

    source = Row(Failure())

    def attempt() -> None:
        try:
            clone.deep(source)
        except LookupError:
            pass
        else:
            pytest.fail("the state hook failure must propagate")

    attempt()
    gc.collect()

    assert len(allocated) == 1
    assert allocated[0]() is None
    assert "loop" not in vars(source)


def test_state_truth_hook_changes_destination_access_before_lookup(
    backend: None,
) -> None:
    reads: list[int] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    def getter(self: Any, name: str) -> Any:
        if name == "__dict__":
            reads.append(id(self))
        return object.__getattribute__(self, name)

    class State(dict):
        def __bool__(self) -> bool:
            # Admission selected an ordinary class; state truth is evaluated
            # before the destination dictionary/update method is resolved.
            Row.__getattribute__ = getter
            return True

    source = Row([1])
    source.__dict__ = State(values=[1])

    actual = clone.deep(source)

    assert reads == [id(actual)]
    assert actual.values == [1]
    assert actual.values is not source.values

    reads.clear()
    another = clone.deep(source)
    assert reads == [id(source), id(another)]
    assert another.values is not actual.values


def test_cached_native_record_rechecks_a_replaced_allocator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    allocations: list[object] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    def allocate(cls: type[Row]) -> Row:
        twin = object.__new__(cls)
        allocations.append(twin)
        return twin

    def refuse(_obj: object, _memo: object) -> object:
        pytest.fail("an invalidated record class must refuse before state traversal")

    source = Row([1])
    monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    monkeypatch.setattr(structural, "_native_dataclass", native.clone_dataclass)
    # The public batch walker has its own per-call recipes. Warm this helper's
    # weak layout cache directly before changing the allocator it admitted.
    reference = weakref.ref(Row)
    baseline = sys.getrefcount(reference)
    warm = native.clone_dataclass(source, {}, structural._walk)
    assert warm.values is not source.values
    assert sys.getrefcount(reference) > baseline

    monkeypatch.setattr(Row, "__new__", staticmethod(allocate))
    memo: dict[int, Any] = {}
    assert native.clone_dataclass(source, memo, refuse) is NotImplemented
    assert allocations == []
    assert memo == {}

    actual = clone.deep(source)
    assert allocations == [actual]
    assert actual.values == [1]
    assert actual.values is not source.values


def test_standalone_native_record_memo_pins_source_until_released() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")

    @dataclasses.dataclass
    class Row:
        values: list[int]

    source = Row([1])
    reference = weakref.ref(source)
    memo: dict[int, Any] = {}
    actual = native.clone_dataclass(source, memo, structural._walk)

    assert actual is not source
    assert actual.values is not source.values
    del source
    gc.collect()
    assert reference() is not None

    memo.clear()
    gc.collect()
    assert reference() is None
    assert actual.values == [1]


def test_native_record_callback_can_replace_both_instance_classes() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    # A direct helper call has no Python _walk frame retaining the source type.
    # Contain a stale native type pointer in a worker if this regresses.
    code = """
import dataclasses
import gc
import weakref
from unstd.clone import _clone_native as native

@dataclasses.dataclass
class Replacement:
    value: object

def make_source():
    transient = dataclasses.make_dataclass('Transient', [('value', object)])
    return transient(object()), weakref.ref(transient)

source, old_class = make_source()
opaque = source.value
memo = {}
calls = []

def walk(node, supplied_memo):
    assert supplied_memo is memo
    # The continuing native state walk delegates at the unsupported field,
    # retaining the already allocated twin and the selected source class.
    assert node is opaque
    calls.append(True)
    source.__class__ = Replacement
    twin = supplied_memo[id(source)]
    twin.__class__ = Replacement
    gc.collect()
    # Exercise the allocator after releasing the old class's Python owners.
    for index in range(32):
        temporary = dataclasses.make_dataclass('Churn', [('value', object)])
        temporary(index)
    return [9]

actual = native.clone_dataclass(source, memo, walk)
assert calls == [True]
assert type(source) is type(actual) is Replacement
assert actual.value == [9]
assert actual is memo[id(source)] and actual is not source
gc.collect()
assert old_class() is None, 'native admission cache retained the replaced class'
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal test source
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

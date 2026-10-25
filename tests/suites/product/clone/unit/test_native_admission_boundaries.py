"""Native continuations retain memo and pin behavior at dispatch boundaries."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any
import weakref

import pytest

from tests.support.environment.process import isolated_environment, run_python
from unstd import clone
from unstd.clone import structural


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(params=["python", "native", "native-dataclass-fallback"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "python":
        for name in list(vars(structural)):
            if name.startswith("_native_"):
                monkeypatch.setattr(structural, name, None)
        return
    native = pytest.importorskip("unstd.clone._clone_native")
    monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    monkeypatch.setattr(structural, "_native_graph", native.clone_graph)
    monkeypatch.setattr(structural, "_native_dataclass", native.clone_dataclass)
    monkeypatch.setattr(
        structural,
        "_native_walk_graph",
        None
        if request.param == "native-dataclass-fallback"
        else native.clone_walk_graph,
    )


@pytest.mark.parametrize("custom_new", [False, True])
def test_admission_memo_write_does_not_skip_the_selected_dataclass_handler(
    backend: None, custom_new: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    active: dict[int, Any] | None = None
    new_calls: list[type] = []

    @dataclasses.dataclass
    class Row:
        values: list[int]

    row = Row([1])

    class Install:
        def __deepcopy__(self, memo: dict[int, Any]) -> None:
            nonlocal active
            active = memo

    class Admission:
        def __get__(self, instance: Any, owner: type) -> None:
            assert active is not None
            active[id(row)] = "admission marker"

    monkeypatch.setattr(Row, "__deepcopy__", Admission(), raising=False)
    if custom_new:

        def allocate(cls: type) -> Any:
            new_calls.append(cls)
            return object.__new__(cls)

        monkeypatch.setattr(Row, "__new__", staticmethod(allocate))

    actual = clone.deep([Install(), row, row])

    assert type(actual[1]) is Row
    assert actual[1] is actual[2]
    assert actual[1] is not row
    assert actual[1].values == [1]
    assert actual[1].values is not row.values
    assert new_calls == ([Row] if custom_new else [])


@pytest.mark.parametrize("list_subclass", [False, True])
def test_dataclass_state_export_honors_custom_keepalive_append_once(
    backend: None, list_subclass: bool
) -> None:
    @dataclasses.dataclass
    class Row:
        values: list[int]

    row = Row([1])
    pinned: list[Any] = []

    def pin(source: Any) -> None:
        pinned.append(source)
        if source is row.__dict__:
            row.values.append(2)

    class Pins:
        def append(self, source: Any) -> None:
            pin(source)

    class ListPins(list[Any]):
        def append(self, source: Any) -> None:
            pin(source)
            super().append(source)

    class Install:
        def __deepcopy__(self, memo: dict[int, Any]) -> None:
            memo[id(memo)] = ListPins() if list_subclass else Pins()

    actual = clone.deep([Install(), row, row.values])

    assert type(actual[1]) is Row
    assert actual[1] is not row
    assert actual[1].values is actual[2]
    assert actual[2] == [1, 2]
    assert actual[2] is not row.values
    assert sum(source is row for source in pinned) == 1
    state_position = next(
        i for i, source in enumerate(pinned) if source is row.__dict__
    )
    values_position = next(i for i, source in enumerate(pinned) if source is row.values)
    assert state_position < values_position


@pytest.mark.parametrize("depth", [126, 127, 128])
def test_native_depth_handoff_pins_each_selected_record_once(
    backend: None, depth: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate the general walk's handoff from the separate dataclass helper.
    monkeypatch.setattr(structural, "_native_dataclass", None)

    @dataclasses.dataclass
    class Row:
        value: int

    row = Row(1)
    pinned: list[Any] = []

    class Pins(list[Any]):
        def append(self, source: Any) -> None:
            pinned.append(source)
            super().append(source)

    class Install:
        def __deepcopy__(self, memo: dict[int, Any]) -> None:
            memo[id(memo)] = Pins()

    nested: Any = row
    for _ in range(depth):
        nested = [nested]
    actual = clone.deep([Install(), nested])[1]
    for _ in range(depth):
        actual = actual[0]

    assert type(actual) is Row
    assert actual is not row
    assert actual.value == 1
    assert sum(source is row for source in pinned) == 1


@pytest.mark.parametrize(
    "kind", ["range", "type", "property", "code", "function", "builtin", "weakref"]
)
def test_atomic_siblings_ignore_hook_memo_overrides(backend: None, kind: str) -> None:
    class Owner:
        pass

    owner = Owner()
    atom = {
        "range": range(3),
        "type": Owner,
        "property": property(lambda self: 1),
        "code": (lambda: 1).__code__,
        "function": lambda: 1,
        "builtin": len,
        "weakref": weakref.ref(owner),
    }[kind]

    class Hook:
        def __deepcopy__(self, memo: dict[int, Any]) -> None:
            memo[id(atom)] = "atomic memo marker"

    actual = clone.deep([Hook(), atom])

    assert actual[1] is atom


@pytest.mark.parametrize("custom_metaclass", [False, True])
@pytest.mark.parametrize("wrapper", ["none", "ordinary", "custom-new"])
def test_memo_key_comparison_cannot_change_the_selected_source_type(
    backend: None,
    custom_metaclass: bool,
    wrapper: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if wrapper == "none":
        monkeypatch.setattr(structural, "_native_dataclass", None)

    class Meta(type):
        pass

    @dataclasses.dataclass
    class Row(metaclass=Meta if custom_metaclass else type):
        values: list[int]

    @dataclasses.dataclass
    class Outer:
        child: Any

    class Other:
        def __deepcopy__(self, memo: dict[int, Any]) -> str:
            return "foreign marker"

    row = Row([1])

    class Key:
        def __hash__(self) -> int:
            return id(row)

        def __eq__(self, other: object) -> bool:
            if other == id(row):
                row.__class__ = Other
            return False

    key = Key()

    class Hook:
        def __deepcopy__(self, memo: dict[Any, Any]) -> None:
            memo[key] = "unused collision"

    wrapped = row if wrapper == "none" else Outer(row)
    if wrapper == "custom-new":

        def allocate(cls: type) -> Any:
            return object.__new__(cls)

        monkeypatch.setattr(Outer, "__new__", staticmethod(allocate))

    result = clone.deep([Hook(), wrapped])
    actual = result[1] if wrapper == "none" else result[1].child

    assert type(actual) is Other
    assert actual is not row
    assert actual.values == [1]
    assert actual.values is not row.values


def test_native_dataclass_state_handoff_retains_recursion_limits(
    tmp_path: Path,
) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    assert structural._native_dataclass is native.clone_dataclass
    code = """
import copy
import dataclasses
import sys
from unstd.clone import structural

assert structural._NATIVE_SELECTED_MEMO
structural._native_walk_graph = None

@dataclasses.dataclass
class Row:
    values: object

def boundary():
    def recurse(level):
        try:
            return recurse(level + 1)
        except RecursionError:
            return level
    return recurse(0)

def attempt(operation, source, limit):
    sys.setrecursionlimit(limit)
    try:
        return 'ok', operation(source)
    except RecursionError:
        return 'recursion', None
    finally:
        sys.setrecursionlimit(200)

sys.setrecursionlimit(200)
before = boundary()
for depth, limit, expected in ((8, 160, 'ok'), (60, 40, 'recursion')):
    values = [1]
    for _ in range(depth):
        values = [values]
    row = Row(values)
    source = [row, row]
    oracle, _ = attempt(copy.deepcopy, source, limit)
    actual, result = attempt(structural.deep, source, limit)
    assert oracle == actual == expected, (depth, limit, oracle, actual)
    if actual == 'ok':
        assert result[0] is result[1] and result[0] is not row
        original, copied = values, result[0].values
        for _ in range(depth + 1):
            assert type(copied) is list and copied is not original
            original, copied = original[0], copied[0]
        assert copied == original == 1
    assert boundary() == before, 'native state continuation leaked a recursion reservation'
"""
    completed = run_python(code, cwd=tmp_path, env=isolated_environment(), timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("native_backend", [False, True], ids=["python", "native"])
def test_record_recipe_cache_overflow_keeps_aliases_and_selected_dispatch(
    native_backend: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if native_backend:
        native = pytest.importorskip("unstd.clone._clone_native")
        monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
        monkeypatch.setattr(structural, "_native_graph", native.clone_graph)
        monkeypatch.setattr(structural, "_native_walk_graph", native.clone_walk_graph)
        monkeypatch.setattr(structural, "_native_dataclass", native.clone_dataclass)
    else:
        for name in list(vars(structural)):
            if name.startswith("_native_"):
                monkeypatch.setattr(structural, name, None)

    classes = [
        dataclasses.make_dataclass(f"Row{index}", [("values", list[Any])])
        for index in range(40)
    ]
    rows = [cls([index, {"items": [index + 1]}]) for index, cls in enumerate(classes)]
    overflow = rows[32]
    unexpected_hooks: list[object] = []

    class Other:
        def __deepcopy__(self, memo: dict[int, Any]) -> str:
            unexpected_hooks.append(self)
            return "wrong source handler"

    class Key:
        def __hash__(self) -> int:
            return id(overflow)

        def __eq__(self, other: object) -> bool:
            if other == id(overflow):
                overflow.__class__ = Other
            return False

    key = Key()

    class HookBase:
        pass

    class Hook(HookBase):
        # An inherited layout delegates without consuming a native recipe, so
        # the 33rd distinct record reaches the 32-entry per-call cache limit.
        def __deepcopy__(self, memo: dict[Any, Any]) -> None:
            memo[key] = "unused collision"

    source = [
        Hook(),
        *rows,
        rows[0],
        overflow,
        {"row": rows[-1], "values": overflow.values},
    ]
    actual = clone.deep(source)
    copied_rows = actual[1:41]

    assert len(actual) == len(source)
    assert type(overflow) is Other
    assert unexpected_hooks == []
    for index, (original, copied) in enumerate(zip(rows, copied_rows, strict=True)):
        assert type(copied) is (Other if index == 32 else classes[index])
        assert copied is not original
        assert copied.values == original.values
        assert copied.values is not original.values
        assert copied.values[1] is not original.values[1]
        assert copied.values[1]["items"] is not original.values[1]["items"]
    assert len({id(copied.values) for copied in copied_rows}) == 40
    assert actual[-3] is copied_rows[0]
    assert actual[-2] is copied_rows[32]
    assert actual[-1]["row"] is copied_rows[-1]
    assert actual[-1]["values"] is copied_rows[32].values

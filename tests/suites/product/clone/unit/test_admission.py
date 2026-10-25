"""Per-class admission: the walker rebuilds only default-protocol shapes, once each.

The documented rule is that a class is admitted once, and only when it leaves the
default copy protocol alone; every other node is handed to ``copy.deepcopy`` by
itself while its siblings keep walking. A recording ``deepcopy`` shows which
nodes were delegated; the stdlib result on the same value is the value oracle.
"""

from __future__ import annotations

import collections
import copy
import copyreg
import dataclasses
import decimal
import enum
import gc
import sys
from typing import Any

import pytest

from tests.support.environment.backends import require_backend
from unstd import clone
from unstd.clone import structural


_deepcopy = copy.deepcopy


@pytest.fixture
def delegated(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    seen: list[object] = []

    def record(obj: object, memo: Any = None) -> object:
        # The captured stdlib oracle also recursively calls this wrapper. Only
        # direct calls from the structural module are per-node delegation.
        if sys._getframe(1).f_globals.get("__name__") == structural.__name__:
            seen.append(obj)
        return _deepcopy(obj, memo)

    monkeypatch.setattr(structural._stdlib, "deepcopy", record)
    return seen


@dataclasses.dataclass
class _Plain:
    items: list[int]


@dataclasses.dataclass
class _Inherited(_Plain):
    more: dict[str, list[int]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class _Frozen:
    items: list[int]


class _Color(enum.Enum):
    RED = [1]  # noqa: RUF012 — a mutable enum value still makes the member a singleton.


def _admitted() -> list[object]:
    return [_Plain([1]), _Inherited([1], {"k": [2]}), _Frozen([1]), _Color.RED]


@pytest.mark.parametrize("value", _admitted(), ids=lambda v: type(v).__name__)
def test_default_protocol_shapes_are_rebuilt_without_deepcopy(
    value: object, delegated: list[object]
) -> None:
    expected = _deepcopy(value)
    actual = clone.deep({"value": value, "sibling": [1]})["value"]
    assert delegated == []
    assert type(actual) is type(expected)
    assert actual == expected
    if isinstance(value, enum.Enum):
        assert actual is value is expected
    else:
        assert actual is not value
        assert actual.items is not value.items


def _custom_shapes() -> dict[str, tuple[object, list[str]]]:
    calls: list[str] = []

    @dataclasses.dataclass
    class GetState:
        items: list[int]

        def __getstate__(self) -> dict[str, Any]:
            calls.append("getstate")
            return {"items": [*self.items, 0]}

    @dataclasses.dataclass
    class SetState:
        items: list[int]

        def __setstate__(self, state: dict[str, Any]) -> None:
            calls.append("setstate")
            self.__dict__.update(state, restored=True)

    @dataclasses.dataclass
    class Reduce:
        items: list[int]

        def __reduce__(self) -> Any:
            calls.append("reduce")
            return Reduce, ([*self.items, 9],)

    @dataclasses.dataclass
    class ReduceEx:
        items: list[int]

        def __reduce_ex__(self, protocol: Any) -> Any:
            calls.append("reduce_ex")
            return ReduceEx, ([*self.items, 8],)

    @dataclasses.dataclass
    class NewArgsEx:
        items: list[int]

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            calls.append("new")
            return super().__new__(cls)

        def __getnewargs_ex__(self) -> Any:
            calls.append("getnewargs_ex")
            return (), {"items": self.items}

    @dataclasses.dataclass
    class Hooked:
        items: list[int]

        def __deepcopy__(self, memo: Any) -> Any:
            calls.append("deepcopy")
            return Hooked([-1])

    @dataclasses.dataclass(slots=True)
    class Slotted:
        items: list[int]

    class HookedEnum(enum.Enum):
        A = 1

        def __deepcopy__(self, memo: Any) -> Any:
            calls.append("enum")
            return "replaced"

    return {
        "getstate": (GetState([1]), calls),
        "setstate": (SetState([1]), calls),
        "reduce": (Reduce([1]), calls),
        "reduce_ex": (ReduceEx([1]), calls),
        "getnewargs_ex": (NewArgsEx([1]), calls),
        "deepcopy": (Hooked([1]), calls),
        "slots": (Slotted([1]), calls),
        "enum": (HookedEnum.A, calls),
        "namedtuple": (collections.namedtuple("P", "a")([1]), calls),
        "decimal": (decimal.Decimal("1.5"), calls),
        "bytearray": (bytearray(b"ab"), calls),
        "ordered": (collections.OrderedDict(a=[1]), calls),
    }


@pytest.mark.parametrize("shape", list(_custom_shapes()))
def test_custom_protocols_are_delegated_per_node_and_honored(
    shape: str, delegated: list[object]
) -> None:
    value, calls = _custom_shapes()[shape]
    calls.clear()
    expected = _deepcopy(value)
    expected_calls = list(calls)
    calls.clear()
    sibling = [1]
    actual = clone.deep({"value": value, "sibling": sibling})

    assert len(delegated) == 1
    assert delegated[0] is value
    assert calls == expected_calls
    assert type(actual["value"]) is type(expected)
    assert actual["value"] == expected
    if hasattr(expected, "__dict__") and not isinstance(expected, enum.Enum):
        assert vars(actual["value"]) == vars(expected)
    assert actual["sibling"] == sibling
    assert actual["sibling"] is not sibling


def test_copyreg_registration_takes_precedence_over_admission(
    monkeypatch: pytest.MonkeyPatch, delegated: list[object]
) -> None:
    @dataclasses.dataclass
    class Registered:
        items: list[int]

    monkeypatch.setitem(
        copyreg.dispatch_table, Registered, lambda o: (Registered, ([*o.items, 5],))
    )
    source = Registered([1])
    actual = clone.deep([source])
    assert len(delegated) == 1
    assert delegated[0] is source
    assert actual == [Registered([1, 5])] == _deepcopy([source])


def test_each_class_is_admitted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclasses.dataclass
    class Row:
        cells: list[int]

    admissions: list[type] = []
    real = structural._admit

    def counting(cls: type) -> Any:
        admissions.append(cls)
        return real(cls)

    monkeypatch.setattr(structural, "_admit", counting)
    rows = [Row([i]) for i in range(50)]
    assert clone.deep(rows) == rows
    assert clone.deep({"again": rows[0]}) == {"again": rows[0]}
    assert admissions == [Row]


def test_same_named_class_churn_never_reuses_a_dead_admission() -> None:
    # Fresh classes often reuse a collected class's address; admission must not.
    hooks: list[int] = []
    for index in range(24):
        namespace: dict[str, Any] = {}
        if index % 2:

            def hook(self: Any, memo: Any, index: int = index) -> Any:
                hooks.append(index)
                return type(self)([index])

            namespace["__deepcopy__"] = hook
        cls = dataclasses.make_dataclass(
            "Churn", [("items", list)], namespace=namespace
        )
        source = cls([index])
        twin = clone.deep([source])[0]
        assert type(twin) is cls
        assert twin.items == [index]
        assert twin.items is not source.items
        assert hooks.count(index) == index % 2
        del cls, source, twin, namespace
        gc.collect()
    assert hooks == list(range(1, 24, 2))


def test_pydantic_and_msgspec_overrides_are_never_admitted(
    delegated: list[object],
) -> None:
    pydantic = require_backend("pydantic")
    msgspec = require_backend("msgspec")
    calls: list[str] = []

    class Plain(pydantic.BaseModel):
        items: list[int]

    class Hooked(Plain):
        def __deepcopy__(self, memo: Any = None) -> Any:
            calls.append("model")
            return Plain(items=[-1])

    class Struct(msgspec.Struct):
        items: list[int]

    plain_model, plain_struct, hooked = Plain(items=[1]), Struct([1]), Hooked(items=[2])
    actual = clone.deep([plain_model, plain_struct, hooked])
    assert len(delegated) == 1
    assert delegated[0] is hooked
    assert calls == ["model"]
    assert actual[0] == plain_model
    assert actual[0].items is not plain_model.items
    assert actual[1] == plain_struct
    assert actual[1].items is not plain_struct.items
    assert actual[2] == Plain(items=[-1])

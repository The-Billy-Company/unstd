"""Native callback validation preserves ordinary Python callability contracts."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from unstd.clone import structural


native = pytest.importorskip("unstd.clone._clone_native")


@pytest.mark.parametrize(
    "entrypoint", ["clone_walk_graph", "clone_walk_canonical_graph"]
)
@pytest.mark.parametrize("kind", ["function", "instance", "builtin"])
@pytest.mark.usefixtures("native_mixed_graph")
def test_native_graph_accepts_each_callback_shape(entrypoint: str, kind: str) -> None:
    @dataclasses.dataclass
    class Row:
        payload: Any

    opaque = object()
    marker = object()
    source = Row(opaque)
    memo: dict[int, Any] = {}
    events: list[tuple[Any, ...]] = []

    def walk(value: Any, selected_memo: dict[int, Any]) -> Any:
        events.append(("walk", value, selected_memo))
        return marker

    def admit(cls: type) -> tuple[bool, Any]:
        events.append(("admit", cls))
        return True, structural._dataclass

    class Call:
        def __init__(self, function: Any) -> None:
            self.function = function

        def __call__(self, *args: Any) -> Any:
            return self.function(*args)

    walker: Any = walk
    admission: Any = admit
    if kind == "instance":
        walker, admission = Call(walk), Call(admit)
    elif kind == "builtin":
        walker = {opaque: marker}.get
        admission = {Row: (True, structural._dataclass)}.get

    twin = getattr(native, entrypoint)(source, walker, admission, memo)

    assert type(twin) is Row
    assert twin is not source
    assert twin.payload is marker
    assert source.payload is opaque
    assert memo[id(source)] is twin
    if kind != "builtin":
        assert events == [("admit", Row), ("walk", opaque, memo)]


@pytest.mark.parametrize(
    "entrypoint", ["clone_walk_graph", "clone_walk_canonical_graph"]
)
@pytest.mark.parametrize("position", [1, 2])
def test_native_graph_rejects_noncallables_before_copying(
    entrypoint: str, position: int
) -> None:
    memo: dict[int, Any] = {}
    arguments: list[Any] = [[], structural._walk, structural._record_admit, memo]
    arguments[position] = object()

    with pytest.raises(TypeError, match=f"^{entrypoint} requires source,"):
        getattr(native, entrypoint)(*arguments)

    assert memo == {}


@pytest.mark.parametrize(
    "entrypoint", ["clone_walk_graph", "clone_walk_canonical_graph"]
)
@pytest.mark.usefixtures("native_mixed_graph")
def test_native_graph_validation_does_not_resolve_call_descriptors(
    entrypoint: str,
) -> None:
    sentinel = AssertionError("validation resolved __call__")

    class DescriptorCallable:
        @property
        def __call__(self) -> Any:
            raise sentinel

    callback = DescriptorCallable()
    assert callable(callback)
    source: dict[str, Any] = {}
    memo: dict[int, Any] = {}

    twin = getattr(native, entrypoint)(source, callback, callback, memo)

    assert twin == source
    assert twin is not source
    assert memo[id(source)] is twin

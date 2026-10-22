"""Adversarial + parity tests for ``unstd.clone`` — structural deep clone.

The walker is pure stdlib, so the core runs in every environment; the pydantic
and ``msgspec.Struct`` shapes are proven under ``pytest.importorskip``.

Independence is asserted adversarially: we mutate the clone and assert the
original is untouched *and* that the two genuinely diverged — a shallow-copy bug
would fail both halves.
"""

from __future__ import annotations

import collections
import copy as _stdlib_copy
import copyreg
import dataclasses
import datetime as dt
import enum
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from unstd import clone


# ── the surface itself ─────────────────────────────────────────────────────────


def test_reexports_are_the_stdlib_copy_functions() -> None:
    """Test reexports are the stdlib copy functions."""
    assert clone.copy is _stdlib_copy.copy
    assert clone.deepcopy is _stdlib_copy.deepcopy


def test_primitives_round_trip() -> None:
    """Test primitives round trip."""
    for p in (5, "x", 3.14, None, True, b"bytes"):
        assert clone.deep(p) == p


# ── independence: mutating the clone must never touch the original ──────────────


def test_deep_clone_of_nested_dict_is_independent() -> None:
    """Test deep clone of nested dict is independent."""
    original = {"a": {"b": [1, 2, 3]}, "c": [{"d": 4}]}
    cloned = clone.deep(original)

    assert cloned == original
    assert cloned is not original
    assert cloned["a"] is not original["a"]
    assert cloned["a"]["b"] is not original["a"]["b"]

    cloned["a"]["b"].append(999)
    cloned["c"][0]["d"] = 0
    assert original["a"]["b"] == [1, 2, 3]  # original untouched…
    assert original["c"][0]["d"] == 4
    assert cloned != original  # …and the two genuinely diverged


def test_deep_clone_of_nested_list_is_independent() -> None:
    """Test deep clone of nested list is independent."""
    original = [[1], [2, [3]]]
    cloned = clone.deep(original)

    assert cloned == original
    assert cloned is not original
    assert cloned[1] is not original[1]

    cloned[1][1].append(4)
    assert original[1][1] == [3]
    assert cloned != original


def test_tuple_and_set_clone_are_independent() -> None:
    """Test tuple and set clone are independent."""
    original = (1, [2, 3])
    cloned = clone.deep(original)
    assert cloned == original
    assert cloned is not original
    cloned[1].append(4)  # mutate the shared-if-shallow inner list
    assert original[1] == [2, 3]

    s = {1, 2, 3}
    cs = clone.deep(s)
    assert cs == s
    assert cs is not s
    cs.add(4)
    assert s == {1, 2, 3}


# ── parity with copy.deepcopy across mixed structural shapes ───────────────────


def test_deep_matches_copy_deepcopy_semantics() -> None:
    """Test deep matches copy deepcopy semantics."""
    for obj in (
        {"x": [1, {"y": 2}]},
        [1, (2, 3), {4, 5}],
        (1, [2, {"k": "v"}]),
        {"a", "b", "c"},
        frozenset({1, 2}),
    ):
        assert clone.deep(obj) == _stdlib_copy.deepcopy(obj)


def test_plain_data_never_reaches_deepcopy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The walker owns plain data outright — ``deepcopy`` is not consulted at all."""
    from unstd.clone import structural

    def refuse(_obj: object, _memo: object = None) -> object:
        msg = "walked data must not fall back to copy.deepcopy"
        raise AssertionError(msg)

    monkeypatch.setattr(structural._stdlib, "deepcopy", refuse)
    src = {"a": [1, 2.5, None], "b": (3, {4}), "c": frozenset({"x"}), (1, 2): b"k"}
    assert clone.deep(src) == src


@pytest.mark.parametrize("container", [dict, set, frozenset])
def test_hashable_atom_subclasses_retain_custom_deepcopy(container: type) -> None:
    """Key and set fast paths must still honor subclasses' copy protocols."""

    class Number(int):
        def __deepcopy__(self, memo: dict[int, object]) -> Number:
            return Number(self + 10)

    source = container({Number(1): "value", 2: "other"})
    actual = clone.deep(source)

    assert actual == _stdlib_copy.deepcopy(source)
    assert actual is not source
    assert Number(11) in actual


def test_foreign_node_is_deepcopied_alone_and_siblings_still_walk() -> None:
    """A node the walker does not own goes to ``deepcopy`` per node, not per tree."""
    od = collections.OrderedDict(k=[1])
    src = {"od": od, "plain": [2]}
    cloned = clone.deep(src)

    assert type(cloned["od"]) is collections.OrderedDict  # subclass preserved
    assert cloned == src
    assert cloned["od"]["k"] is not od["k"]
    assert cloned["plain"] is not src["plain"]


def test_immutable_leaves_are_shared_like_deepcopy_shares_atoms() -> None:
    """Enum members and datetime values come back as the same immutable objects."""

    class Color(enum.Enum):
        RED = "red"

    when = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
    cloned = clone.deep({"c": Color.RED, "t": when})
    assert cloned["c"] is Color.RED
    assert cloned["t"] is when


class _MutableTimezone(dt.tzinfo):
    def __init__(self) -> None:
        self.hours = [1]

    def utcoffset(self, _value: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(hours=self.hours[-1])

    def dst(self, _value: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(0)


@dataclasses.dataclass
class _TemporalBox:
    value: dt.datetime | dt.time


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("operation", ["deep", "asdict"])
def test_custom_timezone_state_is_independent_in_datetime_and_time(
    kind: type[dt.datetime | dt.time], operation: str
) -> None:
    """An immutable temporal value can still contain a mutable timezone."""
    zone = _MutableTimezone()
    original = (
        dt.datetime(2026, 10, 21, 12, 30, tzinfo=zone, fold=1)
        if kind is dt.datetime
        else dt.time(12, 30, tzinfo=zone, fold=1)
    )
    cloned = (
        clone.deep(original)
        if operation == "deep"
        else clone.asdict(_TemporalBox(original))["value"]
    )
    expected = _stdlib_copy.deepcopy(original)

    assert cloned == expected
    assert cloned.fold == expected.fold == 1
    assert cloned is not original
    assert cloned.tzinfo is not zone
    assert cloned.tzinfo.hours is not zone.hours
    cloned.tzinfo.hours.append(2)
    assert zone.hours == [1]
    assert original.utcoffset() == dt.timedelta(hours=1)
    assert cloned.utcoffset() == dt.timedelta(hours=2)


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
def test_custom_timezone_is_copied_through_every_structural_shortcut(
    kind: type[dt.datetime | dt.time],
) -> None:
    """Container values, keys, sets, and dataclass fields all copy timezone state."""
    zone = _MutableTimezone()
    value = (
        dt.datetime(2026, 10, 21, tzinfo=zone)
        if kind is dt.datetime
        else dt.time(12, tzinfo=zone)
    )
    original = {
        "value": value,
        "list": [value],
        "tuple": (value,),
        "set": {value},
        "frozenset": frozenset({value}),
        "record": _TemporalBox(value),
        value: "key",
    }
    cloned = clone.deep(original)
    values = [
        cloned["value"],
        cloned["list"][0],
        cloned["tuple"][0],
        next(iter(cloned["set"])),
        next(iter(cloned["frozenset"])),
        cloned["record"].value,
        next(key for key in cloned if type(key) is kind),
    ]
    for copied in values:
        assert copied == value
        assert copied is not value
        assert copied.tzinfo is not zone
        assert copied.tzinfo.hours is not zone.hours


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
def test_custom_timezone_deepcopy_protocol_is_preserved(
    kind: type[dt.datetime | dt.time],
) -> None:
    """Temporal reconstruction delegates to a custom timezone's copy protocol."""
    calls = []

    class CustomTimezone(_MutableTimezone):
        def __deepcopy__(self, memo: dict[int, object]) -> dt.timezone:
            calls.append(memo)
            return dt.timezone(dt.timedelta(hours=8))

    zone = CustomTimezone()
    original = (
        dt.datetime(2026, 10, 21, tzinfo=zone)
        if kind is dt.datetime
        else dt.time(12, tzinfo=zone)
    )
    expected = _stdlib_copy.deepcopy(original)
    calls.clear()
    cloned = clone.deep(original)
    assert len(calls) == 1
    assert cloned == expected
    assert cloned.utcoffset() == dt.timedelta(hours=8)


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("zone_kind", ["naive", "utc", "fixed", "zoneinfo"])
def test_safe_temporal_values_keep_the_fast_identity_path(
    kind: type[dt.datetime | dt.time],
    zone_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naive values and exact immutable timezone types never invoke deepcopy."""
    from unstd.clone import structural

    if zone_kind == "zoneinfo":
        try:
            zone = ZoneInfo("UTC")
        except ZoneInfoNotFoundError:
            pytest.skip("IANA timezone data not installed")
    else:
        zone = {
            "naive": None,
            "utc": dt.UTC,
            "fixed": dt.timezone(dt.timedelta(hours=3)),
        }[zone_kind]
    value = (
        dt.datetime(2026, 10, 21, tzinfo=zone)
        if kind is dt.datetime
        else dt.time(12, tzinfo=zone)
    )

    def refuse(_obj: object, _memo: object = None) -> object:
        pytest.fail("safe immutable temporal values must not invoke deepcopy")

    monkeypatch.setattr(structural._stdlib, "deepcopy", refuse)
    assert clone.deep(value) is value
    assert clone.deep([value])[0] is value
    assert clone.deep(_TemporalBox(value)).value is value
    assert clone.asdict(_TemporalBox(value))["value"] is value


def test_shared_reference_is_cloned_twice() -> None:
    """The documented divergence: aliasing is not preserved (deepcopy's memo is not kept)."""
    inner = [1]
    cloned = clone.deep({"a": inner, "b": inner})
    assert cloned == {"a": [1], "b": [1]}
    assert cloned["a"] is not cloned["b"]


# ── objects that require a real deepcopy still work ────────────────────────────


def test_object_with_custom_deepcopy_takes_the_deepcopy_path() -> None:
    """Test object with custom deepcopy takes the deepcopy path."""
    calls: list[int] = []

    class HasCustomDeepcopy:
        def __init__(self, payload: list[int]) -> None:
            self.payload = payload

        def __deepcopy__(self, _memo: dict[int, object]) -> HasCustomDeepcopy:
            calls.append(1)
            return HasCustomDeepcopy(list(self.payload))

    original = HasCustomDeepcopy([1, 2])
    cloned = clone.deep(original)

    assert calls == [1]  # its __deepcopy__ fired — proves the deepcopy path
    assert cloned is not original
    cloned.payload.append(3)
    assert original.payload == [1, 2]


def test_cyclic_graph_clones_via_deepcopy() -> None:
    # A cycle is exactly what copy.deepcopy's memo table exists for; deep() must
    # route it there rather than choking on the structural fast path.
    """Test cyclic graph clones via deepcopy."""
    a: list[object] = [1]
    a.append(a)
    cloned = clone.deep(a)
    assert cloned is not a
    assert cloned[0] == 1
    assert cloned[1] is cloned  # cycle preserved, and self-referential


# ── dataclass clone — type-preserving + independent ────────────────────────────


@dataclasses.dataclass
class _Node:
    label: str
    kids: list[int]


def test_dataclass_clone_is_independent_and_type_preserving() -> None:
    """Test dataclass clone is independent and type preserving."""
    original = _Node(label="root", kids=[1, 2])
    cloned = clone.deep(original)

    assert isinstance(cloned, _Node)
    assert cloned == original
    assert cloned is not original
    assert cloned.kids is not original.kids

    cloned.kids.append(3)
    assert original.kids == [1, 2]


def test_frozen_slotted_dataclass_takes_deepcopy_and_stays_independent() -> None:
    """``__slots__`` changes the reduce state shape, so the walker defers that class."""

    @dataclasses.dataclass(frozen=True, slots=True)
    class Frozen:
        kids: list[int]

    original = Frozen([1])
    cloned = clone.deep(original)
    assert cloned == original
    assert cloned.kids is not original.kids


def test_dataclass_with_custom_deepcopy_keeps_its_protocol() -> None:
    """A dataclass that overrides ``__deepcopy__`` is never rebuilt by the walker."""
    calls: list[int] = []

    @dataclasses.dataclass
    class Custom:
        kids: list[int]

        def __deepcopy__(self, _memo: dict[int, object]) -> Custom:
            calls.append(1)
            return Custom(list(self.kids))

    assert clone.deep({"c": Custom([1])}) == {"c": Custom([1])}
    assert calls == [1]


def test_dataclass_with_pickle_constructor_arguments_keeps_its_protocol() -> None:
    """A custom allocator receives the constructor arguments its reducer supplies."""

    @dataclasses.dataclass
    class WithNewArgs:
        items: list[int]

        def __new__(cls, items: list[int]) -> Self:
            return super().__new__(cls)

        def __getnewargs__(self) -> tuple[list[int]]:
            return (self.items,)

    original = WithNewArgs([1, 2])
    cloned = clone.deep(original)
    assert cloned == _stdlib_copy.deepcopy(original)
    assert cloned.items is not original.items


def test_subclasses_that_compare_equal_keep_their_type() -> None:
    """Regression (1.0.5): an equality guard cannot see ``StrEnum`` → ``str`` or ``defaultdict`` → ``dict``."""

    class Mode(enum.StrEnum):
        FAST = "fast"

    class Level(enum.IntEnum):
        HIGH = 3

    src = {
        "mode": Mode.FAST,
        "level": Level.HIGH,
        "ordered": collections.OrderedDict(a=[1]),
        "grouped": collections.defaultdict(list, a=[1]),
    }
    cloned = clone.deep(src)

    assert cloned == src
    assert cloned["mode"] is Mode.FAST
    assert cloned["level"] is Level.HIGH
    assert type(cloned["ordered"]) is collections.OrderedDict
    assert type(cloned["grouped"]) is collections.defaultdict
    cloned["grouped"]["new"].append(1)  # the factory survived, not just the type name
    assert "new" not in src["grouped"]


def test_non_json_types_survive_unmangled() -> None:
    """No serialization in the loop: tuples, int keys, and sets keep their types."""
    for value in ({"k": (1, 2)}, {1: "a"}, [1, {2, 3}], {(1, "x"): frozenset({2})}):
        cloned = clone.deep(value)
        assert cloned == value == _stdlib_copy.deepcopy(value)
        assert type(cloned) is type(value)


# ── msgspec.Struct and pydantic — proven when the library is installed ─────────


def test_struct_clone_is_independent_and_runs_post_init_like_deepcopy() -> None:
    """Struct rebuilds through its own reducer, so ``__post_init__`` fires as under deepcopy."""
    msgspec = pytest.importorskip("msgspec")
    inits: list[int] = []

    class Point(msgspec.Struct, kw_only=True):
        x: int
        y: list[int]

        def __post_init__(self) -> None:
            inits.append(self.x)

    original = Point(x=1, y=[2, 3])
    cloned = clone.deep(original)
    via_stdlib = _stdlib_copy.deepcopy(original)

    assert isinstance(cloned, Point)
    assert cloned == original == via_stdlib
    assert cloned.y is not original.y
    assert inits == [1, 1, 1]  # construct, clone, deepcopy


def test_model_clone_matches_model_copy_deep() -> None:
    """The walker mirrors ``BaseModel.__deepcopy__``: fields, extras, private attrs, fields-set."""
    pydantic = pytest.importorskip("pydantic")

    class Inner(pydantic.BaseModel):
        tags: list[str]

    class Outer(pydantic.BaseModel, extra="allow"):
        inner: Inner
        scores: dict[str, list[float]] = pydantic.Field(default_factory=dict)
        _cache: list[int] = pydantic.PrivateAttr(default_factory=lambda: [7])

    original = Outer(inner=Inner(tags=["a"]), scores={"k": [0.5]}, note=["x"])
    cloned = clone.deep(original)
    expected = original.model_copy(deep=True)

    assert cloned == expected == original
    assert cloned.model_fields_set == original.model_fields_set
    assert cloned.model_extra == original.model_extra
    assert cloned._cache == [7]
    assert cloned.inner is not original.inner
    assert cloned.inner.tags is not original.inner.tags
    assert cloned.model_extra is not None
    assert original.model_extra is not None
    assert cloned.model_extra["note"] is not original.model_extra["note"]
    assert cloned._cache is not original._cache


def test_model_private_attributes_filter_undefined_and_clone_values() -> None:
    """Private attributes are filtered and copied in the same way as pydantic."""
    pydantic = pytest.importorskip("pydantic")
    pydantic_core = pytest.importorskip("pydantic_core")

    class Model(pydantic.BaseModel):
        _ready: list[int] = pydantic.PrivateAttr(default_factory=lambda: [1])
        _unset: object = pydantic.PrivateAttr()

    original = Model()
    original.__pydantic_private__["_unset"] = pydantic_core.PydanticUndefined
    cloned = clone.deep(original)
    expected = original.model_copy(deep=True)
    assert cloned.__pydantic_private__ == expected.__pydantic_private__
    assert "_unset" not in cloned.__pydantic_private__
    cloned._ready.append(2)
    assert original._ready == [1]


@pytest.mark.parametrize("custom_mapping", [False, True])
def test_model_private_copy_hooks_see_a_snapshot(custom_mapping: bool) -> None:
    """A private value's copy hook can mutate the original private attribute mapping."""
    pydantic = pytest.importorskip("pydantic")

    class Model(pydantic.BaseModel):
        _cache: object = pydantic.PrivateAttr()

    original = Model()
    if custom_mapping:

        class PrivateMap(dict[str, object]):
            def copy(self) -> dict[str, object]:
                pytest.fail("a custom private mapping's copy method must not be called")

        original.__pydantic_private__ = PrivateMap()

    class ChangePrivate:
        def __deepcopy__(self, _memo: dict[int, object]) -> str:
            original.__pydantic_private__["added"] = [1]
            return "copied"

    original.__pydantic_private__["_cache"] = ChangePrivate()
    cloned = clone.deep(original)
    assert cloned.__pydantic_private__ == {"_cache": "copied"}
    assert original.__pydantic_private__["added"] == [1]


@pytest.mark.parametrize("protocol", ["reduce", "reduce_ex", "copyreg"])
def test_msgspec_custom_reducers_keep_their_copy_protocol(
    protocol: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered reducers and both reducer methods take precedence over the fast path."""
    msgspec = pytest.importorskip("msgspec")

    class Point(msgspec.Struct):
        x: int

    def reduce(_obj: object) -> tuple[type[str], tuple[str]]:
        return str, ("custom",)

    if protocol == "copyreg":
        monkeypatch.setitem(copyreg.dispatch_table, Point, reduce)
    elif protocol == "reduce":
        monkeypatch.setattr(Point, "__reduce__", reduce)
    else:

        def reduce_ex(obj: object, _protocol: int) -> tuple[type[str], tuple[str]]:
            return reduce(obj)

        monkeypatch.setattr(Point, "__reduce_ex__", reduce_ex)

    original = Point(1)
    assert clone.deep(original) == _stdlib_copy.deepcopy(original) == "custom"


# ── asdict — dataclasses.asdict's answer, walked ───────────────────────────────


@dataclasses.dataclass
class _Leaf:
    pair: tuple[int, list[int]]
    bag: set[int]
    when: dt.datetime


@dataclasses.dataclass
class _Tree:
    leaf: _Leaf
    leaves: list[_Leaf]
    index: dict[object, object]
    extra: object = None


def _tree() -> _Tree:
    when = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
    return _Tree(
        leaf=_Leaf((1, [2]), {3}, when),
        leaves=[_Leaf((4, []), set(), when)],
        index={"k": _Leaf((5, [6]), {7}, when), (1, 2): [3]},
    )


def test_asdict_matches_stdlib() -> None:
    """Nested dataclasses in fields, lists, and dict values convert exactly as the stdlib does."""
    tree = _tree()
    assert clone.asdict(tree) == dataclasses.asdict(tree)


def test_asdict_result_is_independent() -> None:
    """Every mutable leaf in the result is a copy, as ``asdict`` deep-copies leaves."""
    tree = _tree()
    out = clone.asdict(tree)
    out["leaf"]["pair"][1].append(99)
    out["leaf"]["bag"].add(99)
    assert tree.leaf.pair[1] == [2]
    assert tree.leaf.bag == {3}


def test_asdict_defers_containers_it_does_not_own() -> None:
    """A namedtuple or dict subclass holding a dataclass gets the stdlib's own conversion."""
    Pair = collections.namedtuple("Pair", "left right")
    tree = _tree()
    tree.extra = Pair(_tree().leaf, collections.OrderedDict(x=_tree().leaf))
    assert clone.asdict(tree) == dataclasses.asdict(tree)


def test_asdict_keeps_stdlib_errors_and_factory() -> None:
    """Non-dataclass arguments raise the stdlib's TypeError; a custom factory is honored."""
    with pytest.raises(TypeError):
        clone.asdict({"not": "a dataclass"})
    with pytest.raises(TypeError):
        clone.asdict(_Tree)
    tree = _tree()
    factory = collections.OrderedDict
    assert clone.asdict(tree, dict_factory=factory) == dataclasses.asdict(
        tree, dict_factory=factory
    )


def test_asdict_keeps_dataclass_predicate_metaclass_errors() -> None:
    """Inlined dataclass detection retains the original attribute access and exception."""
    failure = RuntimeError("dataclass field inspection failed")

    class Meta(type):
        def __getattribute__(cls, name: str) -> object:
            if name == "__dataclass_fields__":
                raise failure
            return super().__getattribute__(name)

    class Record(metaclass=Meta):
        pass

    with pytest.raises(RuntimeError) as expected:
        dataclasses.is_dataclass(Record)
    with pytest.raises(RuntimeError) as observed:
        clone.asdict(Record)
    assert observed.value is expected.value is failure

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
import dataclasses
import datetime as dt
import enum

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

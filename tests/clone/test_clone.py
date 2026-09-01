"""Adversarial + parity tests for ``unstd.clone`` — structural deep clone.

The base dev env installs no ``clone`` extra, so ``msgspec`` is absent and every
call here exercises the ``copy.deepcopy`` fallback — these tests pin that the
fallback is a faithful, fully *independent* deep clone. The msgspec fast path is
proven under ``pytest.importorskip("msgspec")`` (skipped without the extra).

Independence is asserted adversarially: we mutate the clone and assert the
original is untouched *and* that the two genuinely diverged — a shallow-copy bug
would fail both halves.
"""

from __future__ import annotations

import copy as _stdlib_copy
import dataclasses
import importlib.util

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


def test_fallback_is_the_active_path_in_the_base_env() -> None:
    # Documents the doctrine: with no `clone` extra installed, deep() runs the
    # copy.deepcopy fallback. If the extra IS present, the fast path is proven
    # by the msgspec tests below instead.
    """Test fallback is the active path in the base env."""
    if importlib.util.find_spec("msgspec") is not None:
        pytest.skip("clone extra installed — the msgspec fast path is active")

    from unstd.clone import structural

    assert structural._HAVE_MSGSPEC is False
    src = {"a": [1, 2], "b": (3, {4})}
    assert clone.deep(src) == _stdlib_copy.deepcopy(src)


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


# ── dataclass clone — type-preserving + independent on BOTH paths ──────────────


@dataclasses.dataclass
class _Node:
    label: str
    kids: list[int]


def test_dataclass_clone_is_independent_and_type_preserving() -> None:
    # Holds on the deepcopy fallback (here) and on the msgspec fast path (extra).
    """Test dataclass clone is independent and type preserving."""
    original = _Node(label="root", kids=[1, 2])
    cloned = clone.deep(original)

    assert isinstance(cloned, _Node)
    assert cloned == original
    assert cloned is not original
    assert cloned.kids is not original.kids

    cloned.kids.append(3)
    assert original.kids == [1, 2]


# ── msgspec fast path — skipped per-test when the `clone` extra is not installed ─


def test_fast_path_struct_clone_is_independent_and_type_preserving() -> None:
    """Test fast path struct clone is independent and type preserving."""
    msgspec = pytest.importorskip("msgspec")

    class _Point(msgspec.Struct):
        x: int
        y: list[int]

    original = _Point(x=1, y=[2, 3])
    cloned = clone.deep(original)

    assert isinstance(cloned, _Point)
    assert cloned == original
    assert cloned is not original
    assert cloned.y is not original.y

    cloned.y.append(4)
    assert original.y == [2, 3]


def test_fast_path_engages_for_structural_data_when_extra_present() -> None:
    """Test fast path engages for structural data when extra present."""
    pytest.importorskip("msgspec")
    from unstd.clone import structural

    assert structural._HAVE_MSGSPEC is True
    # A pure-JSON dict round-trips through msgspec and stays fully independent
    # (decoding from bytes cannot share a reference with the original).
    original = {"a": [1, 2], "b": {"c": 3}}
    cloned = clone.deep(original)
    assert cloned == original
    assert cloned["a"] is not original["a"]
    cloned["a"].append(9)
    assert original["a"] == [1, 2]


def test_fast_path_eq_guard_keeps_lossy_values_faithful() -> None:
    # With msgspec present, a value the JSON round-trip would silently mangle
    # (a tuple in an Any slot -> list; an int dict key -> str) must NOT come back
    # corrupted: the eq-guard rejects the lossy round-trip and returns a faithful
    # copy.deepcopy instead. This is the adversarial guard, proven only with the
    # fast path active.
    """Test fast path eq guard keeps lossy values faithful."""
    pytest.importorskip("msgspec")
    for lossy in ({"k": (1, 2)}, {1: "a"}, [1, {2, 3}]):
        cloned = clone.deep(lossy)
        assert cloned == lossy  # faithful — not mangled to lists/str-keys
        assert cloned == _stdlib_copy.deepcopy(lossy)

"""Adversarial + property-style tests for ``unstd.containers``.

Two contracts are pinned here:

1. **The guarded-optional packaging.** Importing the package never crashes, but
   *constructing* a backend-backed type without the ``containers`` extra raises a
   clear, actionable ImportError naming the extra. This dev env has no extra
   installed, so the ImportError path is exercised for real (at minimum via the
   backend that is absent); the message contract is also asserted directly
   through :func:`unstd._extra.missing`, independent of which backends
   happen to resolve.

2. **The surfaces themselves, where the backend is present.** Sorted-collection
   ordering invariants and HAMT persistence/structural-sharing semantics are
   proven with randomized, property-style assertions and skipped when the backend
   is unavailable. Assertions are never weakened to accommodate a missing backend;
   they are skipped.

The randomized inputs are driven by a tiny in-file deterministic PRNG
(:class:`_Seq`, a SplitMix64 stream — Steele, Lea & Flood, "Fast Splittable
Pseudorandom Number Generators", OOPSLA 2014) rather than the stdlib ``random``
module: it is hermetic and byte-reproducible (the right property for a regression
corpus), and needs no ``hypothesis`` dependency, which ``unstd``'s pure-stdlib dev
closure does not carry.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING

import pytest

from unstd._extra import missing
from unstd.containers import Map, SortedDict, SortedList, SortedSet
from unstd.containers.persistent import HAVE_IMMUTABLES
from unstd.containers.sorted import HAVE_SORTEDCONTAINERS


if TYPE_CHECKING:
    from collections.abc import Callable


requires_sorted = pytest.mark.skipif(
    not HAVE_SORTEDCONTAINERS,
    reason="containers extra (sortedcontainers) not installed",
)
requires_immutables = pytest.mark.skipif(
    not HAVE_IMMUTABLES, reason="containers extra (immutables) not installed"
)

_U64 = (1 << 64) - 1


class _Seq:
    """Deterministic SplitMix64 stream — reproducible property-test inputs with a ``random.Random``-shaped surface, without the stdlib ``random`` module."""

    __slots__ = ("_s",)

    def __init__(self, seed: int) -> None:
        self._s = seed & _U64

    def _next(self) -> int:
        self._s = (self._s + 0x9E3779B97F4A7C15) & _U64
        z = self._s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _U64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _U64
        return z ^ (z >> 31)

    def randint(self, lo: int, hi: int) -> int:
        return lo + self._next() % (hi - lo + 1)

    def choice[T](self, seq: list[T]) -> T:
        return seq[self._next() % len(seq)]

    def unit(self) -> float:
        return (self._next() >> 11) / (1 << 53)


# ── packaging contract: import safe, construction guarded ──────────────────────


def test_import_exposes_the_full_public_surface() -> None:
    """Test import exposes the full public surface."""
    import unstd.containers as containers

    assert set(containers.__all__) == {"Map", "SortedDict", "SortedList", "SortedSet"}
    for name in containers.__all__:
        assert getattr(containers, name) is not None


def test_missing_placeholder_raises_a_clear_actionable_importerror() -> None:
    # Backend-independent: the error contract every guarded name shares.
    """Test missing placeholder raises a clear actionable importerror."""
    placeholder = missing("containers.Widget", "containers")
    with pytest.raises(ImportError) as exc:
        placeholder(1, 2, key="v")
    msg = str(exc.value)
    assert "unstd.containers.Widget" in msg  # names the type…
    assert "requires the 'containers' extra" in msg  # …the extra…
    assert "pip install 'unstd[containers]'" in msg  # …and the fix.


_backend_present = "backend present — covered by the real-behavior tests"


@pytest.mark.parametrize(
    ("name", "ctor"),
    [
        pytest.param(
            "SortedDict",
            SortedDict,
            marks=pytest.mark.skipif(HAVE_SORTEDCONTAINERS, reason=_backend_present),
        ),
        pytest.param(
            "SortedList",
            SortedList,
            marks=pytest.mark.skipif(HAVE_SORTEDCONTAINERS, reason=_backend_present),
        ),
        pytest.param(
            "SortedSet",
            SortedSet,
            marks=pytest.mark.skipif(HAVE_SORTEDCONTAINERS, reason=_backend_present),
        ),
        pytest.param(
            "Map",
            Map,
            marks=pytest.mark.skipif(HAVE_IMMUTABLES, reason=_backend_present),
        ),
    ],
)
def test_construction_without_backend_raises(
    name: str, ctor: Callable[[], object]
) -> None:
    """Test construction without backend raises."""
    with pytest.raises(ImportError) as exc:
        ctor()
    msg = str(exc.value)
    assert name in msg
    assert "pip install 'unstd[containers]'" in msg


# ── sorted collections: ordering invariants (property-style) ───────────────────


@requires_sorted
def test_sortedlist_stays_sorted_through_inserts_and_matches_bisect() -> None:
    """Test sortedlist stays sorted through inserts and matches bisect."""
    rng = _Seq(1729)
    for _ in range(200):
        data = [rng.randint(-50, 50) for _ in range(rng.randint(0, 40))]
        sl = SortedList()
        model: list[int] = []
        for x in data:
            sl.add(x)
            bisect.insort(model, x)
            assert list(sl) == model  # invariant holds after every insert
        # non-decreasing, and equal to the fully-sorted input
        assert list(sl) == sorted(data)
        assert all(sl[i] <= sl[i + 1] for i in range(len(sl) - 1))
        if data:
            probe = rng.choice(data)
            assert sl.bisect_left(probe) == bisect.bisect_left(model, probe)
            assert sl.index(probe) == model.index(probe)


@requires_sorted
def test_sortedlist_removal_preserves_order() -> None:
    """Test sortedlist removal preserves order."""
    rng = _Seq(4)
    data = [rng.randint(0, 20) for _ in range(60)]
    sl = SortedList(data)
    model = sorted(data)
    while model:
        victim = rng.choice(model)
        sl.remove(victim)
        model.remove(victim)
        assert list(sl) == model
    assert list(sl) == []


@requires_sorted
def test_sorteddict_iterates_keys_in_sorted_order() -> None:
    """Test sorteddict iterates keys in sorted order."""
    rng = _Seq(99)
    for _ in range(100):
        pairs = {rng.randint(-30, 30): rng.unit() for _ in range(rng.randint(0, 25))}
        sd = SortedDict(pairs)
        assert list(sd.keys()) == sorted(pairs)
        assert dict(sd) == pairs  # value fidelity — a thin, faithful wrapper
        if pairs:
            assert sd.peekitem(0)[0] == min(pairs)
            assert sd.peekitem(-1)[0] == max(pairs)


@requires_sorted
def test_sortedset_dedups_orders_and_is_indexable() -> None:
    """Test sortedset dedups orders and is indexable."""
    rng = _Seq(2024)
    for _ in range(100):
        data = [rng.randint(0, 15) for _ in range(rng.randint(0, 30))]
        ss = SortedSet(data)
        assert list(ss) == sorted(set(data))  # set semantics + sorted iteration
        if ss:
            assert ss[0] == min(data)
            assert ss[-1] == max(data)
        other = SortedSet(rng.randint(0, 15) for _ in range(10))
        assert set(ss & other) == (set(data) & set(other))
        assert set(ss | other) == (set(data) | set(other))


# ── Map: persistence + structural-sharing semantics (property-style) ───────────


@requires_immutables
def test_map_set_is_persistent_original_untouched() -> None:
    """Test map set is persistent original untouched."""
    rng = _Seq(7)
    for _ in range(200):
        base = {f"k{i}": rng.randint(0, 1000) for i in range(rng.randint(0, 20))}
        m1 = Map(base)
        key, val = f"new{rng.randint(0, 5)}", rng.randint(0, 1000)
        had, prior = key in base, base.get(key)
        m2 = m1.set(key, val)
        # a NEW value, not a mutation of m1
        assert m1 is not m2
        assert m2[key] == val
        # m1 is unchanged by the derivation
        assert (key in m1) == had
        if had:
            assert m1[key] == prior
        assert dict(m1) == base


@requires_immutables
def test_map_delete_returns_new_map_leaving_original_intact() -> None:
    """Test map delete returns new map leaving original intact."""
    rng = _Seq(11)
    base = {f"k{i}": i for i in range(30)}
    m1 = Map(base)
    victim = f"k{rng.randint(0, 29)}"
    m2 = m1.delete(victim)
    assert victim in m1  # original retains the key…
    assert m1[victim] == base[victim]  # …with its value
    assert victim not in m2
    assert dict(m2) == {k: v for k, v in base.items() if k != victim}


@requires_immutables
def test_map_value_equality_is_order_independent_and_roundtrips() -> None:
    """Test map value equality is order independent and roundtrips."""
    assert Map(a=1, b=2, c=3) == Map(c=3, b=2, a=1)
    assert Map(a=1) != Map(a=2)
    rng = _Seq(5)
    for _ in range(100):
        d = {f"k{i}": rng.randint(0, 50) for i in range(rng.randint(0, 25))}
        m = Map(d)
        assert dict(m) == d
        assert len(m) == len(d)
        assert m.get("absent-key", "default") == "default"
        for k, v in d.items():
            assert m[k] == v


@requires_immutables
def test_map_structural_sharing_across_a_long_derivation_chain() -> None:
    # A chain of N derivations must not disturb any earlier version — the
    # persistence guarantee that makes O(log n) snapshots safe.
    """Test map structural sharing across a long derivation chain."""
    versions = [Map()]
    for i in range(500):
        versions.append(versions[-1].set(f"k{i}", i))
    for i, m in enumerate(versions):
        assert len(m) == i
        assert all(m[f"k{j}"] == j for j in range(i))

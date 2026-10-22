"""Generated laws: every combinator against a plain-loop reference specification.

The references below are written from the documented contracts with ordinary
loops and lists. Three generated regimes share them: finite inputs, sources that
raise at item N (outputs stop at exactly the reference prefix, then the source's
own exception propagates), and counted sources pulled k times (each combinator
reads exactly what its next output logically requires).
"""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.iters import (
    chunked,
    first,
    flatten,
    ilen,
    last,
    nth,
    partition,
    runs,
    take,
    unique_everseen,
    unique_justseen,
    windowed,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


def ref_chunks(xs: list[int], n: int) -> list[tuple[int, ...]]:
    out, current = [], []
    for x in xs:
        current.append(x)
        if len(current) == n:
            out.append(tuple(current))
            current = []
    if current:
        out.append(tuple(current))
    return out


def ref_windows(xs: list[int], n: int, step: int) -> list[tuple[int, ...]]:
    if n == 0:
        return [()]
    out, start = [], 0
    while start + n <= len(xs):
        out.append(tuple(xs[start : start + n]))
        start += step
    return out


def ref_runs(
    xs: list[object], key: Callable[[object], object]
) -> list[tuple[object, tuple[object, ...]]]:
    out: list[tuple[object, list[object]]] = []
    for x in xs:
        k = key(x)
        if out and out[-1][0] == k:
            out[-1][1].append(x)
        else:
            out.append((k, [x]))
    return [(k, tuple(run)) for k, run in out]


def ref_everseen(xs: list[object]) -> list[object]:
    out: list[object] = []
    for x in xs:
        if all(x != kept for kept in out):
            out.append(x)
    return out


class RefusedError(Exception):
    pass


class Counted:
    def __init__(self, items: Iterable[object]) -> None:
        self._it, self.pulled = iter(items), 0

    def __iter__(self) -> Counted:
        return self

    def __next__(self) -> object:
        value = next(self._it)
        self.pulled += 1
        return value


def failing_after(items: list[object], error: RefusedError) -> Iterator[object]:
    yield from items
    raise error


def drain(produced: Iterator[object]) -> tuple[list[object], BaseException | None]:
    out: list[object] = []
    try:
        out.extend(produced)
    except RefusedError as error:
        return out, error
    return out, None


SMALL = st.integers(-3, 3)
MIXED = st.one_of(SMALL, st.lists(SMALL, max_size=2), st.frozensets(SMALL, max_size=2))


# ── finite inputs ──────────────────────────────────────────────────────────────


@given(xs=st.lists(SMALL, max_size=30), n=st.integers(1, 8))
def test_chunks_concatenate_back_to_the_input_with_only_the_last_short(
    xs: list[int], n: int
) -> None:
    got = list(chunked(iter(xs), n))
    assert got == ref_chunks(xs, n)
    assert [x for chunk in got for x in chunk] == xs
    assert all(len(chunk) == n for chunk in got[:-1])


@given(xs=st.lists(SMALL, max_size=30), n=st.integers(0, 8), step=st.integers(1, 9))
def test_windows_are_the_complete_slices_at_each_stride(
    xs: list[int], n: int, step: int
) -> None:
    got = list(windowed(iter(xs), n, step))
    assert got == ref_windows(xs, n, step)
    if n and step == 1:
        assert len(got) == max(0, len(xs) - n + 1)


@given(xs=st.lists(MIXED, max_size=30))
def test_runs_and_justseen_group_mixed_hashability_by_equality(
    xs: list[object],
) -> None:
    expected = ref_runs(xs, lambda x: x)
    assert list(runs(iter(xs), lambda x: x)) == expected
    heads = list(unique_justseen(iter(xs)))
    assert heads == [run[0] for _, run in expected]
    assert all(h is run[0] for h, (_, run) in zip(heads, expected, strict=True))
    by_kind = ref_runs(xs, lambda x: type(x).__name__)
    assert list(runs(xs, lambda x: type(x).__name__)) == by_kind
    assert list(unique_justseen(xs, key=lambda x: type(x).__name__)) == [
        run[0] for _, run in by_kind
    ]


@given(xs=st.lists(SMALL, max_size=30))
def test_partition_is_the_stable_split_of_the_input(xs: list[int]) -> None:
    falsy, truthy = partition(iter(xs), lambda v: v > 0)
    assert list(truthy) == [v for v in xs if v > 0]
    assert list(falsy) == [v for v in xs if not v > 0]


@given(nested=st.lists(st.lists(MIXED, max_size=4), max_size=8))
def test_flatten_removes_exactly_one_level(nested: list[list[object]]) -> None:
    expected = []
    for inner in nested:
        expected.extend(inner)
    assert list(flatten(iter(nested))) == expected


@given(xs=st.lists(SMALL, max_size=20), k=st.integers(0, 25))
def test_selectors_match_list_indexing(xs: list[int], k: int) -> None:
    assert take(iter(xs), k) == xs[:k]
    assert nth(iter(xs), k, None) == (xs[k] if k < len(xs) else None)
    assert first(iter(xs), None) == (xs[0] if xs else None)
    assert last(iter(xs), None) == (xs[-1] if xs else None)
    assert ilen(iter(xs)) == len(xs)


# ── sources that raise at item N ───────────────────────────────────────────────


@given(xs=st.lists(SMALL, max_size=20), n=st.integers(1, 6), step=st.integers(1, 6))
def test_failing_source_yields_the_reference_prefix_then_its_own_exception(
    xs: list[int], n: int, step: int
) -> None:
    error = RefusedError()

    def source() -> Iterator[object]:
        return failing_after(list(xs), error)

    complete = xs[: len(xs) // n * n]
    cases: list[tuple[Iterator[object], list[object]]] = [
        (chunked(source(), n), ref_chunks(complete, n)),
        (chunked(source(), n, strict=True), ref_chunks(complete, n)),
        (windowed(source(), n, step), ref_windows(xs, n, step)),
        (runs(source(), lambda x: x), ref_runs(xs, lambda x: x)[:-1]),
        (unique_justseen(source()), [run[0] for _, run in ref_runs(xs, lambda x: x)]),
        (unique_everseen(source()), ref_everseen(xs)),
        (flatten([x] for x in source()), list(xs)),
        (partition(source(), lambda v: v > 0)[1], [v for v in xs if v > 0]),
    ]
    for produced, expected in cases:
        assert drain(produced) == (expected, error)
    for select in (
        ilen,
        last,
        lambda it: take(it, len(xs) + 1),
        lambda it: nth(it, len(xs), None),
    ):
        with pytest.raises(RefusedError) as raised:
            select(source())
        assert raised.value is error
    if xs:
        assert first(source()) == xs[0]
        assert take(source(), len(xs)) == xs
        assert nth(source(), len(xs) - 1) == xs[-1]


def test_zero_width_windows_never_reach_a_failing_source() -> None:
    error = RefusedError()
    assert drain(windowed(failing_after([], error), 0)) == ([()], None)


@given(xs=st.lists(SMALL, max_size=20), schedule=st.lists(st.booleans(), max_size=60))
def test_partition_side_failure_surfaces_on_whichever_side_reaches_it(
    xs: list[int], schedule: list[bool]
) -> None:
    error = RefusedError()
    falsy, truthy = partition(failing_after(list(xs), error), lambda v: v > 0)
    sides = {False: (falsy, []), True: (truthy, [])}
    for side in schedule:
        produced, out = sides[side]
        try:
            value = next(produced)
        except RefusedError as raised:
            assert raised is error  # noqa: PT017 — scheduled pulls may yield or raise.
            assert out == [v for v in xs if (v > 0) is side]
            return
        out.append(value)
    for side, (_, out) in sides.items():
        expected = [v for v in xs if (v > 0) is side]
        assert out == expected[: len(out)]


# ── exact consumption ──────────────────────────────────────────────────────────


@given(
    xs=st.lists(SMALL, max_size=30),
    n=st.integers(1, 6),
    step=st.integers(1, 6),
    k=st.integers(0, 12),
)
def test_each_output_reads_exactly_what_it_requires(
    xs: list[int], n: int, step: int, k: int
) -> None:
    def pulled(make: Callable[[Counted], Iterator[object]], outputs: int) -> int:
        counted = Counted(xs)
        produced = make(counted)
        assert counted.pulled == 0
        list(islice(produced, outputs))
        return counted.pulled

    assert pulled(lambda c: chunked(c, n), k) == min(k * n, len(xs))
    if 0 < k <= len(ref_windows(xs, n, step)):
        assert pulled(lambda c: windowed(c, n, step), k) == n + (k - 1) * step
    assert pulled(lambda c: windowed(c, n, step), 0) == 0

    # The k-th distinct/run head is read at its own position and not beyond.
    firsts = [i for i, x in enumerate(xs) if x not in xs[:i]]
    heads = [i for i, x in enumerate(xs) if i == 0 or xs[i - 1] != x]
    for positions, make in ((firsts, unique_everseen), (heads, unique_justseen)):
        expected = (
            positions[k - 1] + 1 if 0 < k <= len(positions) else (len(xs) if k else 0)
        )
        assert pulled(make, k) == expected
    # A run is complete only once the following item (or the end) is seen.
    ends = [*heads[1:], len(xs)]
    expected_runs = (
        min(ends[k - 1] + 1, len(xs)) if 0 < k <= len(ends) else (len(xs) if k else 0)
    )
    assert pulled(lambda c: runs(c, lambda x: x), k) == expected_runs

    positives = [i for i, x in enumerate(xs) if x > 0]
    expected_side = (
        positives[k - 1] + 1 if 0 < k <= len(positives) else (len(xs) if k else 0)
    )
    assert pulled(lambda c: partition(c, lambda v: v > 0)[1], k) == expected_side


@given(xs=st.lists(SMALL, max_size=30), k=st.integers(0, 35))
def test_selectors_read_exactly_their_prefix(xs: list[int], k: int) -> None:
    for select, expected in (
        (lambda c: take(c, k), min(k, len(xs))),
        (lambda c: nth(c, k, None), min(k + 1, len(xs))),
        (lambda c: first(c, None), min(1, len(xs))),
        (ilen, len(xs)),
        (lambda c: last(c, None), len(xs)),
    ):
        counted = Counted(xs)
        select(counted)
        assert counted.pulled == expected


@given(xs=st.lists(SMALL, max_size=30), schedule=st.lists(st.booleans(), max_size=80))
def test_partition_any_pull_schedule_calls_pred_once_in_source_order(
    xs: list[int], schedule: list[bool]
) -> None:
    calls: list[int] = []

    def pred(v: int) -> bool:
        calls.append(v)
        return v > 0

    counted = Counted(xs)
    falsy, truthy = partition(counted, pred)
    out: dict[bool, list[int]] = {False: [], True: []}
    for side in schedule:
        value = next(truthy if side else falsy, None)
        if value is not None:
            out[side].append(value)
    out[False] += falsy
    out[True] += truthy
    assert out == {False: [v for v in xs if v <= 0], True: [v for v in xs if v > 0]}
    assert calls == xs
    assert counted.pulled == len(xs)
